"""O perfil de gosto: o que ele já salvou, lido do vault — **somente leitura**.

O radar **nunca escreve no vault**, e isso é decisão, não omissão. Uma nota por vídeo
novo poria ~20 notas por semana em `50_LINKS/` que ele não pediu, afogando as 111 que
ele curou — e o deduplicador do projeto irmão passaria a ver as nossas como links já
arquivados. A costura limpa: **nós avisamos; se ele quiser guardar, ele posta o link e
o outro projeto arquiva.** Efeito colateral bom: o container monta o vault `:ro` de
verdade.

**O que o corpus mede, e o que ele não mede.** São 111 notas, todas de uma pessoa
(`Jads`), todas com `triagem: inbox` — ninguém triou nada, então "ele salvou" é o único
sinal positivo que existe, e é fraco. Das 111, **86 têm a seção "Por que importa"**:
texto curto em PT-BR dizendo o valor, escrito por ele. É o melhor material de perfil
aqui — melhor que as tags.

E o número que decide o desenho: as 77 URLs de vídeo resolvem para **60 canais únicos,
53 deles com um vídeo só**. Por isso a afinidade de canal é um sinal de cauda longa, e
não uma lista de favoritos.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .lexico import Indice

LINKS_DIR = "50_LINKS"

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
PORQUE = re.compile(r"^## Por que importa\n\n(?P<texto>.+?)(?:\n\n|\Z)", re.MULTILINE | re.DOTALL)
RESUMO = re.compile(r"^> (?P<texto>.+)$", re.MULTILINE)
URL_YOUTUBE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/\S+")


@dataclass
class NotaDeLink:
    caminho: Path
    url: str = ""
    titulo: str = ""
    dia: str = ""
    categoria: str = ""
    tags: list[str] = field(default_factory=list)
    resumo: str = ""
    porque_importa: str = ""

    @property
    def texto_de_perfil(self) -> str:
        return " ".join(p for p in (self.titulo, self.resumo, self.porque_importa) if p)


def _frontmatter(bruto: str) -> dict:
    """Parser mínimo do frontmatter: só `chave: valor` e lista com `- `.

    Deliberadamente burro em vez de chamar o YAML: o frontmatter do vault tem
    wikilinks (`- "[[MOC - Captura Discord]]"`) e datas soltas, e um parser completo
    aqui traria os erros do YAML para um caminho que só precisa de quatro campos. O que
    ele não entender, ele ignora — nunca levanta.
    """
    dados: dict = {}
    chave_atual = ""
    for linha in bruto.splitlines():
        if linha.startswith("  - ") or linha.startswith("- "):
            if chave_atual:
                dados.setdefault(chave_atual, []).append(
                    linha.split("- ", 1)[1].strip().strip('"').strip("'")
                )
            continue
        if ":" not in linha:
            continue
        chave, _, valor = linha.partition(":")
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")
        chave_atual = chave if not valor else ""
        if valor:
            dados[chave] = valor
    return dados


def ler_nota(caminho: Path) -> NotaDeLink | None:
    try:
        bruto = caminho.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    achado = FRONTMATTER.match(bruto)
    if not achado:
        return None
    meta = _frontmatter(achado.group(1))
    corpo = bruto[achado.end():]

    resumo = RESUMO.search(corpo)
    porque = PORQUE.search(corpo)
    tags = meta.get("tags") or []
    return NotaDeLink(
        caminho=caminho,
        url=str(meta.get("url") or ""),
        titulo=caminho.stem,
        dia=str(meta.get("capturado") or meta.get("criado") or ""),
        categoria=str(meta.get("categoria") or ""),
        tags=[t for t in tags if isinstance(t, str)],
        resumo=(resumo.group("texto").strip() if resumo else ""),
        porque_importa=(porque.group("texto").strip() if porque else ""),
    )


@dataclass
class Perfil:
    """O gosto dele, como dado. Construído do vault mais os sinais capturados."""

    notas: list[NotaDeLink] = field(default_factory=list)
    afinidade_canal: Counter = field(default_factory=Counter)
    afinidade_tag: Counter = field(default_factory=Counter)
    polegar_baixo_canal: Counter = field(default_factory=Counter)
    polegar_cima_canal: Counter = field(default_factory=Counter)
    urls_conhecidas: set = field(default_factory=set)
    corpus_chars: int = 0
    leitura_ms: float = 0.0
    indice: Indice | None = None

    @property
    def canais_do_pool(self) -> list[str]:
        """Os handles que ele demonstrou interesse, do mais ao menos frequente."""
        return [handle for handle, _ in self.afinidade_canal.most_common()]

    def pontuar_afinidade(self, cfg, channel_id: str, handle: str, tags_do_canal=()) -> dict:
        """Componentes de afinidade que não dependem do vídeo em si."""
        chave = (handle or channel_id or "").lstrip("@").casefold()
        canal = self.afinidade_canal.get(chave, 0) * cfg.peso_canal
        tag = sum(self.afinidade_tag.get(t, 0) for t in tags_do_canal) * cfg.peso_tag
        baixo = self.polegar_baixo_canal.get(chave, 0) * cfg.peso_polegar_baixo
        return {"canal": round(canal, 3), "tag": round(tag, 3), "polegar_baixo": round(-baixo, 3)}


def carregar(cfg, sinais: list[dict] | None = None, mapa_canal: dict | None = None) -> Perfil:
    """Lê `50_LINKS/` e os sinais capturados. **Nunca escreve.**

    `mapa_canal` é `url_do_video -> handle`, resolvido por oEmbed e guardado em cache
    por quem chamar. Sem ele, a afinidade de canal fica vazia — o vault guarda a URL do
    vídeo, não a do canal, e resolver 72 URLs custa 27 segundos de rede que não cabem
    dentro de um ciclo de 15 minutos.
    """
    inicio = time.monotonic()
    pasta = Path(cfg.vault_path) / LINKS_DIR
    perfil = Perfil()

    for arquivo in sorted(pasta.glob("*.md")) if pasta.is_dir() else []:
        nota = ler_nota(arquivo)
        if nota is None:
            continue
        perfil.notas.append(nota)
        perfil.corpus_chars += len(nota.texto_de_perfil)
        for tag in nota.tags:
            perfil.afinidade_tag[tag] += 1
        if nota.url and URL_YOUTUBE.match(nota.url):
            perfil.urls_conhecidas.add(nota.url)
            handle = (mapa_canal or {}).get(nota.url, "")
            if handle:
                perfil.afinidade_canal[handle.lstrip("@").casefold()] += 1

    for sinal in sinais or []:
        chave = (sinal.get("handle") or sinal.get("channel_id") or "").lstrip("@").casefold()
        if not chave:
            continue
        if sinal.get("reacao") == "👎":
            perfil.polegar_baixo_canal[chave] += 1
        elif sinal.get("reacao") == "👍":
            perfil.polegar_cima_canal[chave] += 1
            perfil.afinidade_canal[chave] += 1

    perfil.indice = Indice([n.texto_de_perfil for n in perfil.notas])
    perfil.leitura_ms = (time.monotonic() - inicio) * 1000
    return perfil
