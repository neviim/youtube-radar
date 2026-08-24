"""Classificar URL do YouTube, resolver `channel_id`, e guardar a lista de canais.

Três decisões deste módulo, e as três vieram de medição ou de crítica:

**1. Link de canal cadastra; link de vídeo só registra sinal.** Resolvi por oEmbed as
77 URLs de vídeo do vault: **72 responderam, e são 60 canais únicos — 53 deles com um
vídeo só**. "Ele salvou um vídeo" é sinal sobre o *vídeo*, não sobre o *canal*.
Cadastrar canal a partir de vídeo daria 60 monitorados dominados por canais que ele
tocou uma vez.

**2. Nenhuma string vira `channel_id`.** Só `ChannelId` entra em `canais.yaml`, e ele
valida a forma no construtor. O motivo é concreto: o `yt-dlp` desta máquina
(2024.04.09) responde `--print channel_id` com **saída vazia e código 0**. Um fallback
que confia no código de saída cadastraria um canal com id em branco e diria que deu
certo.

**3. Nenhum canal é persistido sem uma leitura de RSS bem-sucedida.** Isto inclui o
caminho "barato", `/channel/UC…`: uma URL com 22 caracteres válidos passa pela regex e
pode simplesmente não existir. A regex evita id **vazio**; só o feed evita id
**errado**. E a leitura não é trabalho extra — é a mesma que semeia os 15 ids atuais
para o canal não avisar o catálogo inteiro no primeiro ciclo.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from .feed import ID_CANAL, URL_FEED, FeedInvalido, parse
from .rede import Cliente, RedeError
from .state import agora_utc, escrever_atomico

# `externalId` é onde a página de canal guarda o id. Fica quase no fim do HTML (byte
# 1.611.260 de 1.635.667, medido), então parar a leitura no meio não ajuda — gzip ajuda.
EXTERNAL_ID = re.compile(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"')

OEMBED = "https://www.youtube.com/oembed?url={url}&format=json"

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class CanalError(RuntimeError):
    """Não deu para resolver. Sai como uma frase, nunca como traceback."""


class ChannelId(str):
    """Um id de canal do YouTube, validado na construção.

    Subclasse de `str` para poder ir direto para uma URL, e com `__new__` validando
    para que **não exista** um `ChannelId` inválido em lugar nenhum do programa. A
    alternativa — validar na hora de escrever o YAML — deixa o valor errado circular
    entre a resolução e a escrita, que é justamente onde ele nasce.
    """

    def __new__(cls, bruto: object) -> "ChannelId":
        texto = str(bruto or "").strip()
        if not ID_CANAL.fullmatch(texto):
            raise CanalError(
                f"{texto!r} não tem a forma de um id de canal (UC + 22 caracteres). "
                "Id vazio ou truncado costuma vir de uma ferramenta que falhou e "
                "devolveu código 0 mesmo assim."
            )
        return super().__new__(cls, texto)

    @property
    def url_feed(self) -> str:
        return URL_FEED.format(channel_id=str(self))


# ------------------------------------------------------------- classificação


@dataclass
class Alvo:
    """O que uma URL do YouTube é. `tipo` ∈ canal | video | nenhum."""

    tipo: str
    url: str
    channel_id: str = ""
    handle: str = ""
    video_id: str = ""


def classificar(url: str) -> Alvo:
    """URL → canal, vídeo ou nada. Sem rede.

    Canoniza `/shorts/ID` e `youtu.be/ID` para o mesmo `video_id` de `watch?v=ID` — os
    três são o mesmo vídeo, e tratá-los como três faria o mesmo link contar três vezes
    no perfil de gosto.
    """
    bruto = (url or "").strip()
    if not bruto:
        return Alvo("nenhum", bruto)
    if "://" not in bruto:
        bruto = "https://" + bruto

    partes = urlparse(bruto)
    host = partes.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host not in ("youtube.com", "youtu.be", "music.youtube.com"):
        return Alvo("nenhum", url)

    caminho = partes.path.rstrip("/")

    if host == "youtu.be":
        vid = caminho.lstrip("/")
        return Alvo("video", url, video_id=vid) if VIDEO_ID.fullmatch(vid) else Alvo("nenhum", url)

    if caminho == "/watch":
        vid = (parse_qs(partes.query).get("v") or [""])[0]
        return Alvo("video", url, video_id=vid) if VIDEO_ID.fullmatch(vid) else Alvo("nenhum", url)

    for prefixo in ("/shorts/", "/live/", "/embed/"):
        if caminho.startswith(prefixo):
            vid = caminho[len(prefixo):].split("/")[0]
            return Alvo("video", url, video_id=vid) if VIDEO_ID.fullmatch(vid) else Alvo("nenhum", url)

    if caminho.startswith("/channel/"):
        candidato = caminho[len("/channel/"):].split("/")[0]
        # Casa a forma aqui, mas **não** constrói `ChannelId` ainda: o construtor é o
        # portão da persistência, e este caminho ainda vai passar pelo feed.
        if ID_CANAL.fullmatch(candidato):
            return Alvo("canal", url, channel_id=candidato)
        return Alvo("nenhum", url)

    if caminho.startswith("/@"):
        return Alvo("canal", url, handle=caminho.split("/")[1])

    for prefixo in ("/c/", "/user/"):
        if caminho.startswith(prefixo):
            nome = caminho[len(prefixo):].split("/")[0]
            return Alvo("canal", url, handle=nome) if nome else Alvo("nenhum", url)

    return Alvo("nenhum", url)


# ---------------------------------------------------------------- resolução


@dataclass
class Resolucao:
    channel_id: ChannelId
    nome: str = ""
    handle: str = ""
    fonte: str = ""
    videos_atuais: list[str] = field(default_factory=list)
    bytes_gastos: int = 0


def resolver(alvo: Alvo, cliente: Cliente, usar_yt_dlp: bool = False) -> Resolucao:
    """Resolve e **confirma pelo feed**. Nunca devolve um canal que o RSS não abriu.

    Ordem das fontes, com o custo medido de cada uma:

    1. `/channel/UC…` — sem requisição de resolução (mas a confirmação por feed vem
       igual, e é o que evita id **errado**);
    2. página `/@handle` com UA de navegador e gzip — ~150 KB, 0,5 s, **uma vez na vida
       do canal**;
    3. `yt-dlp` — só com `usar_yt_dlp=True`, e a saída é validada por forma. A versão
       desta máquina devolve vazio com código 0.
    """
    gasto_inicial = cliente.bytes_gastos

    if alvo.channel_id:
        bruto, fonte = alvo.channel_id, "url"
    else:
        bruto, fonte = _resolver_handle(alvo, cliente, usar_yt_dlp)

    channel_id = ChannelId(bruto)
    feed = _confirmar_por_feed(channel_id, cliente)

    return Resolucao(
        channel_id=channel_id,
        nome=feed.titulo,
        handle=alvo.handle or "",
        fonte=fonte,
        videos_atuais=[v.video_id for v in feed.videos],
        bytes_gastos=cliente.bytes_gastos - gasto_inicial,
    )


def _resolver_handle(alvo: Alvo, cliente: Cliente, usar_yt_dlp: bool) -> tuple[str, str]:
    try:
        resposta = cliente.get(alvo.url, navegador=True)
    except RedeError as erro:
        resposta = None
        if not usar_yt_dlp:
            raise CanalError(f"não consegui abrir {alvo.url}: {erro}") from erro

    if resposta is not None and resposta.ok:
        achado = EXTERNAL_ID.search(resposta.texto)
        if achado:
            return achado.group(1), "pagina"
    if resposta is not None and not resposta.ok and not usar_yt_dlp:
        raise CanalError(f"{alvo.url} respondeu HTTP {resposta.status}.")

    if usar_yt_dlp:
        achado = _yt_dlp_channel_id(alvo.url)
        if achado:
            return achado, "yt-dlp"

    raise CanalError(
        f"não achei o id do canal em {alvo.url}. "
        "A página abriu mas não trouxe `externalId` — pode ser canal inexistente ou "
        "mudança no HTML do YouTube."
    )


def _yt_dlp_channel_id(url: str) -> str:
    """`yt-dlp --print channel_id`, validado **por forma**, nunca pelo código de saída.

    Medido nesta máquina (yt-dlp 2024.04.09, 2026-08-24):

        yt-dlp --skip-download --playlist-items 1 --print channel_id <handle>
          → saída VAZIA, EXIT=0

    Um fallback que confiasse no código de saída persistiria `channel_id: ""` e
    reportaria sucesso. Por isso a saída passa pelo mesmo `ChannelId` de todo mundo, e
    há um teste que afirma que string vazia é recusada.
    """
    try:
        processo = subprocess.run(
            ["yt-dlp", "--skip-download", "--playlist-items", "1",
             "--print", "channel_id", url],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for linha in (processo.stdout or "").splitlines():
        if ID_CANAL.fullmatch(linha.strip()):
            return linha.strip()
    return ""


def _confirmar_por_feed(channel_id: ChannelId, cliente: Cliente):
    """Lê o RSS. É o que separa "id com forma válida" de "canal que existe".

    A mesma resposta serve de semente: os 15 ids que ela traz entram no conjunto de
    avisados, para o canal recém-cadastrado **não** avisar o catálogo inteiro.
    """
    try:
        resposta = cliente.get(channel_id.url_feed)
    except RedeError as erro:
        raise CanalError(
            f"o id {channel_id} tem a forma certa, mas o RSS não abriu ({erro}). "
            "Não cadastro canal que não posso ler."
        ) from erro

    if resposta.status == 404:
        raise CanalError(
            f"o id {channel_id} tem a forma certa e **não existe** — o RSS respondeu "
            "404. É por isso que a forma sozinha não basta para cadastrar."
        )
    if not resposta.ok:
        raise CanalError(f"o RSS de {channel_id} respondeu HTTP {resposta.status}.")
    try:
        return parse(resposta.texto)
    except FeedInvalido as erro:
        raise CanalError(f"o RSS de {channel_id} não é um feed válido: {erro}") from erro


def handle_por_oembed(url_video: str, cliente: Cliente) -> str:
    """Handle do canal a partir de uma URL de vídeo — 442 bytes por chamada (medido).

    O oEmbed devolve `author_url: "https://www.youtube.com/@Nome"`. É o caminho barato
    de "vídeo → canal", e é o que resolveu os 77 links do vault em 27 segundos.
    """
    try:
        resposta = cliente.get(OEMBED.format(url=url_video))
    except RedeError:
        return ""
    if not resposta.ok:
        return ""
    try:
        dados = json.loads(resposta.texto)
    except json.JSONDecodeError:
        return ""
    autor = str(dados.get("author_url") or "")
    return autor.rstrip("/").rsplit("/", 1)[-1] if "/@" in autor else ""


def vivo(url_video: str, cliente: Cliente) -> bool:
    """O vídeo ainda existe? 442 bytes por candidato.

    Obrigatório antes de recomendar: **5 dos 77 vídeos salvos já são 404** em ~11 meses
    — 6,5% de podridão por ano. Recomendar um link morto uma vez ensina a ignorar o
    digest para sempre.
    """
    try:
        return cliente.get(OEMBED.format(url=url_video)).ok
    except RedeError:
        return False


# ------------------------------------------------------------- canais.yaml


@dataclass
class Canal:
    channel_id: str
    handle: str = ""
    nome: str = ""
    url_original: str = ""
    cadastrado_em: str = ""
    cadastrado_por: str = ""
    ativo: bool = True
    avisar_shorts: bool = False
    transcricao: bool = False
    falhas: int = 0
    visto_em: str = ""


class Canais:
    """A lista de canais monitorados, em YAML.

    YAML e não SQLite porque **esta lista é a curadoria dele**: ele precisa poder abrir
    o arquivo e apagar uma linha. Linha de banco não se edita no `vim` nem no Obsidian.
    O resto do estado é derivado e descartável; este não é.

    O arquivo tem mais de um comando que escreve (o ciclo e o `canais desativar`). A
    exclusão vem do `flock` de `ytr.trava`, não do desenho — e isso tem teste.
    """

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self._canais: dict[str, Canal] = {}
        self.carregar()

    def carregar(self) -> None:
        self._canais = {}
        if not self.caminho.is_file():
            return
        try:
            dados = yaml.safe_load(self.caminho.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as erro:
            raise CanalError(
                f"{self.caminho} não é YAML válido: {erro}. "
                "Este arquivo é editado à mão de propósito — conserte a linha e rode de "
                "novo. Não vou reescrevê-lo por cima."
            ) from erro
        for bruto in dados.get("canais") or []:
            if not isinstance(bruto, dict) or not bruto.get("channel_id"):
                continue
            conhecidos = {c: bruto.get(c) for c in Canal.__dataclass_fields__ if c in bruto}
            canal = Canal(**conhecidos)
            self._canais[canal.channel_id] = canal

    def __len__(self) -> int:
        return len(self._canais)

    def __contains__(self, channel_id: object) -> bool:
        return str(channel_id) in self._canais

    def get(self, channel_id: str) -> Canal | None:
        return self._canais.get(str(channel_id))

    def todos(self) -> list[Canal]:
        return sorted(self._canais.values(), key=lambda c: (not c.ativo, c.handle or c.channel_id))

    def ativos(self) -> list[Canal]:
        return [c for c in self.todos() if c.ativo]

    def por_apelido(self, apelido: str) -> Canal | None:
        """Acha por id, por handle com ou sem `@`, ou por nome. Para a linha de comando.

        Existe porque a alternativa é o Dotcom ter de copiar um `UC` de 24 caracteres
        para desativar um canal — o tipo de atrito que faz alguém editar o YAML à mão
        com o container de pé, que é a corrida que o `flock` existe para impedir.
        """
        chave = (apelido or "").strip()
        if not chave:
            return None
        if chave in self._canais:
            return self._canais[chave]
        alvo = chave.lstrip("@").casefold()
        for canal in self._canais.values():
            if canal.handle.lstrip("@").casefold() == alvo or canal.nome.casefold() == alvo:
                return canal
        return None

    def adicionar(self, channel_id: ChannelId, **campos) -> Canal:
        """Só aceita `ChannelId`. Escrita de string crua é erro de tipo, não de valor.

        O `isinstance` parece redundante numa linguagem sem tipos em runtime, e é
        justamente por isso que ele está aqui: sem ele, a garantia do construtor vale só
        para quem se lembrou de usá-lo.
        """
        if not isinstance(channel_id, ChannelId):
            raise CanalError(
                "`adicionar` só aceita `ChannelId`, não uma string qualquer. "
                "Construa com `ChannelId(...)`, que valida a forma, e passe o resultado."
            )
        canal = Canal(channel_id=str(channel_id), cadastrado_em=agora_utc(), **campos)
        self._canais[canal.channel_id] = canal
        return canal

    def marcar_visto(self, channel_id: str) -> None:
        canal = self._canais.get(str(channel_id))
        if canal:
            canal.visto_em = agora_utc()

    def salvar(self) -> None:
        corpo = {
            "canais": [
                {k: v for k, v in asdict(c).items() if v not in ("", None)}
                for c in self.todos()
            ]
        }
        texto = (
            "# Canais monitorados pelo youtube-radar.\n"
            "# Editável à mão: apagar uma linha para de monitorar; `ativo: false` pausa\n"
            "# sem perder o histórico. Pare o container antes de editar, ou use\n"
            "# `ytr canais desativar` — os dois tomam o mesmo lock.\n"
            + yaml.safe_dump(corpo, allow_unicode=True, sort_keys=False)
        )
        escrever_atomico(self.caminho, texto)
