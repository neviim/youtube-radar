"""Montar o texto do aviso. **Determinístico, sem modelo.**

O caminho da notificação não tem LLM nenhum, e isso é uma decisão de disponibilidade,
não de economia: o login local do `claude` já é disputado por três consumidores do
projeto irmão, e o radar quer rodar de 15 em 15 minutos. Um aviso que depende de quota
é um aviso que some no dia em que a quota acaba.

**Toda mensagem carrega a procedência do resumo.** Se o texto é a descrição que o autor
escreveu, ele diz isso; se não havia descrição, ele diz que só tem o título. O sistema
nunca dá a entender que assistiu ao vídeo.
"""

from __future__ import annotations

import re

from .feed import Video

# Abaixo disto, a descrição não é resumo: é uma linha de divulgação.
MINIMO_DESCRICAO = 200

RESUMO_MAX = 400

# As descrições do YouTube são metade rodapé de patrocínio. Uma linha que é só link, ou
# só hashtag, ou "Inscreva-se em…" não acrescenta nada ao aviso.
_LINHA_LIXO = re.compile(
    r"^\s*(?:https?://\S+\s*)+$|^\s*(?:#\w+\s*)+$|"
    r"^\s*(?:subscribe|inscreva-se|follow us|siga)\b.*$",
    re.I,
)


def limpar_descricao(bruta: str) -> str:
    """Tira as linhas de rodapé e devolve o primeiro parágrafo útil."""
    linhas = [l for l in (bruta or "").splitlines() if not _LINHA_LIXO.match(l)]
    texto = "\n".join(linhas).strip()
    paragrafo = texto.split("\n\n")[0].strip() if texto else ""
    return re.sub(r"\s+", " ", paragrafo)


def truncar(texto: str, limite: int = RESUMO_MAX) -> str:
    """Corta em fronteira de frase quando dá, em fronteira de palavra quando não dá.

    Cortar no meio de uma palavra é o tipo de detalhe que faz a mensagem parecer
    quebrada em vez de resumida.
    """
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    janela = texto[: limite + 1]
    for fim in (". ", "! ", "? ", "; "):
        corte = janela.rfind(fim)
        if corte > limite // 2:
            return janela[: corte + 1].strip()
    corte = janela.rfind(" ")
    return (janela[:corte] if corte > limite // 2 else janela[:limite]).rstrip() + "…"


def resumo(video: Video) -> tuple[str, str]:
    """(texto, procedência). Procedência ∈ `descricao` | `so_titulo`."""
    limpa = limpar_descricao(video.descricao)
    if len(limpa) >= MINIMO_DESCRICAO:
        return truncar(limpa), "descricao"
    return "", "so_titulo"


def _engajamento(video: Video) -> str:
    partes = []
    if video.views:
        partes.append(f"{video.views:,} views".replace(",", "."))
    if video.rating_n:
        partes.append(f"★{video.rating_media:.1f} ({video.rating_n})")
    return " · ".join(partes)


def aviso(video: Video) -> str:
    """A mensagem de um vídeo novo num canal monitorado.

    O `video_id` aparece no texto de propósito: é o que permite, depois de um timeout
    ambíguo (o Discord aceitou o POST e o cliente não soube), procurar a mensagem nas
    últimas do canal antes de repostar. Sem ele, a recuperação teria de comparar
    títulos.

    A URL vai entre `<>` para suprimir a prévia. Um cartão seria útil, mas *Embed
    Links* não está no inteiro de permissões que o bot já tem, e trocar permissão por
    estética não paga.
    """
    texto, procedencia = resumo(video)
    linhas = [f"📡 **{video.titulo}**", f"_{video.canal}_"]

    if procedencia == "descricao":
        linhas += ["", texto, "", "_(resumo: a descrição escrita pelo canal)_"]
    else:
        linhas += ["", "_(sem descrição no feed — só o título; não li o conteúdo.)_"]

    engajamento = _engajamento(video)
    if engajamento:
        linhas.append(f"_{engajamento}_")

    linhas += ["", f"<{video.url}>", f"`{video.video_id}`"]
    return "\n".join(linhas)


def item_de_digest(video: Video, razao: str) -> str:
    """Uma recomendação, como **mensagem própria**.

    Mensagem por candidato, e não um digest único com 5 vídeos, porque a reação tem de
    ser inequívoca: um 👍 numa mensagem que contém cinco vídeos não diz qual agradou.
    Com uma mensagem por item, o 👍 mapeia para um `video_id` sem o Dotcom ter de
    decorar reação numerada e sem o bot pré-semear cinco emojis por digest.
    """
    linhas = [f"🎯 **{video.titulo}**", f"_{video.canal}_", "", f"_por quê:_ {razao}"]
    texto, procedencia = resumo(video)
    if procedencia == "descricao":
        linhas += ["", truncar(texto, 220)]
    linhas += ["", f"<{video.url}>", f"`{video.video_id}`", "", "👍 / 👎"]
    return "\n".join(linhas)


def cabecalho_de_digest(quantos: int, narracao: str, com_modelo: bool) -> str:
    linhas = [f"🎯 **{quantos} recomendação(ões) de hoje**"]
    if narracao:
        linhas += ["", narracao]
    if not com_modelo:
        linhas += ["", "_ranking sem modelo hoje — as regras decidiram sozinhas._"]
    linhas += ["", "_Reaja com 👍 ou 👎 **na mensagem de cada item**: é assim que o "
                   "radar aprende._"]
    return "\n".join(linhas)
