#!/usr/bin/env python3
"""Rodar os testes deste projeto de todas as formas, com saída legível.

    ./testar.py                    a suíte inteira, módulo por módulo
    ./testar.py --help             todos os modos

Implementação do método de `discord-link-brain/docs/PADRAO_SAIDA_DE_TESTES.md`. O que
foi portado é o **método** — os cinco princípios, a gramática visual, os três desfechos,
as três armadilhas; o que é específico deste projeto (como invocar, como interpretar) foi
reescrito. Os modos `--cobertura` e `--docker` de lá **não** vieram: não existe imagem
`dev` aqui ainda, e modo que não pode rodar é flag para decorar.

O princípio que domina os outros: **saída que mente é pior que saída feia.** Zero teste
executado não é sucesso — é um terceiro desfecho, com nome, cor e código próprios.

**Sem dependência nova.** Cor é meia dúzia de escapes ANSI e caixa são cinco caracteres
de box-drawing. Um runner de teste não vai ser a porta de entrada de uma biblioteca de
terminal num projeto cujo `requirements.txt` tem duas linhas.

Equivalente cru de cada modo, para o runner não esconder a ferramenta de baixo:

    ./testar.py                →  python3 -m unittest tests.test_X   (um por módulo)
    ./testar.py --um-processo  →  python3 -m unittest discover -s tests -t .
    ./testar.py -m feed        →  python3 -m unittest tests.test_feed
    ./testar.py -t tests.test_feed.TestParse
                               →  python3 -m unittest tests.test_feed.TestParse -v
    ./testar.py --listar       →  loadTestsFromName(...).countTestCases()  (não roda)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
TESTES = RAIZ / "tests"

# ------------------------------------------------------------------ aparência


class Cor:
    """Escapes ANSI, ou string vazia quando não há terminal para colorir.

    Respeita `NO_COLOR` (convenção de fato) e `TERM=dumb`, e desliga sozinho quando a
    saída é redirecionada — senão o arquivo de log fica cheio de `\\x1b[32m`.
    """

    def __init__(self, ativo: bool):
        def faz(codigo: str) -> str:
            return f"\033[{codigo}m" if ativo else ""

        self.ativo = ativo
        self.zero = faz("0")
        self.forte = faz("1")
        self.fraco = faz("2")
        self.verde = faz("32")
        self.vermelho = faz("31")
        self.amarelo = faz("33")
        self.azul = faz("36")


def decidir_cor(forcar: str) -> bool:
    if forcar == "sempre":
        return True
    if forcar == "nunca":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def largura() -> int:
    """Largura útil, com teto: linha de 200 colunas cansa mais do que ajuda."""
    return min(shutil.get_terminal_size((80, 24)).columns, 100)


_ANSI = re.compile(r"\033\[[0-9;]*m")


def _vis(texto: str) -> int:
    """Largura visível: desconta os escapes ANSI, que não ocupam coluna.

    Usado em **todo** cálculo de padding. Alinhar com `len()` numa string colorida
    entorta a caixa inteira, e o defeito parece cosmético até alguém tentar ler a tela.
    """
    return len(_ANSI.sub("", texto))


class Tela:
    def __init__(self, cor: Cor):
        self.c = cor
        self.w = largura()

    def caixa(self, titulo: str, linhas: list[str], tom: str = "") -> None:
        c = self.c
        borda = tom or c.fraco
        print(f"{borda}╭{'─' * (self.w - 2)}╮{c.zero}")
        recuo = " " * max(0, self.w - 4 - _vis(titulo))
        print(f"{borda}│{c.zero} {c.forte}{titulo}{c.zero}{recuo} {borda}│{c.zero}")
        if linhas:
            print(f"{borda}├{'─' * (self.w - 2)}┤{c.zero}")
        for linha in linhas:
            recuo = " " * max(0, self.w - 4 - _vis(linha))
            print(f"{borda}│{c.zero} {linha}{recuo} {borda}│{c.zero}")
        print(f"{borda}╰{'─' * (self.w - 2)}╯{c.zero}")

    def regua(self, texto: str = "") -> None:
        c = self.c
        if not texto:
            print(f"{c.fraco}{'─' * self.w}{c.zero}")
            return
        # "── " + texto + " " + enfeite tem de somar `self.w` exatamente. Errar por um
        # deixa a régua estourando a caixa, e a tela parece torta sem motivo aparente.
        enfeite = "─" * max(0, self.w - _vis(texto) - 4)
        print(f"\n{c.fraco}──{c.zero} {c.forte}{texto}{c.zero} {c.fraco}{enfeite}{c.zero}")


# -------------------------------------------------------------------- modelo


@dataclass
class Resultado:
    nome: str
    total: int = 0
    falhas: int = 0
    erros: int = 0
    pulados: int = 0
    segundos: float = 0.0
    saida: str = ""
    quebrados: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.falhas == 0 and self.erros == 0

    @property
    def ruins(self) -> int:
        return self.falhas + self.erros


_RAN = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.M)
_CONTA = re.compile(r"(failures|errors|skipped)=(\d+)")
_QUEBRADO = re.compile(r"^(?:FAIL|ERROR): (\S+)", re.M)


def interpretar(nome: str, saida: str, segundos: float) -> Resultado:
    """Lê a saída do `unittest`. Formato estável há duas décadas.

    Regex em texto é a segunda escolha — dado estruturado seria melhor — mas o
    `unittest` da stdlib não emite JSON, e trazer um plugin para isso contradiz o
    princípio de zero dependência nova.
    """
    r = Resultado(nome=nome, segundos=segundos, saida=saida)
    achado = _RAN.search(saida)
    if achado:
        r.total = int(achado.group(1))
    campo = {"failures": "falhas", "errors": "erros", "skipped": "pulados"}
    for chave, valor in _CONTA.findall(saida):
        setattr(r, campo[chave], int(valor))
    r.quebrados = _QUEBRADO.findall(saida)
    return r


def rodar(argumentos: list[str], mostrar: bool = False) -> tuple[str, float, int]:
    """Invoca o `unittest` e **captura tudo**.

    Capturar tudo e cortar só na apresentação: passar a saída por `tail` para pegar o
    `Ran N tests` esconde justamente as linhas `FAIL:`/`ERROR:` que o desfecho precisa
    listar — armadilha 4.3 do padrão.
    """
    inicio = time.monotonic()
    processo = subprocess.run(
        [sys.executable, "-m", "unittest", *argumentos],
        cwd=str(RAIZ),
        capture_output=not mostrar,
        text=True,
        check=False,
    )
    saida = "" if mostrar else (processo.stdout or "") + (processo.stderr or "")
    return saida, time.monotonic() - inicio, processo.returncode


def modulos() -> list[str]:
    return sorted(p.stem for p in TESTES.glob("test_*.py"))


# -------------------------------------------------------------------- modos


def _provisorio(c: Cor, texto: str) -> None:
    """Linha que vai ser sobrescrita pelo resultado.

    `\\r` sozinho volta o cursor e **não apaga** o que estava lá — o resultado fica
    colado no "rodando…". `\\033[K` limpa até o fim da linha. E sem terminal não escreve
    nada: em arquivo de log, linha provisória é lixo duplicado.
    """
    if c.ativo:
        print(f"{texto}\033[K", end="\r", flush=True)


def _glifo(c: Cor, ok: bool) -> str:
    return f"{c.verde}✔{c.zero}" if ok else f"{c.vermelho}✘{c.zero}"


def _numeros(c: Cor, r: Resultado) -> str:
    """Glifo, nome, contagem, tempo — e só então o que é anormal. Ordem fixa."""
    partes = [f"{c.fraco}{r.total:>4} testes{c.zero}", f"{c.fraco}{r.segundos:>5.2f}s{c.zero}"]
    if r.pulados:
        partes.append(f"{c.amarelo}{r.pulados} pulado(s){c.zero}")
    if not r.ok:
        partes.append(f"{c.vermelho}{r.ruins} quebrado(s){c.zero}")
    return "  ".join(partes)


def modo_por_modulo(tela: Tela, alvos: list[str]) -> list[Resultado]:
    """Um processo por módulo, com a linha saindo assim que cada um termina.

    Isolamento por processo paga aqui: estes testes trocam globais de módulo por dublê
    (`rede.Cliente`, `subprocess.run` do `yt-dlp`), e um `tearDown` esquecido num
    arquivo apareceria como falha em outro — horas perdidas no arquivo errado.
    """
    c = tela.c
    resultados: list[Resultado] = []
    maior = max((len(a) for a in alvos), default=10)
    for alvo in alvos:
        _provisorio(c, f"  {c.fraco}·{c.zero} {alvo:<{maior}} {c.fraco}rodando…{c.zero}")
        saida, segundos, _ = rodar([f"tests.{alvo}"])
        r = interpretar(alvo, saida, segundos)
        resultados.append(r)
        print(f"  {_glifo(c, r.ok)} {alvo:<{maior}} {_numeros(c, r)}")
    return resultados


def modo_um_processo(tela: Tela) -> list[Resultado]:
    saida, segundos, _ = rodar(["discover", "-s", "tests", "-t", "."])
    r = interpretar("suíte inteira", saida, segundos)
    print(f"  {_glifo(tela.c, r.ok)} {r.total} testes em {r.segundos:.2f}s")
    return [r]


def modo_repetir(tela: Tela, vezes: int) -> list[Resultado]:
    """Roda a suíte N vezes e aponta quem falhou **em alguma** delas.

    Não é luxo: teste que falha uma vez em cinco não é azar, é ambiguidade — ele para
    de dizer se o código está certo.
    """
    c = tela.c
    resultados: list[Resultado] = []
    instaveis: dict[str, int] = {}
    for volta in range(1, vezes + 1):
        _provisorio(c, f"  {c.fraco}·{c.zero} volta {volta}/{vezes} {c.fraco}rodando…{c.zero}")
        saida, segundos, _ = rodar(["discover", "-s", "tests", "-t", "."])
        r = interpretar(f"volta {volta}", saida, segundos)
        resultados.append(r)
        for quebrado in r.quebrados:
            instaveis[quebrado] = instaveis.get(quebrado, 0) + 1
        print(f"  {_glifo(c, r.ok)} volta {volta}/{vezes}   {_numeros(c, r)}")
    if instaveis:
        tela.regua("quem falhou em alguma volta")
        for nome, quantas in sorted(instaveis.items(), key=lambda kv: -kv[1]):
            print(f"  {c.vermelho}✘{c.zero} {nome} {c.fraco}({quantas}/{vezes} voltas){c.zero}")
        print(
            f"\n  {c.amarelo}Teste que falha às vezes não é azar: é ambiguidade.{c.zero}\n"
            f"  {c.fraco}Ele para de dizer se o código está certo — conserte o teste "
            f"ou o código.{c.zero}"
        )
    return resultados


_CONTAR = (
    "import json, unittest;"
    "carregador = unittest.defaultTestLoader;"
    "print(json.dumps({n: carregador.loadTestsFromName('tests.' + n).countTestCases()"
    " for n in %r}))"
)


def modo_listar(tela: Tela) -> list[Resultado]:
    """Quantos testes cada arquivo tem, **sem rodar nada**.

    `countTestCases()` responde a mesma pergunta lendo os módulos, num processo só.
    Rodar a suíte inteira para produzir uma lista é o erro que o padrão nomeia.
    """
    c = tela.c
    tela.regua("módulos de teste")
    nomes = modulos()
    if not nomes:
        print(f"  {c.amarelo}nenhum tests/test_*.py{c.zero}")
        return []
    processo = subprocess.run(
        [sys.executable, "-c", _CONTAR % nomes],
        cwd=str(RAIZ), capture_output=True, text=True, check=False,
    )
    try:
        contagens = [(v, k) for k, v in json.loads(processo.stdout).items()]
    except (ValueError, TypeError):
        print(f"  {c.vermelho}não consegui contar:{c.zero} {processo.stderr.strip()[:300]}")
        return []
    maior_nome = max(len(n) for _, n in contagens)
    maior_total = max(t for t, _ in contagens) or 1
    for total, nome in sorted(contagens, reverse=True):
        celas = round(total / maior_total * 30)
        print(f"  {nome:<{maior_nome}}  {c.azul}{'▇' * celas}{c.zero} {total}")
    print(f"\n  {c.forte}{sum(t for t, _ in contagens)} testes{c.zero} em {len(contagens)} módulos")
    return []


# ------------------------------------------------------------------ desfecho


def _trecho_da_falha(saida: str, maximo: int = 14) -> list[str]:
    """As linhas que importam de um traceback: o nome, o arquivo e a asserção.

    O `unittest` imprime o traceback inteiro, e o meio dele quase nunca é o que explica
    — o que explica é a última linha e o `File` mais próximo do teste.
    """
    guardar: list[str] = []
    dentro = False
    for linha in saida.splitlines():
        if re.match(r"^(FAIL|ERROR): ", linha):
            dentro = True
            guardar.append(linha)
            continue
        if dentro:
            if linha.startswith("Ran ") or linha.startswith("OK"):
                dentro = False
                continue
            if linha.strip() and not linha.startswith("=") and not linha.startswith("-"):
                guardar.append("  " + linha.strip())
        if len(guardar) >= maximo:
            guardar.append("  …")
            break
    return guardar


def desfecho(tela: Tela, resultados: list[Resultado], segundos: float) -> int:
    """O bloco final. Tem de responder "passou?" a um metro de distância."""
    c = tela.c
    total = sum(r.total for r in resultados)
    ruins = sum(r.ruins for r in resultados)
    pulados = sum(r.pulados for r in resultados)
    quebrados = [q for r in resultados for q in r.quebrados]

    # Zero teste rodado **não é sucesso** — é o runner não tendo conseguido rodar a
    # suíte. Terceiro desfecho, com nome, cor e código próprios. É a regra que domina
    # todas as outras neste arquivo.
    #
    # `not resultados` entra no **mesmo** ramo, e essa linha custou uma tela verde
    # mentirosa: a versão portada guardava um `if not resultados: return 0` no topo,
    # herdado de um projeto onde sempre há 30 módulos. Aqui, com `tests/` ainda vazio,
    # `./testar.py` imprimiu régua, nada, e saiu **0**. Lista vazia de resultados é
    # justamente "não rodei nada" — o caso que este bloco existe para nomear.
    if not resultados or (total == 0 and not ruins):
        tela.caixa(
            "⚠  NADA RODOU",
            [
                f"{c.amarelo}nenhum teste foi executado{c.zero} em {segundos:.1f}s",
                (f"{c.fraco}não achei tests/test_*.py — nada foi coletado{c.zero}"
                 if not resultados else
                 f"{c.fraco}o comando não chegou a rodar a suíte — veja a saída acima{c.zero}"),
            ],
            tom=c.amarelo,
        )
        for r in resultados:
            for linha in (r.saida or "").strip().splitlines()[-6:]:
                # A saída vem de outro processo e pode trazer cor própria. Deixá-la
                # passar num modo `--cor nunca` contradiz a promessa da flag.
                limpa = linha if c.ativo else _ANSI.sub("", linha)
                print(f"  {c.fraco}{limpa}{c.zero}")
        return 2

    if ruins:
        tela.regua("o que quebrou")
        for r in resultados:
            if r.ok:
                continue
            for linha in _trecho_da_falha(r.saida):
                print(f"  {linha}")
        print()
        tela.caixa(
            "✘  FALHOU",
            [
                f"{c.vermelho}{ruins} de {total} testes quebrados{c.zero} em {segundos:.1f}s",
                *[f"{c.vermelho}·{c.zero} {q}" for q in quebrados[:12]],
                *([f"{c.fraco}… e mais {len(quebrados) - 12}{c.zero}"]
                  if len(quebrados) > 12 else []),
            ],
            tom=c.vermelho,
        )
        return 1

    detalhe = f"{c.verde}{total} testes, nenhuma falha{c.zero} · {segundos:.1f}s"
    if pulados:
        detalhe += f" · {c.amarelo}{pulados} pulado(s){c.zero}"
    tela.caixa("✔  PASSOU", [detalhe], tom=c.verde)
    return 0


# --------------------------------------------------------------------- main


def cabecalho(tela: Tela, modo: str) -> None:
    """Contexto: **o que eu rodei, exatamente?**

    Commit e aviso de árvore suja porque "passou no commit X" é falso se havia
    mudanças não commitadas — e essa é a frase que acaba num callback.
    """
    c = tela.c
    def git(*args: str) -> str:
        try:
            saida = subprocess.run(["git", *args], cwd=str(RAIZ),
                                   capture_output=True, text=True, check=False)
            return saida.stdout.strip()
        except OSError:
            return ""

    commit = git("rev-parse", "--short", "HEAD") or "?"
    sujo = git("status", "--porcelain")
    tela.caixa(
        "youtube-radar · testes",
        [
            f"{c.fraco}modo{c.zero}    {modo}",
            f"{c.fraco}commit{c.zero}  {commit}"
            + (f"  {c.amarelo}(árvore com mudanças){c.zero}" if sujo else ""),
            f"{c.fraco}python{c.zero}  {sys.version.split()[0]}  ·  "
            f"{len(modulos())} módulos de teste",
        ],
        tom=c.azul,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="./testar.py",
        description="Roda os testes do youtube-radar e mostra o resultado de forma legível.",
        epilog=(
            "exemplos:\n"
            "  ./testar.py                       a suíte inteira, módulo por módulo\n"
            "  ./testar.py --um-processo         o mais rápido (uma chamada só)\n"
            "  ./testar.py -m feed -m canal      só esses módulos\n"
            "  ./testar.py -t tests.test_feed.TestParse\n"
            "  ./testar.py --repetir 10          caça teste instável\n"
            "  ./testar.py --listar              quantos testes por arquivo\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-m", "--modulo", action="append", default=[], metavar="NOME",
                   help="um módulo (aceita `feed` ou `test_feed`, repetível)")
    p.add_argument("-t", "--teste", metavar="CAMINHO",
                   help="um teste ou classe: tests.test_feed.TestParse[.test_x]")
    p.add_argument("--um-processo", action="store_true",
                   help="a suíte numa chamada só — o mais rápido, sem isolamento")
    p.add_argument("--repetir", type=int, metavar="N",
                   help="roda a suíte N vezes e aponta os instáveis")
    p.add_argument("--listar", action="store_true",
                   help="só lista os módulos e quantos testes têm (não roda nada)")
    p.add_argument("--cor", choices=("auto", "sempre", "nunca"), default="auto")
    args = p.parse_args(argv)

    tela = Tela(Cor(decidir_cor(args.cor)))
    inicio = time.monotonic()
    resultados: list[Resultado] = []

    if args.listar:
        cabecalho(tela, "listar")
        modo_listar(tela)
        return 0

    if args.teste:
        cabecalho(tela, f"um teste · {args.teste}")
        tela.regua("saída do unittest, crua")
        _, segundos, codigo = rodar([args.teste, "-v"], mostrar=True)
        tela.caixa(
            "✔  PASSOU" if codigo == 0 else "✘  FALHOU",
            [f"{args.teste} · {segundos:.2f}s"],
            tom=tela.c.verde if codigo == 0 else tela.c.vermelho,
        )
        return codigo

    modo = (
        f"repetir {args.repetir}" if args.repetir else
        "um processo" if args.um_processo else
        "por módulo"
    )
    cabecalho(tela, modo)

    if args.modulo:
        alvos = []
        for bruto in args.modulo:
            nome = bruto if bruto.startswith("test_") else f"test_{bruto}"
            if nome not in modulos():
                print(f"  {tela.c.vermelho}não achei tests/{nome}.py{tela.c.zero}")
                print(f"  {tela.c.fraco}use --listar para ver os nomes{tela.c.zero}")
                return 2
            alvos.append(nome)
        tela.regua("módulos escolhidos")
        resultados += modo_por_modulo(tela, alvos)
    elif args.repetir:
        tela.regua(f"{args.repetir} voltas da suíte")
        resultados += modo_repetir(tela, args.repetir)
    elif args.um_processo:
        tela.regua("suíte inteira, um processo")
        resultados += modo_um_processo(tela)
    else:
        tela.regua("suíte inteira, módulo por módulo")
        resultados += modo_por_modulo(tela, modulos())

    print()
    return desfecho(tela, resultados, time.monotonic() - inicio)


if __name__ == "__main__":
    raise SystemExit(main())
