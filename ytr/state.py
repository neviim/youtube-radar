"""Estado local em arquivo, atômico, com **um escritor por arquivo**.

Idioma adaptado de `discord-link-brain:dlb/state.py` — a estrutura de dados aqui é
outra, mas as duas regras que custaram caro lá transportam inteiras:

1. **Escrita atômica** (`tmp` + `os.replace`). `write_text` direto deixa uma janela em
   que o arquivo existe truncado. O `carregar` tolera JSON corrompido e devolve estado
   vazio — o que aqui significaria **avisar os 15 vídeos do feed de novo**.
2. **Nenhum arquivo com dois escritores.** O cursor de cada canal mora no arquivo
   *daquele* canal, e não num JSON único, porque 20 canais buscados em paralelo seriam
   20 escritores no mesmo arquivo: o read-modify-write concorrente clássico.

Onde há mais de um **comando** que escreve o mesmo arquivo (`canais.yaml` é escrito
pelo `ciclo` e pelo `canais desativar`), a exclusão vem do `flock` de `ytr/trava.py`,
não do desenho de arquivo — e isso é invariante com teste, não convenção.

**Aviso de NFS:** `flock` não vale em sistema de arquivos de rede. Se `.state` for
montado por NFS, a garantia de escritor único cai.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def agora_utc() -> str:
    """Carimbo interno, sempre UTC ISO-8601. Fuso local só na apresentação."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def escrever_atomico(destino: Path, texto: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".tmp")
    temporario.write_text(texto, encoding="utf-8")
    os.replace(temporario, destino)


def anexar_linha(destino: Path, registro: dict) -> None:
    """Append de uma linha JSONL.

    Append é seguro **porque há um escritor só** — a mesma regra que fez o projeto
    irmão recusar um `runs.jsonl` com três escritores. A regra é "um escritor", não
    "nunca JSONL".
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")


def ler_linhas(origem: Path) -> list[dict]:
    """Lê um JSONL, pulando linha corrompida em vez de morrer.

    Linha meio escrita só pode existir se o processo morreu no meio de um append; o
    resto do arquivo continua bom, e perder o histórico inteiro por causa dela seria
    pior que perder a linha.
    """
    if not origem.is_file():
        return []
    saida = []
    for linha in origem.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            saida.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return saida


@dataclass
class EstadoCanal:
    """O que o radar sabe sobre um canal entre um ciclo e o outro.

    `avisados` é uma **lista de ids**, e é ela — não o carimbo de tempo — que impede
    aviso repetido. Motivo medido: no feed real, `updated` chega 15 minutos depois de
    `published` no mesmo item, então data se move e id não.
    """

    channel_id: str = ""
    avisados: list[str] = field(default_factory=list)
    ultima_busca: str | None = None
    proxima_busca: str | None = None
    ultimo_publicado: str | None = None
    falhas: int = 0
    ultimo_erro: str = ""
    semeado: bool = False
    bytes_ultimo_ciclo: int = 0

    def lembrar(self, video_id: str, teto: int) -> None:
        if video_id in self.avisados:
            return
        self.avisados.append(video_id)
        if len(self.avisados) > teto:
            del self.avisados[: len(self.avisados) - teto]

    def ja_avisou(self, video_id: str) -> bool:
        return video_id in self.avisados


class Estado:
    """Cursores em disco, **um arquivo por canal**."""

    def __init__(self, diretorio: Path):
        self.diretorio = Path(diretorio)

    def _caminho(self, channel_id: str) -> Path:
        return self.diretorio / "visto" / f"{channel_id}.json"

    def carregar(self, channel_id: str) -> EstadoCanal:
        caminho = self._caminho(channel_id)
        if not caminho.is_file():
            return EstadoCanal(channel_id=channel_id)
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            return EstadoCanal(**dados)
        except (json.JSONDecodeError, TypeError, OSError):
            # Estado ilegível é estado ausente. O preço é reavisar, e é por isso que
            # a escrita é atômica: para esta janela não existir na prática.
            return EstadoCanal(channel_id=channel_id)

    def salvar(self, estado: EstadoCanal) -> None:
        estado.ultima_busca = agora_utc()
        escrever_atomico(
            self._caminho(estado.channel_id),
            json.dumps(asdict(estado), ensure_ascii=False, indent=2) + "\n",
        )

    def todos(self) -> list[EstadoCanal]:
        pasta = self.diretorio / "visto"
        if not pasta.is_dir():
            return []
        return [self.carregar(p.stem) for p in sorted(pasta.glob("*.json"))]


# ------------------------------------------------------------------------ saúde


@dataclass
class Saude:
    """Heartbeat e a trava que impede spam quando o estado quebra.

    `postagem_bloqueada` existe por um modo de falha específico e nada teórico: se um
    POST no Discord der certo e o save do `avisados` falhar logo depois (disco cheio,
    `.state` remontado read-only), o ciclo seguinte lê o estado antigo, não vê o vídeo
    como avisado, e posta de novo — **a cada 15 minutos, para sempre**. A entrega é
    at-least-once por escolha; "at-least-once" não pode virar "infinitas vezes".
    """

    heartbeat: str = ""
    postagem_bloqueada: bool = False
    motivo: str = ""
    ciclos_com_falha_total: int = 0

    @classmethod
    def carregar(cls, diretorio: Path) -> "Saude":
        caminho = Path(diretorio) / "saude.json"
        if not caminho.is_file():
            return cls()
        try:
            return cls(**json.loads(caminho.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, OSError):
            return cls()

    def salvar(self, diretorio: Path) -> None:
        escrever_atomico(
            Path(diretorio) / "saude.json",
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
        )


class EstadoNaoGravavel(RuntimeError):
    """`.state` não aceita escrita. Levantado **antes** do primeiro POST."""


def preflight(diretorio: Path) -> None:
    """Prova que `.state` aceita escrita, antes de qualquer mensagem sair.

    Escreve, relê e apaga uma sonda. Sem isto, um `.state` read-only deixa o sistema
    postar com sucesso e falhar ao marcar, indefinidamente — que é exatamente o cenário
    que a barreira de `Saude.postagem_bloqueada` cobre depois, e que este preflight
    evita de acontecer uma primeira vez.
    """
    diretorio = Path(diretorio)
    sonda = diretorio / ".sonda"
    marca = agora_utc()
    try:
        escrever_atomico(sonda, marca)
        if sonda.read_text(encoding="utf-8") != marca:
            raise EstadoNaoGravavel(f"{diretorio} não devolveu o que foi escrito.")
        sonda.unlink()
    except OSError as erro:
        raise EstadoNaoGravavel(
            f"não consigo escrever em {diretorio}: {erro}. "
            "O ciclo recusa postar sem estado gravável — senão o mesmo vídeo é avisado "
            "a cada ciclo, para sempre."
        ) from erro
