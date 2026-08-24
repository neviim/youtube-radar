"""`flock` no `.state`, para duas instâncias não escreverem o mesmo arquivo.

**Tomado dentro do `main()`, nunca no lançador.** A distinção não é estilo: se o lock
morasse no `ytr.sh`, o ciclo que roda *dentro* do container não o tomaria — e a
colisão que ele existe para impedir (o Dotcom rodando na mão com o container de pé)
seria exatamente a que passaria.

**`flock` não vale em NFS.** Se `.state` estiver num sistema de arquivos de rede, a
garantia de escritor único cai, e nenhum código aqui pode detectar isso por você.
"""

from __future__ import annotations

import errno
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from .state import agora_utc


class TravaOcupada(RuntimeError):
    """Outro processo detém a trava. Sai com código 3."""


@contextmanager
def travar(diretorio: Path, nome: str = "ciclo"):
    """Exclusão entre `ciclo`, `digest` e `canais desativar` — todos tomam esta trava.

    Ela é o que garante escritor único em `canais.yaml` e em `sinais.jsonl`, que têm
    mais de um comando escrevendo. O desenho de arquivo garante o resto.
    """
    caminho = Path(diretorio) / "locks" / f"{nome}.lock"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    arquivo = caminho.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as erro:
            if erro.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            arquivo.seek(0)
            quem = arquivo.read().strip() or "outro processo (sem carimbo)"
            raise TravaOcupada(
                f"a trava `{nome}` está com {quem}. "
                "Dois ciclos ao mesmo tempo escreveriam o mesmo estado; este sai sem "
                "fazer nada."
            ) from erro
        arquivo.seek(0)
        arquivo.truncate()
        arquivo.write(f"pid {os.getpid()} desde {agora_utc()}")
        arquivo.flush()
        yield
    finally:
        try:
            fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)
        finally:
            arquivo.close()
