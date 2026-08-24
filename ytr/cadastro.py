"""Cadastro de canal a partir de mensagens do Discord (Fase 4, D3 do plano).

Fluxo por mensagem, no canal de entrada: extrair URLs → classificar em canal, vídeo ou
nenhum (`canal.classificar`) → agir. Só link de **canal** cadastra; link de **vídeo**
grava sinal fraco de gosto em `sinais.jsonl`. O resto é ignorado em silêncio — o canal
de entrada pode ser compartilhado com o `discord-link-brain` e carrega link de tudo.

**Por que um cursor, e não `ja_marcada`.** O vizinho relê a mesma janela de mensagens a
cada sync e usa a reação do próprio bot para saber o que já processou. Aqui cada
mensagem recebe um desfecho definitivo (registrada, repetida, sinalizada ou descartada)
na primeira vez que é vista, então um cursor que avança basta — evita uma segunda
chamada de rede (`GET .../reactions`) por mensagem, a cada ciclo, para sempre.

**Por que a resolução falha rápido, e não entre ciclos.** Uma URL de canal que não
resolve tenta de novo dentro da mesma passada (rede instável se recupera em segundos) e
não fica pendente esperando o próximo ciclo — isso exigiria um segundo arquivo de estado
só para "mensagens ainda não resolvidas", pelo preço de uma diferença que só importa
para um handle que nunca vai existir.

**Sinal do vídeo, hoje sem consumidor.** `gosto.carregar()` (Fase 6) só soma sinal de
reação (`👍`/`👎`) num digest; um `{"tipo": "postado", ...}" ainda não afeta o
ranqueamento. Fica gravado porque D3 pede o registro, e o consumo é trabalho de Fase 6/7
revisitada, não desta.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import ledger
from .canal import Alvo, CanalError, Canais, classificar, handle_por_oembed, resolver
from .config import Config
from .discord_client import DiscordClient, DiscordError, snowflake_for
from .rede import Cliente, RedeError
from .state import Estado, escrever_atomico

URL = re.compile(r"https?://\S+")

# Tentativas **dentro da mesma passada**, não entre ciclos — ver docstring do módulo.
TENTATIVAS_RESOLUCAO = 3


@dataclass
class RelatorioDeCadastro:
    mensagens: int = 0
    canais_novos: int = 0
    canais_repetidos: int = 0
    videos_sinalizados: int = 0
    falhas: int = 0
    linhas: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


def _caminho_cursor(state_dir: Path) -> Path:
    return Path(state_dir) / "cadastro.json"


def _ler_cursor(state_dir: Path) -> str:
    caminho = _caminho_cursor(state_dir)
    if not caminho.is_file():
        return ""
    try:
        return json.loads(caminho.read_text(encoding="utf-8")).get("cursor", "")
    except (json.JSONDecodeError, OSError):
        return ""


def _salvar_cursor(state_dir: Path, cursor: str) -> None:
    escrever_atomico(
        _caminho_cursor(state_dir), json.dumps({"cursor": cursor}, ensure_ascii=False) + "\n"
    )


def _extrair_alvos(conteudo: str) -> list[Alvo]:
    alvos = [classificar(u) for u in URL.findall(conteudo or "")]
    return [a for a in alvos if a.tipo != "nenhum"]


def _autorizado(cfg: Config, autor_id: str) -> bool:
    return not cfg.donos or autor_id in cfg.donos


def _resolver_com_tentativas(alvo: Alvo, cliente: Cliente):
    """(resolucao | None, erro). `TENTATIVAS_RESOLUCAO` chances antes de desistir."""
    ultimo_erro = ""
    for _ in range(TENTATIVAS_RESOLUCAO):
        try:
            return resolver(alvo, cliente), ""
        except (CanalError, RedeError) as erro:
            ultimo_erro = str(erro)
    return None, ultimo_erro


def _registrar_canal(canais: Canais, estado: Estado, cfg: Config, achado, alvo: Alvo, autor_id: str):
    """Mesma sequência de `cmd_resolver --salvar`: cadastra e semeia sem avisar."""
    canal = canais.adicionar(
        achado.channel_id,
        handle=achado.handle or alvo.handle,
        nome=achado.nome,
        url_original=alvo.url,
        cadastrado_por=autor_id,
    )
    canais.salvar()

    atual = estado.carregar(str(achado.channel_id))
    for video_id in achado.videos_atuais:
        atual.lembrar(video_id, cfg.lembrar_ids)
    atual.semeado = True
    estado.salvar(atual)
    return canal


def _sinalizar_video(cfg: Config, cliente: Cliente, alvo: Alvo, autor_id: str, message_id: str) -> None:
    handle = handle_por_oembed(alvo.url, cliente)
    ledger.registrar_sinal(
        cfg.state_dir,
        {
            "tipo": "postado",
            "video_id": alvo.video_id,
            "handle": handle,
            "user_id": autor_id,
            "message_id": message_id,
        },
    )


def _reagir(discord: DiscordClient, canal_id: str, message_id: str, emoji: str, relatorio: RelatorioDeCadastro) -> None:
    try:
        discord.add_reaction(canal_id, message_id, emoji)
    except DiscordError as erro:
        relatorio.erros.append(f"cadastro: não consegui reagir em {message_id}: {erro}")


def _avisar_erro(cfg: Config, discord: DiscordClient, message_id: str, url: str, erro: str) -> None:
    """Mensagem nomeando a URL que falhou. **Gated por `YTR_POST_ENABLED`**: é texto que
    um humano lê, ao contrário da reação, que é marca idempotente."""
    if not cfg.post_enabled:
        return
    try:
        discord.post_message(
            cfg.canal_entrada,
            f"{cfg.emoji_erro} não consegui cadastrar `{url}`: {erro}",
            responder_a=message_id,
        )
    except DiscordError:
        pass


def processar(
    cfg: Config,
    canais: Canais,
    estado: Estado,
    cliente: Cliente,
    discord: DiscordClient | None,
    *,
    seco: bool = False,
) -> RelatorioDeCadastro:
    """Lê mensagens novas do canal de entrada e reage a cada uma.

    `discord=None` (sem `DISCORD_TOKEN`) ou sem `YTR_CANAL_ENTRADA`: no-op — o radar
    continua funcionando só como monitor, exatamente como antes da Fase 4.
    """
    relatorio = RelatorioDeCadastro()
    if discord is None or not cfg.canal_entrada:
        return relatorio

    cursor = _ler_cursor(cfg.state_dir)
    if not cursor:
        # Primeiro ciclo com cadastro ligado: começa a partir de agora, sem varrer o
        # histórico do canal — mesma postura do canal recém-monitorado, que semeia em
        # vez de avisar o catálogo inteiro.
        if not seco:
            _salvar_cursor(cfg.state_dir, snowflake_for(int(time.time() * 1000)))
        relatorio.linhas.append("cadastro: primeira vez — monitorando a partir de agora.")
        return relatorio

    try:
        mensagens = list(discord.iter_messages(cfg.canal_entrada, after=cursor))
    except DiscordError as erro:
        relatorio.erros.append(f"cadastro: não consegui ler {cfg.canal_entrada}: {erro}")
        return relatorio

    maior_id = cursor
    for mensagem in mensagens:
        maior_id = mensagem.get("id", maior_id)
        if (mensagem.get("author") or {}).get("bot"):
            continue

        alvos = _extrair_alvos(mensagem.get("content", ""))
        if not alvos:
            continue

        relatorio.mensagens += 1
        autor_id = str((mensagem.get("author") or {}).get("id", ""))
        message_id = mensagem["id"]

        for alvo in alvos:
            if alvo.tipo == "video":
                relatorio.videos_sinalizados += 1
                if seco:
                    continue
                _sinalizar_video(cfg, cliente, alvo, autor_id, message_id)
                _reagir(discord, cfg.canal_entrada, message_id, cfg.emoji_video, relatorio)
                continue

            # alvo.tipo == "canal"
            if not _autorizado(cfg, autor_id):
                continue

            achado, erro = _resolver_com_tentativas(alvo, cliente)
            if achado is None:
                relatorio.falhas += 1
                relatorio.linhas.append(f"{cfg.emoji_erro} não consegui cadastrar {alvo.url}: {erro}")
                if not seco:
                    _reagir(discord, cfg.canal_entrada, message_id, cfg.emoji_erro, relatorio)
                    _avisar_erro(cfg, discord, message_id, alvo.url, erro)
                continue

            if achado.channel_id in canais:
                relatorio.canais_repetidos += 1
                if not seco:
                    canais.marcar_visto(achado.channel_id)
                    canais.salvar()
                    _reagir(discord, cfg.canal_entrada, message_id, cfg.emoji_repetido, relatorio)
                continue

            relatorio.canais_novos += 1
            relatorio.linhas.append(
                f"{cfg.emoji_cadastrado} cadastrado pelo Discord: "
                f"{achado.handle or achado.channel_id} ({achado.nome or 'sem título'})"
            )
            if not seco:
                _registrar_canal(canais, estado, cfg, achado, alvo, autor_id)
                _reagir(discord, cfg.canal_entrada, message_id, cfg.emoji_cadastrado, relatorio)

    if not seco and maior_id != cursor:
        _salvar_cursor(cfg.state_dir, maior_id)

    return relatorio
