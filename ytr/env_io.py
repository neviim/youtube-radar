# ---------------------------------------------------------------------------
# VENDORADO de discord-link-brain:dlb/env_io.py
#   commit de origem: 4674e72c1892e944237937bf86865e57d445aa18
#   copiado: 2026-08-24 · verbatim, sem adaptação
# Regra: conserta em cima (no projeto de origem), depois re-vendora. Ver
# docs/VENDORADO.md.
# ---------------------------------------------------------------------------
"""Leitura do `.env` como dado, com as mesmas regras que o `load_dotenv` do projeto.

O painel precisa do arquivo, não do ambiente do processo: `Config.from_env` usa
`os.environ.setdefault`, então o que ele vê é uma mistura de arquivo e de tudo que
já estava exportado no shell. Para a tela de agenda dizer "o arquivo diz X", ela
tem de ler o arquivo.

Duas regras não óbvias, e as duas são **cópia deliberada** de `config.load_dotenv`:

1. **A primeira ocorrência ganha.** `setdefault` não sobrescreve, então uma chave
   repetida vale pela primeira aparição — e é exatamente o defeito que estava no
   `.env.example` com `DISCORD_POST_ENABLED` em `1` na linha 15 e `0` na 118. Um
   leitor que resolvesse "a última ganha" mostraria um valor que o sistema não usa.
2. **Aspas são removidas nas pontas, sem interpretar escape.** O shell do compose
   e o `load_dotenv` fazem isso; um parser mais esperto aqui divergiria do que
   realmente chega ao processo.

A escrita preserva o arquivo inteiro: comentário, ordem, linha em branco. Um
`.env` que perde os comentários perde a documentação que ele carrega, e ninguém
percebe até precisar dela.

**Uma regra de formato que não é óbvia:** o parser trata tudo depois do `=` como
valor, então `CHAVE=1  # comentário` guarda `1  # comentário`. Comentário na mesma
linha não existe neste formato, e a escrita nunca cria um — inclusive ao ativar uma
linha sugerida como `# DLB_SYNC_AT=03:30   # HH:MM`, cujo comentário é descartado
do valor.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Linha:
    """Uma linha do arquivo, preservada como está. Base para a escrita futura."""

    numero: int
    bruta: str
    chave: str = ""
    valor: str = ""
    comentada: bool = False

    @property
    def e_atribuicao(self) -> bool:
        return bool(self.chave) and not self.comentada


def linhas(caminho: Path) -> list[Linha]:
    """Todas as linhas classificadas, na ordem do arquivo.

    Preserva comentário e linha em branco porque a tela de configuração vai
    reescrever o arquivo mantendo tudo isso — um `.env` que perde os comentários
    perde a documentação que ele mesmo carrega.
    """
    try:
        texto = Path(caminho).read_text(encoding="utf-8")
    except OSError:
        return []
    saida: list[Linha] = []
    for numero, bruta in enumerate(texto.splitlines(), start=1):
        limpa = bruta.strip()
        if not limpa:
            saida.append(Linha(numero, bruta))
            continue
        if limpa.startswith("#"):
            corpo = limpa.lstrip("#").strip()
            chave, sep, valor = corpo.partition("=")
            saida.append(
                Linha(
                    numero,
                    bruta,
                    chave=chave.strip() if sep and chave.strip().isupper() else "",
                    valor=_limpar(valor) if sep else "",
                    comentada=True,
                )
            )
            continue
        if "=" not in limpa:
            saida.append(Linha(numero, bruta))
            continue
        chave, _, valor = limpa.partition("=")
        saida.append(Linha(numero, bruta, chave=chave.strip(), valor=_limpar(valor)))
    return saida


def _limpar(valor: str) -> str:
    return valor.strip().strip('"').strip("'")


def ler(caminho: Path) -> dict[str, str]:
    """`chave -> valor` do arquivo, com a **primeira** ocorrência valendo.

    Igual ao `load_dotenv`: quem usa `setdefault` faz a primeira ganhar. Ver o
    docstring do módulo.
    """
    valores: dict[str, str] = {}
    for linha in linhas(caminho):
        if linha.e_atribuicao and linha.chave not in valores:
            valores[linha.chave] = linha.valor
    return valores


def ambiente_limpo(caminho: Path) -> dict[str, str]:
    """O ambiente de um subprocess, **sem** as chaves que o `.env` define.

    Mora aqui, e não no `compose`, porque não é sobre docker: é sobre a regra de
    precedência deste módulo vista do outro lado. `Config.from_env()` chama
    `load_dotenv()`, que faz `os.environ.setdefault(...)` — então **todo processo
    longo do projeto carrega em si os valores que o arquivo tinha quando subiu**.
    O painel é o único processo longo, e é justamente quem dispara filhos.

    Dois defeitos medidos ao vivo, com a mesma causa e sintomas opostos:

    - **o `compose`** recriava o container com o valor de quando o painel subiu,
      porque a interpolação prefere variável de shell ao arquivo. Código 0, tela
      dizendo "recriado", faixa de pendente teimando;
    - **o comando disparado da tela** recusava com "DISCORD_TOKEN não definido"
      depois de o token ser salvo, porque o painel subiu com `DISCORD_TOKEN=`
      (vazio, como vem do `.env.example`) e `setdefault` não sobrescreve string
      vazia. Num clone novo isso fecha o ciclo inteiro: a tela existe para
      preencher a configuração, e o filho continuava vendo a de antes.

    A limpeza é cirúrgica: sai só o que o arquivo define, para `PATH`, `HOME` e o
    resto de que o filho precisa continuarem valendo.
    """
    ambiente = dict(os.environ)
    for chave in ler(caminho):
        ambiente.pop(chave, None)
    return ambiente


def duplicadas(caminho: Path) -> dict[str, list[int]]:
    """Chaves atribuídas mais de uma vez, com as linhas — para a tela avisar.

    Não é hipótese: aconteceu neste repo, e o valor que vale é o da primeira
    linha, que não é o que a segunda (com comentário explicativo) sugere.
    """
    onde: dict[str, list[int]] = {}
    for linha in linhas(caminho):
        if linha.e_atribuicao:
            onde.setdefault(linha.chave, []).append(linha.numero)
    return {chave: nums for chave, nums in onde.items() if len(nums) > 1}


# --------------------------------------------------------------------- escrita


@dataclass
class Resultado:
    """O que a escrita fez. A tela mostra, e o rollback usa."""

    mudadas: dict[str, str] = field(default_factory=dict)
    acrescentadas: tuple[str, ...] = ()
    backup: Path | None = None
    iguais: tuple[str, ...] = ()


def _formatar(chave: str, valor: str) -> str:
    """`CHAVE=valor`, com aspas só quando o espaço nas pontas importa.

    O `load_dotenv` faz `.strip()` no valor, então espaço solto se perde de
    qualquer jeito; aspas são a única forma de preservá-lo, e escrever aspas
    sempre poluiria o arquivo à mão de quem edita direto.
    """
    if valor != valor.strip():
        return f'{chave}="{valor}"'
    return f"{chave}={valor}"


def escrever(caminho: Path, valores: dict[str, str], backup: bool = True) -> Resultado:
    """Aplica `valores` no `.env`, atômico, com backup.

    Ordem das operações, e ela é a diferença entre um arquivo consistente e um
    `.env` que tranca o dono fora do próprio sistema:

    1. lê o arquivo inteiro;
    2. monta o texto novo **em memória**;
    3. copia o original para `.env.bak` com modo `0600`;
    4. escreve num temporário no mesmo diretório, também `0600`;
    5. `os.replace`, que é atômico no mesmo sistema de arquivos.

    Chave que já existe é substituída no lugar, preservando a posição e os
    comentários em volta. Chave que só existe comentada ganha uma linha ativa
    logo **depois** da sugestão — a documentação fica. Chave nova vai para o fim.
    """
    caminho = Path(caminho)
    original = caminho.read_text(encoding="utf-8") if caminho.is_file() else ""
    linhas_atuais = original.splitlines()
    atuais = ler(caminho)

    # Ausente e vazio contam como iguais, e isto não é detalhe: o formulário posta
    # as 55 chaves de uma vez, a maioria em branco porque nunca foi configurada.
    # Sem esta linha, salvar qualquer coisa acrescentaria cinquenta linhas
    # `CHAVE=` ao `.env`, de uma vez.
    #
    # O que se perde: não dá para criar, pela tela, uma linha vazia de chave que
    # ainda não existe no arquivo. O caso que **importa** — esvaziar
    # `DLB_SYNC_EVERY_HOURS` para liberar o `DLB_SYNC_AT` — continua funcionando,
    # porque essa chave já está lá com valor.
    pendentes = dict(valores)
    iguais = tuple(sorted(k for k, v in valores.items() if atuais.get(k, "") == v))
    for k in iguais:
        pendentes.pop(k, None)

    saida: list[str] = []
    ja_escritas: set[str] = set()
    for linha in linhas_atuais:
        limpa = linha.strip()
        if limpa and not limpa.startswith("#") and "=" in limpa:
            chave = limpa.partition("=")[0].strip()
            if chave in pendentes and chave not in ja_escritas:
                saida.append(_formatar(chave, pendentes[chave]))
                ja_escritas.add(chave)
                continue
            if chave in ja_escritas:
                # Chave repetida: a primeira é a que vale (`setdefault`), então a
                # segunda é ruído que confunde. Sai junto com a atualização.
                continue
        saida.append(linha)
        if limpa.startswith("#") and "=" in limpa:
            sugerida = limpa.lstrip("#").strip().partition("=")[0].strip()
            if sugerida in pendentes and sugerida not in ja_escritas:
                saida.append(_formatar(sugerida, pendentes[sugerida]))
                ja_escritas.add(sugerida)

    novas = tuple(k for k in pendentes if k not in ja_escritas)
    if novas:
        if saida and saida[-1].strip():
            saida.append("")
        for chave in novas:
            saida.append(_formatar(chave, pendentes[chave]))

    texto = "\n".join(saida) + "\n"
    resultado = Resultado(mudadas=dict(pendentes), acrescentadas=novas, iguais=iguais)

    if not pendentes:
        return resultado          # nada a fazer: não mexe no arquivo nem faz backup

    if backup and caminho.is_file():
        destino = caminho.with_suffix(caminho.suffix + ".bak")
        destino.write_text(original, encoding="utf-8")
        os.chmod(destino, 0o600)
        resultado.backup = destino

    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_text(texto, encoding="utf-8")
    os.chmod(temporario, 0o600)
    os.replace(temporario, caminho)
    return resultado


def restaurar(caminho: Path) -> bool:
    """Volta o `.env` para o backup, **byte a byte**. `False` se não há backup.

    Byte a byte importa: um rollback que reformatasse o arquivo não seria
    rollback, seria uma terceira versão.
    """
    caminho = Path(caminho)
    origem = caminho.with_suffix(caminho.suffix + ".bak")
    if not origem.is_file():
        return False
    dados = origem.read_bytes()
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_bytes(dados)
    os.chmod(temporario, 0o600)
    os.replace(temporario, caminho)
    return True


def tem_backup(caminho: Path) -> Path | None:
    destino = Path(caminho).with_suffix(Path(caminho).suffix + ".bak")
    return destino if destino.is_file() else None
