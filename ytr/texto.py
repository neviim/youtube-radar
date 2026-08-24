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


def _frase(video: Video) -> str:
    """Título, canal e um contexto de **uma frase só** — nunca sem a procedência.

    Pedido do Dotcom (2026-08-24): mensagem simples, uma frase no máximo, e nada que
    pareça proposta de venda (isso é filtro de conteúdo, em `ytr.pool`, não aqui — este
    módulo só formata o que já foi aprovado). A invariante de honestidade continua:
    se o feed não trouxe descrição, a frase diz isso em vez de inventar contexto a
    partir do título.
    """
    texto, procedencia = resumo(video)
    if procedencia == "descricao":
        contexto = texto.split(". ", 1)[0].rstrip()
        if not contexto.endswith((".", "…", "!", "?")):
            contexto += "."
    else:
        contexto = "sem descrição no feed — não li o conteúdo, só o título."
    return f"**{video.titulo}** _({video.canal})_ — {contexto}"


def aviso(video: Video) -> str:
    """A mensagem de um vídeo novo num canal monitorado: uma frase e o link.

    O link vai solto, sem `<>`: com a permissão *Embed Links* que o bot passou a ter,
    o Discord mostra o card de prévia — link "de verdade", fácil de reconhecer e
    repostar, era o próprio pedido. `video_id` não precisa de linha própria: já está
    dentro da URL (`?v=<id>`), então a recuperação depois de um POST ambíguo (buscar a
    mensagem nas últimas do canal) ainda encontra por substring.
    """
    return f"🎬 {_frase(video)} {video.url}"


def item_de_digest(video: Video, razao: str) -> str:
    """Uma recomendação, como **mensagem própria** — mesma frase única do aviso, mais o
    porquê e o par de reações que fecha o laço de aprendizado (Fase 7).

    Mensagem por candidato, e não um digest único com vários vídeos, porque a reação
    tem de ser inequívoca: um 👍 numa mensagem com cinco vídeos não diz qual agradou.
    """
    return f"🎯 {_frase(video)} _{razao}._ {video.url}\n👍 / 👎"


def cabecalho_de_digest(quantos: int, narracao: str, com_modelo: bool) -> str:
    linhas = [f"🎯 **{quantos} recomendação(ões) de hoje**"]
    if narracao:
        linhas += ["", narracao]
    if not com_modelo:
        linhas += ["", "_ranking sem modelo hoje — as regras decidiram sozinhas._"]
    linhas += ["", "_Reaja com 👍 ou 👎 **na mensagem de cada item**: é assim que o "
                   "radar aprende._"]
    return "\n".join(linhas)
