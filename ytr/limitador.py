"""Freio de circuito contra o YouTube.

**O que motivou isto.** Medido ao vivo (Fase 9): depois de um dia inteiro de testes
— dezenas de resoluções de canal a ~1,5 MB cada, mais o `ciclo` e o `digest`
rodando várias vezes — os três canais que sempre funcionaram passaram a responder
HTTP 404 para tudo, **inclusive um `channel_id` nunca consultado antes, direto via
`curl` do host**. Não é falha de canal: é o YouTube reagindo ao volume.

**Por que isto não é o recuo por canal (`ciclo.RECUO_POR_FALHA`).** Aquele decide
por canal e continua existindo — é o que evita um canal morto virar spam eterno.
Mas ele não enxerga o quadro geral: no primeiro ciclo de um bloqueio global, cada
canal ainda bate no YouTube uma vez antes de qualquer recuo aparecer, e nada impede
o `digest` (que fala com o YouTube por um caminho totalmente diferente — a página
`/@handle`, não o RSS) de bater justo durante essa mesma janela. Este módulo é o
sinal **global**, persistido, que os dois consultam antes de fazer qualquer
requisição.

**Arquivado, não perdido.** Com o freio aberto, `ciclo` pula a busca do ciclo
inteiro — nenhum canal marca falha nova, nenhum `proxima_busca` avança. Cada canal
continua exatamente onde estava, pronto para ser buscado assim que o freio fechar.
Não existe fila separada porque não precisa: o próprio agendamento por canal já
cumpre esse papel.

**Recuperação é uma tentativa, não um relógio.** Quando o tempo de espera passa, a
*próxima* chamada tenta de verdade — uma sonda. Se der certo, o freio fecha; se
continuar ruim, escala para o próximo degrau. Isto é **reativo, não uma cota
imposta**: o YouTube não documenta o limite real, então não há como aplicar um teto
proativo honesto — só reconhecer a degradação e recuar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .state import escrever_atomico

# Mesma escada do recuo por canal — não duplico a tunagem, só reaproveito:
# 15 → 30 → 60 → 120 min, teto 6h.
RECUO_GLOBAL = (900, 1800, 3600, 7200, 21600)

# Ciclos consecutivos com **todos** os canais falhando antes de abrir o freio. Um
# só pode ser coincidência — hoje há só 3 canais reais monitorados, e três canais
# ruins ao mesmo tempo não é impossível. Dois seguidos é o sinal de bloqueio.
LIMIAR_CICLOS_RUINS = 2


def _caminho(state_dir) -> Path:
    return Path(state_dir) / "limitador.json"


@dataclass
class Freio:
    nivel: int = 0
    aberto_ate: str = ""
    motivo: str = ""

    @property
    def aberto(self) -> bool:
        if not self.aberto_ate:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(self.aberto_ate)
        except ValueError:
            return False

    def segundos_restantes(self) -> int:
        if not self.aberto_ate:
            return 0
        try:
            resto = datetime.fromisoformat(self.aberto_ate) - datetime.now(timezone.utc)
        except ValueError:
            return 0
        return max(0, int(resto.total_seconds()))


def carregar(state_dir) -> Freio:
    caminho = _caminho(state_dir)
    if not caminho.is_file():
        return Freio()
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Freio()
    return Freio(
        nivel=dados.get("nivel", 0),
        aberto_ate=dados.get("aberto_ate", ""),
        motivo=dados.get("motivo", ""),
    )


def _salvar(state_dir, freio: Freio) -> None:
    escrever_atomico(
        _caminho(state_dir),
        json.dumps(
            {"nivel": freio.nivel, "aberto_ate": freio.aberto_ate, "motivo": freio.motivo},
            ensure_ascii=False,
        )
        + "\n",
    )


def bloqueado(state_dir) -> bool:
    return carregar(state_dir).aberto


def abrir(state_dir, motivo: str, agora: datetime | None = None) -> Freio:
    """Escala para o próximo degrau. Chamado quando uma tentativa — a primeira
    detecção ou uma sonda depois do cooldown — confirma que o bloqueio continua."""
    agora = agora or datetime.now(timezone.utc)
    atual = carregar(state_dir)
    nivel = min((atual.nivel or 0) + 1, len(RECUO_GLOBAL))
    espera = RECUO_GLOBAL[nivel - 1]
    freio = Freio(
        nivel=nivel,
        aberto_ate=(agora + timedelta(seconds=espera)).isoformat(timespec="seconds"),
        motivo=motivo,
    )
    _salvar(state_dir, freio)
    return freio


def fechar(state_dir) -> None:
    """Uma sonda deu certo, ou a sequência ruim nunca chegou ao limiar: volta ao
    normal. Idempotente — chamar sem um freio aberto não é erro."""
    _salvar(state_dir, Freio())
