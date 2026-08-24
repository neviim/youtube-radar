"""O ciclo de monitoração: buscar feeds, decidir o que é novo, avisar.

**A semântica é at-least-once, e a ordem nunca se inverte:** aviso duplicado é
incômodo cosmético; vídeo perdido é a falha que o sistema existe para impedir. Daí
`avisa-depois-marca` — o id só entra no conjunto de avisados **depois** de o POST dar
certo.

Mas "at-least-once" não pode virar "infinitas vezes", e é aí que entram duas barreiras
que o desenho ingênuo não tem:

1. **Preflight**, antes do primeiro POST: prova que `.state` aceita escrita. Sem isso,
   um `.state` read-only deixa o sistema postar com sucesso e falhar ao marcar, a cada
   15 minutos, para sempre.
2. **`postagem_bloqueada`**, depois de um POST: se o save do estado falhar *depois* de
   uma mensagem ter saído, o ciclo para imediatamente e nenhum POST novo sai até o
   estado voltar.

O primeiro ciclo de um canal recém-cadastrado **semeia os 15 ids atuais e avisa zero**.
Uma linha de código, e é a diferença entre parecer intencional e parecer quebrado.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import ledger, texto
from .canal import Canal, Canais
from .config import Config
from .feed import Feed, FeedInvalido, Video, parse, proxima_em
from .rede import Cliente, RedeError
from .state import Estado, EstadoCanal, Saude, agora_utc

# Recuo por falha, em segundos: 15 → 30 → 60 → 120 min, teto 6 h. Falha de um canal
# **nunca** posta mensagem — canal morto viraria spam eterno. Aparece no `doctor`.
RECUO_POR_FALHA = (900, 1800, 3600, 7200, 21600)


@dataclass
class Novidade:
    canal: Canal
    video: Video


@dataclass
class RelatorioDeCiclo:
    canais_buscados: int = 0
    canais_pulados: int = 0
    canais_com_falha: int = 0
    semeados: int = 0
    novos: int = 0
    avisados: int = 0
    suprimidos_short: int = 0
    suprimidos_teto: int = 0
    bytes_gastos: int = 0
    erros: list[str] = field(default_factory=list)
    linhas: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        return (
            f"{self.canais_buscados} buscados · {self.semeados} semeados · "
            f"{self.novos} novos · {self.avisados} avisados · "
            f"{self.suprimidos_short} shorts · {self.suprimidos_teto} pelo teto · "
            f"{self.bytes_gastos} bytes · {self.canais_com_falha} com falha"
        )


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def devido(estado: EstadoCanal, agora: datetime | None = None) -> bool:
    """Já passou da hora de buscar este canal?

    Sem `proxima_busca` gravada, sim — canal novo ou estado apagado busca já.
    """
    if not estado.proxima_busca:
        return True
    try:
        return (agora or _agora()) >= datetime.fromisoformat(estado.proxima_busca)
    except ValueError:
        return True


def agendar(cfg: Config, estado: EstadoCanal, feed_resposta, agora: datetime | None = None) -> None:
    """Grava quando vale a pena voltar, a partir dos headers de cache.

    `max(piso, max_age - age)`, com **`age` ausente valendo 0** — medido: 2 dos 5
    canais não mandam `age`. E se o canal está quieto há mais de `recuo_lento_dias`, a
    espera sobe para `recuo_lento_segundos`. Isso é **heurística, não medição**: pode
    atrasar um canal que publica em rajada depois de um mês parado, e é por isso que
    está atrás de `YTR_RECUO_LENTO`.
    """
    agora = agora or _agora()
    espera = proxima_em(feed_resposta.cabecalhos, cfg.piso_segundos)

    if cfg.recuo_lento and estado.ultimo_publicado:
        try:
            ultimo = datetime.fromisoformat(estado.ultimo_publicado)
            if (agora - ultimo).days >= cfg.recuo_lento_dias:
                espera = max(espera, cfg.recuo_lento_segundos)
        except ValueError:
            pass

    estado.proxima_busca = (agora + timedelta(seconds=espera)).isoformat(timespec="seconds")


def agendar_falha(estado: EstadoCanal, erro: str, agora: datetime | None = None) -> None:
    estado.falhas += 1
    estado.ultimo_erro = erro[:300]
    indice = min(estado.falhas - 1, len(RECUO_POR_FALHA) - 1)
    espera = RECUO_POR_FALHA[indice]
    estado.proxima_busca = ((agora or _agora()) + timedelta(seconds=espera)).isoformat(timespec="seconds")


# ------------------------------------------------------------------ a busca


def buscar(cfg: Config, canal: Canal, cliente: Cliente, estado: EstadoCanal) -> tuple[Feed | None, object, str]:
    """(feed, resposta, erro). Nunca levanta: falha de um canal não derruba o ciclo."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={canal.channel_id}"
    try:
        resposta = cliente.get(url)
    except RedeError as erro:
        return None, None, str(erro)
    estado.bytes_ultimo_ciclo = resposta.bytes_no_fio
    if not resposta.ok:
        return None, resposta, f"HTTP {resposta.status}"
    try:
        return parse(resposta.texto), resposta, ""
    except FeedInvalido as erro:
        return None, resposta, str(erro)


def novidades(cfg: Config, canal: Canal, feed: Feed, estado: EstadoCanal) -> list[Video]:
    """Os vídeos que ainda não foram avisados, dos mais antigos para os mais novos.

    **O guarda é o conjunto de ids, não o carimbo de tempo.** Medido no feed real:
    `updated` chega 15 minutos depois de `published` no mesmo item, e uma estreia
    retroativa aparece com data anterior ao cursor. Data se move; id não.
    """
    avisar_shorts = canal.avisar_shorts or cfg.avisar_shorts
    novos = [
        v for v in feed.videos
        if not estado.ja_avisou(v.video_id) and (avisar_shorts or not v.is_short)
    ]
    return sorted(novos, key=lambda v: v.publicado)


def semear(cfg: Config, estado: EstadoCanal, feed: Feed) -> int:
    """Marca os 15 ids atuais como já vistos, sem avisar nenhum."""
    for video in feed.videos:
        estado.lembrar(video.video_id, cfg.lembrar_ids)
    estado.semeado = True
    if feed.videos:
        estado.ultimo_publicado = max(v.publicado for v in feed.videos)
    return len(feed.videos)


# ------------------------------------------------------------------ o ciclo


class PostagemBloqueada(RuntimeError):
    """Um POST deu certo e o estado não pôde ser salvo. Nenhum POST novo sai."""


def rodar(
    cfg: Config,
    canais: Canais,
    estado: Estado,
    cliente: Cliente,
    publicador=None,
    seco: bool = False,
) -> RelatorioDeCiclo:
    """Um ciclo inteiro.

    `publicador` é injetado: nos testes é um dublê, em produção é o Discord. O ciclo
    não sabe a diferença, e é isso que deixa a Fase 4 ser exercitada de ponta a ponta
    antes de o id do canal de aviso existir.
    """
    relatorio = RelatorioDeCiclo()
    agora = _agora()
    saude = Saude.carregar(cfg.state_dir)

    if saude.postagem_bloqueada:
        relatorio.erros.append(
            f"postagem bloqueada: {saude.motivo} — conserte o estado e limpe "
            "`.state/saude.json` para voltar a avisar."
        )

    alvos = []
    for canal in canais.ativos():
        atual = estado.carregar(canal.channel_id)
        if devido(atual, agora):
            alvos.append((canal, atual))
        else:
            relatorio.canais_pulados += 1

    # I/O puro: um pool pequeno resolve. `max_workers` de 8 é escolha, não medição —
    # 20 canais de ~7 KB não fazem disso um gargalo em nenhuma configuração razoável.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(alvos)))) as pool:
        buscas = list(pool.map(lambda par: (par[0], par[1], *buscar(cfg, par[0], cliente, par[1])), alvos))

    pendentes: list[Novidade] = []

    for canal, atual, feed, resposta, erro in buscas:
        relatorio.canais_buscados += 1
        if erro or feed is None:
            relatorio.canais_com_falha += 1
            relatorio.erros.append(f"{canal.handle or canal.channel_id}: {erro}")
            agendar_falha(atual, erro, agora)
            estado.salvar(atual)
            continue

        atual.falhas = 0
        atual.ultimo_erro = ""
        agendar(cfg, atual, resposta, agora)

        if not atual.semeado:
            quantos = semear(cfg, atual, feed)
            relatorio.semeados += quantos
            relatorio.linhas.append(
                f"📡 monitorando {canal.handle or canal.nome or canal.channel_id}: "
                f"{quantos} vídeos semeados, aviso a partir do próximo."
            )
            estado.salvar(atual)
            continue

        todos_novos = [v for v in feed.videos if not atual.ja_avisou(v.video_id)]
        novos = novidades(cfg, canal, feed, atual)
        relatorio.suprimidos_short += len(todos_novos) - len(novos)

        # Short suprimido também entra em `avisados`: senão ele é recontado como "novo"
        # a cada ciclo enquanto estiver no feed, e o número de shorts suprimidos cresce
        # sem parar num relatório que deveria contar eventos, não estados.
        ids_a_avisar = {v.video_id for v in novos}
        for video in todos_novos:
            if video.video_id not in ids_a_avisar:
                atual.lembrar(video.video_id, cfg.lembrar_ids)

        do_canal = novos[: cfg.max_avisos_canal]
        if len(novos) > len(do_canal):
            relatorio.suprimidos_teto += len(novos) - len(do_canal)
            relatorio.linhas.append(
                f"⚠ {canal.handle or canal.channel_id}: {len(novos) - len(do_canal)} "
                f"vídeo(s) além do teto de {cfg.max_avisos_canal} neste ciclo."
            )
            # Os que passaram do teto **não** são marcados: eles voltam no próximo
            # ciclo. O teto atrasa o aviso, não o cancela.

        relatorio.novos += len(novos)
        pendentes.extend(Novidade(canal, v) for v in do_canal)
        estado.salvar(atual)

    if relatorio.canais_buscados and relatorio.canais_com_falha == relatorio.canais_buscados:
        saude.ciclos_com_falha_total += 1
    else:
        saude.ciclos_com_falha_total = 0

    _publicar(cfg, estado, relatorio, pendentes, publicador, saude, seco)

    relatorio.bytes_gastos = cliente.bytes_gastos
    saude.heartbeat = agora_utc()
    saude.salvar(cfg.state_dir)
    return relatorio


def _publicar(cfg, estado, relatorio, pendentes, publicador, saude, seco) -> None:
    """Posta, e **marca só depois**. Para tudo se o marcar falhar."""
    if not pendentes:
        return

    if seco or publicador is None or not cfg.post_enabled:
        for novidade in pendentes[: cfg.max_avisos_ciclo]:
            relatorio.linhas.append(
                f"[seco] avisaria {novidade.video.video_id} · {novidade.video.titulo[:70]}"
            )
        return

    if saude.postagem_bloqueada:
        return

    for novidade in pendentes[: cfg.max_avisos_ciclo]:
        mensagem = texto.aviso(novidade.video)
        try:
            message_id = publicador.publicar(mensagem)
        except Exception as erro:  # noqa: BLE001 — qualquer falha de rede é "não avisou"
            relatorio.erros.append(f"não consegui avisar {novidade.video.video_id}: {erro}")
            continue

        # Daqui para baixo a mensagem **já foi lida por alguém**. Uma falha de estado
        # agora não pode ser silenciosa: ela vira barreira, não retentativa.
        try:
            atual = estado.carregar(novidade.canal.channel_id)
            atual.lembrar(novidade.video.video_id, cfg.lembrar_ids)
            atual.ultimo_publicado = novidade.video.publicado or atual.ultimo_publicado
            estado.salvar(atual)
            ledger.registrar_aviso(
                cfg.state_dir, message_id, novidade.video.video_id, novidade.canal.channel_id
            )
        except OSError as erro:
            saude.postagem_bloqueada = True
            saude.motivo = (
                f"avisei {novidade.video.video_id} e não consegui marcar: {erro}. "
                "Parei para não repostar o mesmo vídeo a cada ciclo."
            )
            saude.salvar(cfg.state_dir)
            relatorio.erros.append(saude.motivo)
            raise PostagemBloqueada(saude.motivo) from erro

        relatorio.avisados += 1

    if len(pendentes) > cfg.max_avisos_ciclo:
        relatorio.suprimidos_teto += len(pendentes) - cfg.max_avisos_ciclo
        relatorio.linhas.append(
            f"⚠ {len(pendentes) - cfg.max_avisos_ciclo} aviso(s) além do teto de "
            f"{cfg.max_avisos_ciclo} por ciclo — voltam no próximo."
        )
