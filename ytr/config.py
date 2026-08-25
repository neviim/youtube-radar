"""Configuração vinda do `.env` e do ambiente, com validação que fala.

Duas posturas herdadas do projeto irmão, e as duas são deliberadas:

1. **`os.environ.setdefault`**, não `os.environ[k] = v`. O que já estava exportado no
   shell ganha do arquivo, e a **primeira** ocorrência de uma chave no arquivo é a que
   vale. Um leitor que resolvesse "a última ganha" mostraria um valor que o sistema
   não usa.
2. **Erro de configuração é uma frase, não um traceback.** `ConfigError` sobe até o
   `main`, que imprime a frase e sai com código 2. Ninguém depura `.env` lendo pilha.

E uma que é deste projeto: **`YTR_CANAL_AVISO` não tem padrão**. Vazio, quem for postar
recusa subir e nomeia a variável. Um padrão "seguro" aqui significaria escolher um canal
do Discord de outra pessoa para escrever — não existe escolha segura, existe recusa.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import env_io


class ConfigError(RuntimeError):
    """Configuração faltando ou incoerente. Sai como código 2 com uma frase."""


def load_dotenv(caminho: Path | None = None) -> None:
    """Carrega o `.env` no ambiente sem sobrescrever o que já está exportado."""
    destino = Path(caminho) if caminho else Path.cwd() / ".env"
    for chave, valor in env_io.ler(destino).items():
        os.environ.setdefault(chave, valor)


def _bool(nome: str, padrao: str) -> bool:
    return os.environ.get(nome, padrao).strip().lower() not in ("0", "false", "no", "")


def _int(nome: str, padrao: int) -> int:
    bruto = os.environ.get(nome, "").strip()
    if not bruto:
        return padrao
    try:
        return int(bruto)
    except ValueError as erro:
        raise ConfigError(f"{nome} tem de ser um inteiro, e veio {bruto!r}.") from erro


def _float(nome: str, padrao: float) -> float:
    bruto = os.environ.get(nome, "").strip()
    if not bruto:
        return padrao
    try:
        return float(bruto)
    except ValueError as erro:
        raise ConfigError(f"{nome} tem de ser um número, e veio {bruto!r}.") from erro


BACKENDS_LLM = ("none", "claude-cli", "codex-cli", "anthropic")

# Caminho padrão do vault. Neutro de propósito: um caminho com nome de pessoa dentro
# só existe na máquina de quem escreveu, e o sistema falha em silêncio para todo mundo
# mais. Há um teste que confere que este valor continua neutro.
VAULT_PADRAO = "~/Documents/Obsidian/vault"


@dataclass
class Config:
    # Discord
    discord_token: str = ""
    canal_entrada: str = ""
    canal_aviso: str = ""
    donos: tuple[str, ...] = ()
    post_enabled: bool = False

    # marcas
    emoji_cadastrado: str = "📡"
    emoji_repetido: str = "🔁"
    emoji_video: str = "🎬"
    emoji_erro: str = "⛔"

    # ciclo
    piso_segundos: int = 900
    recuo_lento: bool = True
    recuo_lento_dias: int = 7
    recuo_lento_segundos: int = 3600
    max_avisos_ciclo: int = 10
    max_avisos_canal: int = 3
    avisar_shorts: bool = False
    lembrar_ids: int = 50

    # pool 2 — vídeos de canal monitorado suprimidos do aviso individual (hoje, só
    # Shorts), reaproveitados como candidato do digest em vez de descartados (§D7)
    pool2_ativo: bool = True
    pool2_janela_dias: int = 3

    # gosto
    vault_path: Path = field(default_factory=lambda: Path(os.path.expanduser(VAULT_PADRAO)))
    peso_canal: float = 5.0
    peso_tag: float = 2.0
    peso_lexico: float = 1.0
    peso_engajamento: float = 1.5
    peso_recencia: float = 1.0
    peso_polegar_baixo: float = 8.0
    corpus_max_chars: int = 120_000

    # digest
    digest_at: str = "08:00"
    digest_itens: int = 4
    digest_por_canal: int = 1
    janela_feedback_dias: int = 7

    # modelo
    llm_backend: str = "none"
    llm_max_dia: int = 1
    resumo_llm: bool = False
    llm_budget_dir: str = ""
    claude_bin: str = "claude"
    codex_bin: str = "codex"
    llm_timeout: float = 60.0

    # máquina
    state_dir: Path = field(default_factory=lambda: Path(".state"))
    health_tolerance: int = 3600

    @classmethod
    def from_env(cls) -> "Config":
        backend = os.environ.get("YTR_LLM_BACKEND", "none").strip() or "none"
        if backend not in BACKENDS_LLM:
            raise ConfigError(
                f"YTR_LLM_BACKEND desconhecido: {backend!r}. "
                f"Use um de: {', '.join(BACKENDS_LLM)}."
            )
        vault = os.environ.get("OBSIDIAN_VAULT", "").strip()
        return cls(
            discord_token=os.environ.get("DISCORD_TOKEN", "").strip(),
            canal_entrada=os.environ.get("YTR_CANAL_ENTRADA", "").strip(),
            canal_aviso=os.environ.get("YTR_CANAL_AVISO", "").strip(),
            donos=tuple(
                d.strip() for d in os.environ.get("YTR_DONOS", "").split(",") if d.strip()
            ),
            post_enabled=_bool("YTR_POST_ENABLED", "0"),
            emoji_cadastrado=os.environ.get("YTR_EMOJI_CADASTRADO", cls.emoji_cadastrado),
            emoji_repetido=os.environ.get("YTR_EMOJI_REPETIDO", cls.emoji_repetido),
            emoji_video=os.environ.get("YTR_EMOJI_VIDEO", cls.emoji_video),
            emoji_erro=os.environ.get("YTR_EMOJI_ERRO", cls.emoji_erro),
            piso_segundos=_int("YTR_PISO_SEGUNDOS", 900),
            recuo_lento=_bool("YTR_RECUO_LENTO", "1"),
            recuo_lento_dias=_int("YTR_RECUO_LENTO_DIAS", 7),
            recuo_lento_segundos=_int("YTR_RECUO_LENTO_SEGUNDOS", 3600),
            max_avisos_ciclo=_int("YTR_MAX_AVISOS_CICLO", 10),
            max_avisos_canal=_int("YTR_MAX_AVISOS_CANAL", 3),
            avisar_shorts=_bool("YTR_AVISAR_SHORTS", "0"),
            lembrar_ids=_int("YTR_LEMBRAR_IDS", 50),
            pool2_ativo=_bool("YTR_POOL2_ATIVO", "1"),
            pool2_janela_dias=_int("YTR_POOL2_JANELA_DIAS", 3),
            vault_path=Path(os.path.expanduser(vault or VAULT_PADRAO)),
            peso_canal=_float("YTR_PESO_CANAL", 5.0),
            peso_tag=_float("YTR_PESO_TAG", 2.0),
            peso_lexico=_float("YTR_PESO_LEXICO", 1.0),
            peso_engajamento=_float("YTR_PESO_ENGAJAMENTO", 1.5),
            peso_recencia=_float("YTR_PESO_RECENCIA", 1.0),
            peso_polegar_baixo=_float("YTR_PESO_POLEGAR_BAIXO", 8.0),
            corpus_max_chars=_int("YTR_CORPUS_MAX_CHARS", 120_000),
            digest_at=os.environ.get("YTR_DIGEST_AT", "08:00").strip() or "08:00",
            digest_itens=_int("YTR_DIGEST_ITENS", 4),
            digest_por_canal=_int("YTR_DIGEST_POR_CANAL", 1),
            janela_feedback_dias=_int("YTR_JANELA_FEEDBACK_DIAS", 7),
            llm_backend=backend,
            llm_max_dia=_int("YTR_LLM_MAX_DIA", 1),
            resumo_llm=_bool("YTR_RESUMO_LLM", "0"),
            llm_budget_dir=os.environ.get("LLM_BUDGET_DIR", "").strip(),
            claude_bin=os.environ.get("CLAUDE_BIN", "claude").strip() or "claude",
            codex_bin=os.environ.get("CODEX_BIN", "codex").strip() or "codex",
            llm_timeout=_float("YTR_LLM_TIMEOUT", 60.0),
            state_dir=Path(os.environ.get("YTR_STATE_DIR", "").strip() or ".state"),
            health_tolerance=_int("YTR_HEALTH_TOLERANCE", 3600),
        )

    # ------------------------------------------------------------------ marcas

    @property
    def marcas(self) -> tuple[str, ...]:
        """Todo emoji que o radar põe numa mensagem, em nome do bot."""
        return (
            self.emoji_cadastrado,
            self.emoji_repetido,
            self.emoji_video,
            self.emoji_erro,
        )

    def validar_marcas(self) -> None:
        """As nossas marcas não podem repetir entre si nem encostar nas do vizinho.

        O segundo caso é o que dói e não é hipótese: o `discord-link-brain` roda com o
        **mesmo bot**, e o campo `me` das reações do Discord significa "este
        usuário-bot", não "este processo". Se pusermos ✅ ou 📚 numa mensagem, o
        `ja_marcada()` dele passa a ver a mensagem como já resolvida e **para de
        arquivar aquele link — que some do vault em silêncio**.

        O `validar_marcas()` dele não pode nos ver, porque estamos em outro repo. Então
        o guarda mora aqui, e há um teste que repete os cinco emojis dele à mão.
        """
        proprias = [m for m in self.marcas if m]
        if len(set(proprias)) != len(proprias):
            raise ConfigError(
                "duas marcas do radar usam o mesmo emoji: "
                f"{', '.join(sorted(proprias))}. Cada uma tem de significar uma coisa."
            )
        comuns = set(proprias) & set(MARCAS_DO_VIZINHO)
        if comuns:
            raise ConfigError(
                f"emoji que o discord-link-brain também usa: {', '.join(sorted(comuns))}. "
                "Os dois projetos rodam com o mesmo bot, e o campo `me` das reações não "
                "distingue processo: reusar uma marca dele faz um link parar de ser "
                "arquivado, em silêncio."
            )

    # ---------------------------------------------------------------- validação

    def exigir_discord(self) -> None:
        if not self.discord_token:
            raise ConfigError("DISCORD_TOKEN não definido — preencha o `.env`.")

    def exigir_canal_aviso(self) -> None:
        if not self.canal_aviso:
            raise ConfigError(
                "YTR_CANAL_AVISO não definido, e não existe padrão seguro para ele: "
                "cair no canal de captura encheria o arquivo de outro projeto com ruído "
                "de bot. Ponha o id do canal onde o radar deve avisar."
            )

    def exigir_canal_entrada(self) -> None:
        if not self.canal_entrada:
            raise ConfigError(
                "YTR_CANAL_ENTRADA não definido — é o canal de onde o radar lê os links "
                "de canal postados."
            )

    def exigir_vault(self) -> None:
        if not (self.vault_path / "50_LINKS").is_dir():
            raise ConfigError(
                f"não achei `50_LINKS/` em {self.vault_path} — confira OBSIDIAN_VAULT. "
                "O radar só **lê** o vault; nunca escreve nele."
            )


# Os cinco emojis do `discord-link-brain`, escritos à mão de propósito.
#
# São **cinco**, não seis: `marcas = (PROCESSED, DUPLICATE)` do sync e
# `marcas_responder = (RESPONDER, PENDENTE, ERRO)` do responder. O número importa
# porque esta lista é um guarda de segurança, e um teste cuja aritmética ninguém
# confere não protege nada.
#
# Escritos à mão, e não importados, porque o outro repo pode não estar presente nesta
# máquina — e um guarda que só roda quando o vizinho está instalado não é guarda.
MARCAS_DO_VIZINHO = ("✅", "📚", "💬", "⏳", "❌")
