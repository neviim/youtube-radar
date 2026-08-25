"""O mapa `message_id → vídeo`, em disco.

**Por que ele é obrigatório, e por que uma mensagem por item não o dispensa.**

Quem publica é um processo (`digest`, 1× por dia; `ciclo`, de 15 em 15 minutos). Quem
lê a reação é outro, **minutos ou horas depois**. O leitor recebe um id de mensagem e
uma lista de reações; sem um mapa persistido, ele não tem como saber a que vídeo aquilo
se refere. A alternativa seria reler o texto da nossa própria mensagem e extrair a URL
— fazer parsing do que nós mesmos escrevemos, que é a definição de estado implícito, e
que quebra na primeira vez que alguém mexer no formato do aviso.

Uma mensagem por candidato resolve **outro** problema: a ambiguidade da reação humana
(um 👍 num digest com cinco vídeos não diz qual agradou). São dois problemas distintos,
e por isso as duas coisas entram.

Dois arquivos, duas naturezas:

- `avisos/AAAA-MM.jsonl` — append-only, escrito pelo `ciclo`. Só o mapa.
- `digests/AAAA-MM-DD.json` — um documento por dia, escrito pelo `digest`. Guarda
  **todos** os candidatos avaliados, incluindo os cortados e o porquê do corte. É o que
  faz o `doctor` responder "por que não recomendou X?" sem adivinhação.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .state import agora_utc, anexar_linha, escrever_atomico, ler_linhas

DECISOES = (
    "enviado",
    "cortado_por_teto",
    "cortado_por_liveness",
    "cortado_por_canal",
    "cortado_por_score",
    "cortado_por_marketing",
)


@dataclass
class ItemDeDigest:
    video_id: str
    channel_id: str = ""
    titulo: str = ""
    url: str = ""
    canal: str = ""
    score: float = 0.0
    componentes: dict = field(default_factory=dict)
    razao: str = ""
    liveness: str = "nao_verificado"
    decisao: str = "cortado_por_score"
    message_id: str = ""


@dataclass
class Digest:
    data: str
    gerado_em: str = ""
    backend_llm: str = "none"
    narracao: str = ""
    cabecalho_message_id: str = ""
    itens: list[ItemDeDigest] = field(default_factory=list)
    bytes_gastos: int = 0

    @property
    def enviados(self) -> list[ItemDeDigest]:
        return [i for i in self.itens if i.decisao == "enviado"]


def caminho_digest(state_dir: Path, dia: str) -> Path:
    return Path(state_dir) / "digests" / f"{dia}.json"


def salvar_digest(state_dir: Path, digest: Digest) -> Path:
    digest.gerado_em = digest.gerado_em or agora_utc()
    destino = caminho_digest(state_dir, digest.data)
    escrever_atomico(destino, json.dumps(asdict(digest), ensure_ascii=False, indent=2) + "\n")
    return destino


def carregar_digest(state_dir: Path, dia: str) -> Digest | None:
    caminho = caminho_digest(state_dir, dia)
    if not caminho.is_file():
        return None
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    itens = [ItemDeDigest(**i) for i in bruto.pop("itens", [])]
    return Digest(itens=itens, **bruto)


def digests_recentes(state_dir: Path, dias: int) -> list[Digest]:
    hoje = datetime.now(timezone.utc).date()
    saida = []
    for atras in range(dias + 1):
        dia = (hoje - timedelta(days=atras)).isoformat()
        achado = carregar_digest(state_dir, dia)
        if achado:
            saida.append(achado)
    return saida


# ------------------------------------------------------------------- avisos


def caminho_avisos(state_dir: Path, quando: date | None = None) -> Path:
    quando = quando or datetime.now(timezone.utc).date()
    return Path(state_dir) / "avisos" / f"{quando.strftime('%Y-%m')}.jsonl"


def registrar_aviso(state_dir: Path, message_id: str, video_id: str, channel_id: str) -> None:
    anexar_linha(
        caminho_avisos(state_dir),
        {
            "em": agora_utc(),
            "message_id": str(message_id),
            "video_id": video_id,
            "channel_id": channel_id,
        },
    )


def avisos_recentes(state_dir: Path, dias: int) -> list[dict]:
    """Os avisos dos últimos N dias, para reler as reações deles.

    Lê **dois** arquivos mensais e não um: uma janela de 7 dias que começa dia 28 pega
    o mês seguinte, e ler só o mês corrente perderia justamente os avisos mais velhos
    da janela — que são os que já tiveram tempo de receber reação.
    """
    hoje = datetime.now(timezone.utc).date()
    corte = datetime.now(timezone.utc) - timedelta(days=dias)
    arquivos = {caminho_avisos(state_dir, hoje), caminho_avisos(state_dir, hoje - timedelta(days=31))}
    saida = []
    for arquivo in arquivos:
        for registro in ler_linhas(arquivo):
            try:
                quando = datetime.fromisoformat(registro.get("em", ""))
            except ValueError:
                continue
            if quando >= corte:
                saida.append(registro)
    return sorted(saida, key=lambda r: r.get("em", ""))


# -------------------------------------------------------------------- pool 2


def caminho_pool2(state_dir: Path, quando: date | None = None) -> Path:
    quando = quando or datetime.now(timezone.utc).date()
    return Path(state_dir) / "pool2" / f"{quando.strftime('%Y-%m')}.jsonl"


def registrar_candidato_pool2(state_dir: Path, video, canal) -> None:
    """Um vídeo de canal monitorado suprimido do aviso individual (hoje, só Shorts)
    — persistido no instante da supressão, para concorrer no digest como Pool 2
    (§D7) em vez de simplesmente desaparecer. Não gasta rede: o ciclo já buscou o
    feed para chegar até aqui; isto só guarda o que ele já tinha em mãos.
    """
    anexar_linha(
        caminho_pool2(state_dir),
        {
            "em": agora_utc(),
            "video_id": video.video_id,
            "channel_id": video.channel_id,
            "handle": canal.handle or canal.channel_id,
            "titulo": video.titulo,
            "url": video.url,
            "descricao": video.descricao,
            "publicado": video.publicado,
            "views": video.views,
            "motivo": "short",
        },
    )


def candidatos_pool2_recentes(state_dir: Path, dias: int) -> list[dict]:
    """Os candidatos de Pool 2 dos últimos N dias, **um registro por vídeo**.

    Dedupa por `video_id` porque, embora marcar como visto já devesse impedir o
    mesmo Short de ser persistido duas vezes, dedupar na leitura é barato e fecha a
    porta se essa suposição estiver errada num canal específico.
    """
    hoje = datetime.now(timezone.utc).date()
    corte = datetime.now(timezone.utc) - timedelta(days=dias)
    arquivos = {caminho_pool2(state_dir, hoje), caminho_pool2(state_dir, hoje - timedelta(days=31))}
    por_video: dict[str, dict] = {}
    for arquivo in arquivos:
        for registro in ler_linhas(arquivo):
            try:
                quando = datetime.fromisoformat(registro.get("em", ""))
            except ValueError:
                continue
            if quando >= corte:
                por_video[registro.get("video_id", "")] = registro
    return sorted(por_video.values(), key=lambda r: r.get("em", ""))


# ---------------------------------------------------------------- cadastros


def caminho_cadastros(state_dir: Path) -> Path:
    return Path(state_dir) / "cadastros.jsonl"


def registrar_cadastro(state_dir: Path, channel_id: str, autor_id: str) -> None:
    """Quem cadastrou qual canal, pelo Discord — fora de `curadoria/canais.yaml` de
    propósito. Esse arquivo é versionado (decisão deliberada: é a curadoria dele,
    quer ficar visível e diffável); o id do Discord de quem postou o link não
    pertence a um histórico de git para sempre. Isto é derivado, não curadoria —
    perdê-lo não perde nenhum canal monitorado.
    """
    anexar_linha(
        caminho_cadastros(state_dir),
        {"em": agora_utc(), "channel_id": channel_id, "autor_id": autor_id},
    )


def cadastros(state_dir: Path) -> list[dict]:
    return ler_linhas(caminho_cadastros(state_dir))


# ------------------------------------------------------------------- sinais


def caminho_sinais(state_dir: Path) -> Path:
    return Path(state_dir) / "sinais.jsonl"


def sinais(state_dir: Path) -> list[dict]:
    return ler_linhas(caminho_sinais(state_dir))


def _chave(registro: dict) -> tuple:
    return (
        registro.get("message_id", ""),
        registro.get("user_id", ""),
        registro.get("reacao", ""),
        registro.get("video_id", ""),
    )


def registrar_sinal(state_dir: Path, registro: dict, existentes: set | None = None) -> bool:
    """Anexa um sinal, **idempotente por (mensagem, usuário, reação, vídeo)**.

    Idempotência importa porque a captura relê a mesma janela de 7 dias a cada ciclo:
    sem ela, um único 👍 viraria ~670 sinais por semana e o peso desse polegar
    dominaria o ranking inteiro.
    """
    registro.setdefault("em", agora_utc())
    if existentes is None:
        existentes = {_chave(s) for s in sinais(state_dir)}
    chave = _chave(registro)
    if chave in existentes:
        return False
    anexar_linha(caminho_sinais(state_dir), registro)
    existentes.add(chave)
    return True
