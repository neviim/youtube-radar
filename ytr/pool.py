"""O pool de recomendação e a montagem do digest diário (Fase 7, D7 do plano).

**Dois pools no plano; só o primeiro está implementado aqui.**

- **Pool 1** — os canais que o vault mostra que ele gosta (`perfil.canais_do_pool`).
  Não são monitorados (não geram aviso individual); buscados numa grade lenta, 1x/dia:
  60 canais × ~4,9 KB ≈ 294 KB — é o que transforma os canais de um-vídeo-só do vault
  em pool de recomendação em vez de ruído.
- **Pool 2** [não implementado, marcado de propósito] — vídeos dos canais **monitorados**
  que não passaram a barra do aviso (Shorts, baixa afinidade). `ciclo.rodar()` hoje só
  *conta* `suprimidos_short` — não persiste qual vídeo era, porque marcá-lo como
  avisado (`lembrar`) é o que impede o mesmo Short de reaparecer a cada ciclo. Fazer
  Pool 2 direito exige um segundo lugar para guardar "suprimido, mas candidato", o que
  muda o que a Fase 3 (fechada e testada) grava. Não fiz essa mudança cruzada sem
  alinhar — fica em aberto, não como decisão silenciosa.

**A camada determinística roda sempre, e primeiro** (D7): afinidade de canal e de tag
(`gosto.Perfil.pontuar_afinidade`), sobreposição léxica contra o corpus do perfil
(`lexico.Indice`, já construído em `Perfil.indice`), e um prior de engajamento —
`views` normalizado pela mediana do próprio canal, para não premiar canal grande por
ser grande. O modelo (Fase 8, não implementada) só narraria o que isto decidiu.

**Liveness é preguiçosa, não em massa.** Checar oEmbed dos ~180 candidatos possíveis
custaria ~80 KB à toa; em vez disso, só quem *entraria* no digest pela pontuação é
verificado — o suficiente para "zero link morto" sem gastar banda em candidato que
nunca seria mostrado.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import ledger
from .canal import Alvo, CanalError, resolver, vivo
from .ciclo import buscar
from .canal import Canal as _Canal
from .config import Config
from .discord_client import DiscordClient, DiscordError
from .feed import Video
from .gosto import Perfil
from .rede import Cliente
from .state import EstadoCanal, escrever_atomico

# Pesos internos de campo dentro do casamento léxico: título pesa mais que descrição.
# Não é medido — é o mesmo tipo de chute inicial que os `YTR_PESO_*` já são, só que
# fino demais para merecer variável de ambiente própria.
_PESO_TITULO_LEXICO = 3.0
_PESO_DESCRICAO_LEXICO = 1.0

# Só os N vídeos mais recentes de cada canal do pool entram na pontuação. A banda já
# foi gasta buscando os 15 do feed; isto poupa CPU, não rede — pontuar léxico contra o
# corpus inteiro do perfil para os 15 × 60 candidatos seria trabalho jogado fora para
# os 12 mais antigos, que quase nunca vencem os recentes no componente de recência.
CANDIDATOS_POR_CANAL = 3


def _caminho_mapa_pool(state_dir) -> Path:
    return Path(state_dir) / "pool_canais.json"


def ler_mapa_pool(state_dir) -> dict:
    import json

    caminho = _caminho_mapa_pool(state_dir)
    if not caminho.is_file():
        return {}
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def salvar_mapa_pool(state_dir, mapa: dict) -> None:
    import json

    escrever_atomico(
        _caminho_mapa_pool(state_dir), json.dumps(mapa, ensure_ascii=False, indent=2) + "\n"
    )


def resolver_pool(perfil: Perfil, cliente: Cliente, mapa: dict) -> dict:
    """`handle -> channel_id`, resolvido uma vez na vida de cada canal do pool.

    Reaproveita `canal.resolver`: a chamada que confirma o id já busca o feed para
    validar — o custo de ~150 KB só é pago na primeira vez que um handle aparece.
    """
    for handle in perfil.canais_do_pool:
        chave = handle.lstrip("@").casefold()
        if chave in mapa:
            continue
        alvo = Alvo("canal", f"https://youtube.com/@{chave}", handle=chave)
        try:
            achado = resolver(alvo, cliente)
        except CanalError:
            continue
        mapa[chave] = str(achado.channel_id)
    return mapa


def buscar_pool(cfg: Config, mapa: dict, cliente: Cliente) -> list[tuple[str, list[Video]]]:
    """Um feed por canal do pool. Grade lenta: não monitora, não semeia estado."""
    saida = []
    for handle, channel_id in mapa.items():
        canal = _Canal(channel_id=channel_id, handle=f"@{handle}")
        feed, _resposta, erro = buscar(cfg, canal, cliente, EstadoCanal())
        if feed is None:
            continue
        saida.append((handle, feed.videos))
    return saida


@dataclass
class Candidato:
    video: Video
    handle: str
    componentes: dict = field(default_factory=dict)
    score: float = 0.0
    liveness: str = "nao_verificado"
    decisao: str = "cortado_por_score"
    razao: str = ""


def _mediana_views(videos: list[Video]) -> float:
    vistos = [v.views for v in videos if v.views]
    return statistics.median(vistos) if vistos else 0.0


def _recencia(video: Video, agora: datetime) -> float:
    publicado = video.publicado_em
    if publicado is None:
        return 0.0
    dias = max(0.0, (agora - publicado).total_seconds() / 86400)
    return 1.0 / (1.0 + dias)  # heurística — não medida, decai a metade em ~1 dia


def _razao(componentes: dict) -> str:
    dominante = max(componentes, key=lambda k: componentes[k], default="")
    return {
        "canal": "canal que você já acompanha",
        "tag": "tema que aparece bastante no que você salvou",
        "lexico": "parecido com o que você costuma ler e assistir",
        "engajamento": "engajamento bem acima do normal para esse canal",
        "recencia": "saiu há pouco",
    }.get(dominante, "pontuação combinada de sinais")


def montar_candidatos(cfg: Config, perfil: Perfil, pool: list[tuple[str, list[Video]]]) -> list[Candidato]:
    agora = datetime.now(timezone.utc)
    saida: list[Candidato] = []
    # Fora do laço: são até ~180 candidatos, e reconstruir + retokenizar a consulta
    # (o corpus inteiro do perfil) a cada um multiplicaria por 180 um trabalho que só
    # precisa ser feito uma vez por chamada de `digest`.
    consulta_do_perfil = " ".join(n.texto_de_perfil for n in perfil.notas)

    for handle, videos in pool:
        recentes = sorted(videos, key=lambda v: v.publicado, reverse=True)[:CANDIDATOS_POR_CANAL]
        mediana = _mediana_views(videos)
        for video in recentes:
            afinidade = perfil.pontuar_afinidade(cfg, video.channel_id, handle)
            lexico = 0.0
            if perfil.indice is not None:
                lexico = perfil.indice.pontuar(
                    consulta_do_perfil,
                    {
                        "titulo": (video.titulo, _PESO_TITULO_LEXICO),
                        "descricao": (video.descricao, _PESO_DESCRICAO_LEXICO),
                    },
                ) * cfg.peso_lexico
            engajamento = ((video.views / mediana) if mediana else 1.0) * cfg.peso_engajamento
            recencia = _recencia(video, agora) * cfg.peso_recencia

            componentes = {**afinidade, "lexico": round(lexico, 3),
                           "engajamento": round(engajamento, 3), "recencia": round(recencia, 3)}
            candidato = Candidato(
                video=video, handle=handle, componentes=componentes,
                score=sum(componentes.values()), razao=_razao(componentes),
            )
            saida.append(candidato)

    saida.sort(key=lambda c: c.score, reverse=True)
    return saida


def selecionar(cfg: Config, candidatos: list[Candidato], cliente: Cliente) -> list[Candidato]:
    """Escolhe os `cfg.digest_itens`, com teto `cfg.digest_por_canal` por canal.

    Liveness só é checada para quem *entraria* — ver docstring do módulo.
    """
    por_canal: dict[str, int] = {}
    enviados = 0

    for candidato in candidatos:
        if enviados >= cfg.digest_itens:
            candidato.decisao = "cortado_por_teto"
            continue
        if por_canal.get(candidato.handle, 0) >= cfg.digest_por_canal:
            candidato.decisao = "cortado_por_canal"
            continue

        if vivo(candidato.video.url, cliente):
            candidato.liveness = "vivo"
            candidato.decisao = "enviado"
            por_canal[candidato.handle] = por_canal.get(candidato.handle, 0) + 1
            enviados += 1
        else:
            candidato.liveness = "morto"
            candidato.decisao = "cortado_por_liveness"

    return candidatos


# ------------------------------------------------------------------- feedback


def capturar_feedback(cfg: Config, discord: DiscordClient | None) -> list[str]:
    """Relê 👍/👎 dos itens **enviados** dos últimos `YTR_JANELA_FEEDBACK_DIAS` digests.

    Só leitura (`GET .../reactions/{emoji}`) — não obedece `YTR_POST_ENABLED` porque
    não é POST, no mesmo espírito da reação de cadastro. Idempotente por construção:
    `ledger.registrar_sinal` já dedupa por (mensagem, usuário, reação, vídeo), então
    reler a mesma janela em todo `digest` não duplica sinal.
    """
    linhas: list[str] = []
    if discord is None or not cfg.canal_aviso:
        return linhas

    existentes = {tuple(s.get(k, "") for k in ("message_id", "user_id", "reacao", "video_id"))
                  for s in ledger.sinais(cfg.state_dir)}

    for digest in ledger.digests_recentes(cfg.state_dir, cfg.janela_feedback_dias):
        for item in digest.enviados:
            if not item.message_id:
                continue
            for emoji in ("👍", "👎"):
                try:
                    usuarios = discord.reacted_users(cfg.canal_aviso, item.message_id, emoji)
                except DiscordError:
                    continue
                for usuario in usuarios:
                    if usuario.get("bot"):
                        continue
                    user_id = str(usuario.get("id", ""))
                    chave = (item.message_id, user_id, emoji, item.video_id)
                    if chave in existentes:
                        continue
                    novo = ledger.registrar_sinal(
                        cfg.state_dir,
                        {
                            "reacao": emoji,
                            "video_id": item.video_id,
                            "handle": item.canal,
                            "channel_id": item.channel_id,
                            "user_id": user_id,
                            "message_id": item.message_id,
                        },
                        existentes=existentes,
                    )
                    if novo:
                        linhas.append(f"{emoji} capturado: {item.video_id} ({digest.data})")

    return linhas
