"""Leitor do RSS de canal do YouTube. Sem chave de API, sem raspagem.

    https://www.youtube.com/feeds/videos.xml?channel_id=UC...

O que foi **medido** neste feed, e que decide o desenho deste módulo:

- **Comprime 5,3×** com `Accept-Encoding: gzip` (26.136 → 4.932 bytes). Toda requisição
  pede gzip.
- **`cache-control: max-age=900` em 100% dos canais medidos** (5 de 5), e **`age` em
  60% deles** (3 de 5). Por isso `Resposta.proxima_em()` trata `age` como opcional e
  assume 0 quando falta — a escolha conservadora é esperar o `max-age` inteiro.
- **Sempre 15 entradas**, e `?playlist_id=UU...` também devolve 15: **não existe
  backfill histórico por RSS**.
- **`published` ≠ `updated`** no mesmo item (15 minutos de diferença, medidos). Data se
  move; id não. **A deduplicação é por `video_id`.**
- **`/shorts/` no `href` do `<link>` identifica Short de graça.** No feed de amostra,
  **8 das 15 entradas são Shorts** — mais da metade. Sem esse filtro, o radar viraria
  uma torneira de Shorts.
- Cada entrada traz `media:statistics views` e `media:starRating`, atualizados a cada
  busca. Que os campos chegam é medido; que sejam bom sinal de qualidade é hipótese.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

URL_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# A forma de um id de canal. Vale aqui e em `canal.ChannelId` — é a mesma regra, e
# ela é o que separa "resolvi o canal" de "recebi uma string".
ID_CANAL = re.compile(r"UC[A-Za-z0-9_-]{22}")

# `max-age` do YouTube, para quando o header não vier. Medido em 900 nos 5 canais.
MAX_AGE_PADRAO = 900


class FeedInvalido(ValueError):
    """XML que não é um feed de canal do YouTube.

    Erro nomeado, e não `ParseError` cru, porque quem chama precisa distinguir "o
    YouTube devolveu uma página de erro" de "o disco está corrompido" — e porque um
    traceback de `xml.etree` no log não diz qual canal quebrou.
    """


@dataclass
class Video:
    video_id: str
    titulo: str
    url: str
    channel_id: str = ""
    canal: str = ""
    publicado: str = ""
    atualizado: str = ""
    descricao: str = ""
    is_short: bool = False
    views: int = 0
    rating_media: float = 0.0
    rating_n: int = 0

    @property
    def publicado_em(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.publicado)
        except (ValueError, TypeError):
            return None

    def idade_dias(self, agora: datetime | None = None) -> float:
        """Dias desde a publicação. `-1` quando a data não dá para ler."""
        quando = self.publicado_em
        if quando is None:
            return -1.0
        agora = agora or datetime.now(timezone.utc)
        return (agora - quando).total_seconds() / 86400.0


@dataclass
class Feed:
    channel_id: str = ""
    titulo: str = ""
    videos: list[Video] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.videos is None:
            self.videos = []


def _texto(elemento, caminho: str) -> str:
    achado = elemento.find(caminho, NS)
    return (achado.text or "").strip() if achado is not None else ""


def parse(xml: str | bytes) -> Feed:
    """XML → `Feed`. Levanta `FeedInvalido` em vez de deixar `ParseError` subir.

    Tolerante por dentro: uma entrada sem `videoId` é pulada, não derruba as outras
    catorze. O YouTube já mudou este XML antes e vai mudar de novo; perder um item é
    aceitável, perder o ciclo não.
    """
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as erro:
        raise FeedInvalido(f"XML malformado: {erro}") from erro

    if not raiz.tag.endswith("feed"):
        raise FeedInvalido(f"raiz é <{raiz.tag}>, esperava <feed> do Atom.")

    feed = Feed(titulo=_texto(raiz, "a:title"))

    # **Não confie no `yt:channelId` do topo.** Medido no feed real do canal
    # `UC_x5XG1OV2P6uZZ5FSM9Ttw`: o elemento do topo traz `_x5XG1OV2P6uZZ5FSM9Ttw`,
    # **sem o prefixo `UC`**, enquanto o de cada `<entry>` traz o id completo. Persistir
    # o do topo daria uma chave que nenhuma URL de feed aceita — e o erro só apareceria
    # no próximo ciclo, como "canal que nunca tem vídeo novo".
    do_topo = _texto(raiz, "yt:channelId")

    for entrada in raiz.findall("a:entry", NS):
        video = _entrada(entrada, feed)
        if video is not None:
            feed.videos.append(video)

    das_entradas = next((v.channel_id for v in feed.videos if ID_CANAL.fullmatch(v.channel_id)), "")
    feed.channel_id = das_entradas or do_topo
    return feed


def _entrada(entrada, feed: Feed) -> Video | None:
    video_id = _texto(entrada, "yt:videoId")
    if not video_id:
        return None

    link = entrada.find("a:link", NS)
    href = (link.get("href") if link is not None else "") or ""

    grupo = entrada.find("media:group", NS)
    descricao = _texto(grupo, "media:description") if grupo is not None else ""

    views, media, quantos = 0, 0.0, 0
    comunidade = grupo.find("media:community", NS) if grupo is not None else None
    if comunidade is not None:
        estatisticas = comunidade.find("media:statistics", NS)
        if estatisticas is not None:
            views = _inteiro(estatisticas.get("views"))
        avaliacao = comunidade.find("media:starRating", NS)
        if avaliacao is not None:
            media = _decimal(avaliacao.get("average"))
            quantos = _inteiro(avaliacao.get("count"))

    return Video(
        video_id=video_id,
        titulo=_texto(entrada, "a:title"),
        url=href or f"https://www.youtube.com/watch?v={video_id}",
        channel_id=_texto(entrada, "yt:channelId"),
        canal=_texto(entrada, "a:author/a:name") or feed.titulo,
        publicado=_texto(entrada, "a:published"),
        atualizado=_texto(entrada, "a:updated"),
        descricao=descricao,
        is_short="/shorts/" in href,
        views=views,
        rating_media=media,
        rating_n=quantos,
    )


def _inteiro(bruto: str | None) -> int:
    try:
        return int(bruto or 0)
    except (TypeError, ValueError):
        return 0


def _decimal(bruto: str | None) -> float:
    try:
        return float(bruto or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ cadência

_MAX_AGE = re.compile(r"max-age=(\d+)")


def proxima_em(cabecalhos: dict, piso: int) -> int:
    """Segundos até a próxima busca valer a pena, a partir dos headers de cache.

    `max(piso, max_age - age)`, e **`age` ausente vale 0**.

    O `age` diz quantos segundos a cópia da borda já tinha quando chegou até nós — e
    ele **falta em 2 dos 5 canais medidos**, porque nem toda resposta vem de cache de
    borda. Assumir `age=0` quando falta é a escolha conservadora: espera o `max-age`
    inteiro em vez de arriscar bater cedo e gastar uma requisição garantidamente morna.
    O erro é para o lado de gastar menos banda.
    """
    normalizados = {str(k).lower(): str(v) for k, v in (cabecalhos or {}).items()}
    achado = _MAX_AGE.search(normalizados.get("cache-control", ""))
    max_age = int(achado.group(1)) if achado else MAX_AGE_PADRAO
    age = _inteiro(normalizados.get("age"))
    return max(piso, max_age - age)
