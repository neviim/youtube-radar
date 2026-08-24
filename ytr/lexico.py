"""Ranqueamento léxico: sobreposição de termos com IDF e peso por campo.

**Reescrito a partir do idioma de `discord-link-brain:dlb/busca.py`, não vendorado.**
O módulo de lá depende do `Config`, do `corpus` e do `sintese` daquele projeto; o que
transporta é o método — normalizar sem acento, tokenizar fora da stoplist, IDF barato
para o termo comum não dominar, peso por campo, casamento por prefixo para 4+ letras.

**Sem embeddings, e o gatilho para mudar é aritmético.** O corpus de gosto inteiro cabe
num prompt, e mandar tudo tem recall estritamente melhor que top-k por cosseno: não há
`k` para errar, nem limiar, nem chunk. O `perfil` imprime o tamanho **contado** do
corpus contra `YTR_CORPUS_MAX_CHARS` — quando passar, aí se discute vetor.
"""

from __future__ import annotations

import math
import re
import unicodedata

# PT-BR e EN juntos: os títulos do YouTube são metade em inglês, e uma stoplist só de
# português deixaria "the", "how" e "and" dominarem o IDF.
STOPWORDS = {
    "a", "à", "ao", "aos", "as", "às", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "eu", "foi", "há", "isso", "já", "la", "lo", "mais", "me", "meu",
    "minha", "muito", "na", "nas", "no", "nos", "num", "numa", "o", "os", "ou",
    "para", "pela", "pelo", "por", "que", "qual", "quais", "quando", "se", "sem",
    "ser", "seu", "sobre", "sua", "tem", "ter", "tinha", "um", "uma", "vi", "você",
    "é", "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "how", "what", "why", "this", "that", "it", "you", "your", "we", "i",
    "from", "at", "by", "be", "can", "do", "does", "video", "youtube",
}


def normalizar(texto: str) -> str:
    """Sem acento e em caixa baixa. Título em português vem dos dois jeitos."""
    sem_marca = "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    )
    return sem_marca.casefold()


def tokenizar(texto: str) -> list[str]:
    return [t for t in re.findall(r"\w+", normalizar(texto)) if t not in STOPWORDS and len(t) > 1]


class Indice:
    """IDF sobre um corpus de documentos. Construído uma vez, consultado muitas.

    O IDF não é enfeite: sem ele, "ia" — que aparece em 63 das 111 notas — pontuaria
    tanto quanto o termo que de fato distingue um vídeo dos outros.
    """

    def __init__(self, documentos: list[str]):
        self.total = max(1, len(documentos))
        self._normalizados = [normalizar(d) for d in documentos]
        self._idf: dict[str, float] = {}

    def idf(self, termo: str) -> float:
        if termo not in self._idf:
            df = sum(1 for d in self._normalizados if termo in d)
            self._idf[termo] = math.log(1 + self.total / (1 + df))
        return self._idf[termo]

    def pontuar(self, consulta: str, campos: dict[str, tuple[str, float]]) -> float:
        """`campos` é `nome -> (texto, peso)`. Devolve a soma ponderada por IDF.

        Casamento por prefixo para termo de 4+ letras: `agente` casa `agentes` sem
        precisar de stemmer, e sem o falso positivo que um prefixo de 2 letras traria.
        """
        termos = tokenizar(consulta)
        if not termos:
            return 0.0
        alvos = {nome: (normalizar(t), p) for nome, (t, p) in campos.items()}
        score = 0.0
        for termo in termos:
            peso_idf = self.idf(termo)
            for texto, peso in alvos.values():
                if not texto:
                    continue
                if termo in texto or (
                    len(termo) >= 4 and re.search(rf"\b{re.escape(termo)}", texto)
                ):
                    score += peso * peso_idf
        return score
