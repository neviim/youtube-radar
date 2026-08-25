"""A camada de modelo: opcional, por cima, e nunca decide (Fase 8, D5 do plano).

**Uma chamada por dia**, em lote, narrando em PT-BR o ranking que `ytr.pool` já
decidiu. Fora do caminho crítico por desenho: o login local do `claude` é disputado
pelo `discord-link-brain` (D5), então nada aqui pode bloquear o `ciclo` — só o
`digest`, 1x/dia, chama este módulo, e nunca o `ciclo`.

**"Narra, não escolhe" é estrutural, não um filtro.** O modelo recebe só título,
canal e o porquê dos itens que `pool.selecionar` já aprovou — nunca a lista de
candidatos, nunca uma URL, nunca um `video_id`. A prosa que ele devolve vira **só** o
`narracao` do cabeçalho (`texto.cabecalho_de_digest`); a mensagem de cada item
continua sendo montada por `texto.item_de_digest` a partir do vídeo de verdade,
sempre. Não existe caminho de código onde o texto do modelo produz um item novo —
por isso o teste que prova isso (em `tests/test_cmd_digest` / `test_cli.py`) não
precisa "pegar" uma injeção: ele só confirma que o cabeçalho muda e os itens não.

**Três backends.** `claude-cli`/`codex-cli` são binário local, sem chave de API —
mesmo desenho do `discord-link-brain` (`~/.claude`/`~/.codex` montados `:ro` no
container, Fase 5). `claude-cli` usa `--output-format json` e lê `result`;
`codex-cli` usa `--output-schema` porque a saída dele não tem envelope JSON
previsível sem isso — o mesmo motivo que levou o projeto irmão a fazer igual
(`dlb/sintese.py`). `anthropic` é o quarto: API direta, com `ANTHROPIC_API_KEY`,
pelo SDK oficial (pacote `anthropic`, importado só quando este backend é usado —
não é dependência obrigatória de quem nunca liga `YTR_LLM_BACKEND=anthropic`).
Não disputa o login local com o `discord-link-brain` (é outro caminho de
autenticação inteiramente), mas ainda conta contra `YTR_LLM_MAX_DIA` — o teto é
sobre "quantas vezes o digest chama modelo", não sobre qual credencial usa.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .state import escrever_atomico

SISTEMA = (
    "Você narra, em português do Brasil, um ranking de recomendação de vídeos que já "
    "foi decidido por regras determinísticas. Não escolha, não invente, não sugira "
    "vídeo nenhum além dos listados — escreva de uma a três frases conectando os itens "
    "abaixo, como a introdução de um boletim. Sem markdown, sem repetir os títulos por "
    "extenso, sem inventar número ou dado que não esteja na lista."
)


class ModeloError(RuntimeError):
    """Binário ausente, timeout, código de saída != 0, ou saída malformada."""


def _caminho_uso(state_dir) -> Path:
    return Path(state_dir) / "llm_uso.json"


def chamadas_hoje(state_dir) -> int:
    caminho = _caminho_uso(state_dir)
    if not caminho.is_file():
        return 0
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    hoje = datetime.now(timezone.utc).date().isoformat()
    return dados.get("chamadas", 0) if dados.get("dia") == hoje else 0


def _registrar_chamada(state_dir) -> None:
    hoje = datetime.now(timezone.utc).date().isoformat()
    escrever_atomico(
        _caminho_uso(state_dir),
        json.dumps({"dia": hoje, "chamadas": chamadas_hoje(state_dir) + 1}, ensure_ascii=False) + "\n",
    )


def disponivel(cfg: Config) -> bool:
    """Backend ligado e ainda dentro do teto de hoje. `doctor` e `digest` usam a
    mesma função — um só lugar decidindo "posso chamar"."""
    return cfg.llm_backend != "none" and chamadas_hoje(cfg.state_dir) < cfg.llm_max_dia


def _prompt(itens: list[dict]) -> str:
    linhas = [f"- {i['titulo']} ({i['canal']}): {i['razao']}" for i in itens]
    return "Itens de hoje:\n" + "\n".join(linhas)


def narrar(cfg: Config, itens: list[dict]) -> str:
    """Devolve a prosa, ou levanta `ModeloError`. Quem chama decide o que fazer com a
    falha — por D5, o digest nunca para por causa disto, só degrada."""
    prompt = _prompt(itens)
    if cfg.llm_backend == "claude-cli":
        texto = _rodar_claude(cfg, prompt)
    elif cfg.llm_backend == "anthropic":
        texto = _rodar_anthropic(cfg, prompt)
    elif cfg.llm_backend == "codex-cli":
        texto = _rodar_codex(cfg, prompt)
    else:
        raise ModeloError(f"backend desconhecido ou não implementado: {cfg.llm_backend!r}")
    _registrar_chamada(cfg.state_dir)
    return texto.strip()


def _rodar_claude(cfg: Config, prompt: str) -> str:
    cmd = [cfg.claude_bin, "-p", "--output-format", "json", "--append-system-prompt", SISTEMA]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=cfg.llm_timeout
        )
    except FileNotFoundError as erro:
        raise ModeloError(f"binário do claude não encontrado ({cfg.claude_bin}): {erro}") from erro
    except subprocess.TimeoutExpired as erro:
        raise ModeloError(f"claude não respondeu em {cfg.llm_timeout}s") from erro
    if proc.returncode != 0:
        raise ModeloError(f"claude saiu com código {proc.returncode}: {proc.stderr[-500:]}")
    try:
        return json.loads(proc.stdout)["result"]
    except (json.JSONDecodeError, KeyError) as erro:
        raise ModeloError(f"saída do claude não é o JSON esperado: {erro}") from erro


def _rodar_anthropic(cfg: Config, prompt: str) -> str:
    """API direta da Anthropic — chave, não login local. Import só aqui dentro: quem
    nunca liga `YTR_LLM_BACKEND=anthropic` não precisa do pacote `anthropic` instalado.
    """
    if not cfg.anthropic_api_key:
        raise ModeloError(
            "YTR_LLM_BACKEND=anthropic exige ANTHROPIC_API_KEY (chave de API — "
            "diferente do login local que YTR_LLM_BACKEND=claude-cli usa)."
        )
    try:
        import anthropic
    except ImportError as erro:
        raise ModeloError(f"pacote `anthropic` não instalado (`pip install anthropic`): {erro}") from erro

    cliente = anthropic.Anthropic(api_key=cfg.anthropic_api_key, timeout=cfg.llm_timeout)
    try:
        resposta = cliente.messages.create(
            model=cfg.anthropic_model, max_tokens=500, system=SISTEMA,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as erro:
        raise ModeloError(f"ANTHROPIC_API_KEY rejeitada: {erro}") from erro
    except anthropic.RateLimitError as erro:
        raise ModeloError(f"limite de taxa da API da Anthropic: {erro}") from erro
    except anthropic.APIConnectionError as erro:
        raise ModeloError(f"não consegui conectar à API da Anthropic: {erro}") from erro
    except anthropic.APIStatusError as erro:
        raise ModeloError(f"API da Anthropic respondeu {erro.status_code}: {erro}") from erro

    texto = next((bloco.text for bloco in resposta.content if bloco.type == "text"), "")
    if not texto:
        raise ModeloError("resposta da Anthropic sem bloco de texto")
    return texto


def _rodar_codex(cfg: Config, prompt: str) -> str:
    esquema = {
        "type": "object",
        "properties": {"narracao": {"type": "string"}},
        "required": ["narracao"],
        "additionalProperties": False,
    }
    with tempfile.TemporaryDirectory() as tmp:
        caminho_esquema = Path(tmp) / "esquema.json"
        caminho_saida = Path(tmp) / "saida.json"
        caminho_esquema.write_text(json.dumps(esquema), encoding="utf-8")

        cmd = [
            cfg.codex_bin, "exec", "--skip-git-repo-check", "-s", "read-only",
            "--color", "never", "--output-schema", str(caminho_esquema),
            "-o", str(caminho_saida), f"{SISTEMA}\n\n{prompt}",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.llm_timeout)
        except FileNotFoundError as erro:
            raise ModeloError(f"binário do codex não encontrado ({cfg.codex_bin}): {erro}") from erro
        except subprocess.TimeoutExpired as erro:
            raise ModeloError(f"codex não respondeu em {cfg.llm_timeout}s") from erro
        if proc.returncode != 0:
            raise ModeloError(f"codex saiu com código {proc.returncode}: {proc.stderr[-500:]}")
        try:
            dados = json.loads(caminho_saida.read_text(encoding="utf-8"))
            return dados["narracao"]
        except (OSError, json.JSONDecodeError, KeyError) as erro:
            raise ModeloError(f"saída do codex não é o JSON esperado: {erro}") from erro
