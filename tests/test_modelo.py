"""Rode com: python3 -m unittest tests.test_modelo

A camada de modelo (Fase 8, D5 do plano): opcional, por cima, fora do caminho
crítico. Testa a mecânica — teto diário, resolução de backend, tratamento de falha —
sem invocar nenhum binário de verdade: `subprocess.run` é substituído por um dublê,
mesmo espírito de `_fingir_rede` em `test_cli.py`.
"""

import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import modelo
from ytr.config import Config


class _ProcessoFalso:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self._run_original = modelo.subprocess.run
        self.cfg = Config(llm_backend="claude-cli", state_dir=self.state)

    def tearDown(self):
        modelo.subprocess.run = self._run_original
        self.tmp.cleanup()


class TestTetoDiario(Base):
    def test_zero_chamadas_no_inicio(self):
        self.assertEqual(0, modelo.chamadas_hoje(self.state))

    def test_incrementa_a_cada_chamada_registrada(self):
        modelo._registrar_chamada(self.state)
        modelo._registrar_chamada(self.state)
        self.assertEqual(2, modelo.chamadas_hoje(self.state))

    def test_dia_diferente_no_arquivo_conta_como_zero(self):
        caminho = modelo._caminho_uso(self.state)
        caminho.write_text(json.dumps({"dia": "2020-01-01", "chamadas": 5}), encoding="utf-8")
        self.assertEqual(0, modelo.chamadas_hoje(self.state))

    def test_arquivo_corrompido_conta_como_zero_em_vez_de_levantar(self):
        caminho = modelo._caminho_uso(self.state)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("isto não é json", encoding="utf-8")
        self.assertEqual(0, modelo.chamadas_hoje(self.state))


class TestDisponivel(Base):
    def test_backend_none_nunca_disponivel(self):
        cfg = Config(llm_backend="none", state_dir=self.state)
        self.assertFalse(modelo.disponivel(cfg))

    def test_disponivel_ate_bater_o_teto(self):
        cfg = Config(llm_backend="claude-cli", llm_max_dia=2, state_dir=self.state)
        self.assertTrue(modelo.disponivel(cfg))
        modelo._registrar_chamada(self.state)
        self.assertTrue(modelo.disponivel(cfg))
        modelo._registrar_chamada(self.state)
        self.assertFalse(modelo.disponivel(cfg), "bateu o teto de 2/dia")


class TestNarrarClaude(Base):
    def test_devolve_o_result_do_envelope_json_e_registra_a_chamada(self):
        def fake_run(cmd, **kwargs):
            self.assertEqual(self.cfg.claude_bin, cmd[0])
            self.assertIn("--output-format", cmd)
            return _ProcessoFalso(stdout=json.dumps({"result": " uma narração. "}))
        modelo.subprocess.run = fake_run

        texto = modelo.narrar(self.cfg, [{"titulo": "T", "canal": "C", "razao": "R"}])

        self.assertEqual("uma narração.", texto)
        self.assertEqual(1, modelo.chamadas_hoje(self.state))

    def test_binario_ausente_vira_modelo_error(self):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("não achei")
        modelo.subprocess.run = fake_run
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])
        self.assertEqual(0, modelo.chamadas_hoje(self.state), "falha não conta como chamada")

    def test_timeout_vira_modelo_error(self):
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))
        modelo.subprocess.run = fake_run
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])

    def test_codigo_de_saida_nao_zero_vira_modelo_error(self):
        modelo.subprocess.run = lambda cmd, **kw: _ProcessoFalso(returncode=1, stderr="deu ruim")
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])

    def test_saida_sem_json_valido_vira_modelo_error(self):
        modelo.subprocess.run = lambda cmd, **kw: _ProcessoFalso(stdout="isto não é json")
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])


class TestNarrarCodex(Base):
    def setUp(self):
        super().setUp()
        self.cfg = Config(llm_backend="codex-cli", state_dir=self.state)

    def _fake_run_que_escreve_saida(self, narracao):
        def fake_run(cmd, **kwargs):
            self.assertEqual(self.cfg.codex_bin, cmd[0])
            caminho_saida = Path(cmd[cmd.index("-o") + 1])
            caminho_saida.write_text(json.dumps({"narracao": narracao}), encoding="utf-8")
            return _ProcessoFalso()
        return fake_run

    def test_le_o_arquivo_de_saida_do_output_schema(self):
        modelo.subprocess.run = self._fake_run_que_escreve_saida(" narração do codex ")
        texto = modelo.narrar(self.cfg, [{"titulo": "T", "canal": "C", "razao": "R"}])
        self.assertEqual("narração do codex", texto)

    def test_arquivo_de_saida_ausente_vira_modelo_error(self):
        modelo.subprocess.run = lambda cmd, **kw: _ProcessoFalso()  # não escreve nada
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])


class _BlocoDeTexto:
    def __init__(self, texto):
        self.type = "text"
        self.text = texto


class _RespostaFalsa:
    def __init__(self, texto):
        self.content = [_BlocoDeTexto(texto)]


class _ErroFalso(Exception):
    pass


class _ErroDeStatusFalso(Exception):
    def __init__(self, msg, status_code=500):
        super().__init__(msg)
        self.status_code = status_code


def _modulo_anthropic_falso(cliente_ou_excecao):
    """Um módulo `anthropic` de mentira, injetado em `sys.modules`. `cliente_ou_excecao`
    é chamável com `(api_key, timeout)` e devolve algo com `.messages.create(...)`, ou
    levanta direto — é o dublê do `anthropic.Anthropic(...)`."""
    modulo = types.ModuleType("anthropic")
    modulo.Anthropic = cliente_ou_excecao
    modulo.AuthenticationError = _ErroFalso
    modulo.RateLimitError = _ErroFalso
    modulo.APIConnectionError = _ErroFalso
    modulo.APIStatusError = _ErroDeStatusFalso
    return modulo


class TestNarrarAnthropic(Base):
    def setUp(self):
        super().setUp()
        self.cfg = Config(llm_backend="anthropic", anthropic_api_key="sk-ant-teste",
                           state_dir=self.state)
        self._modulo_original = sys.modules.get("anthropic")

    def tearDown(self):
        if self._modulo_original is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = self._modulo_original
        super().tearDown()

    def _instalar(self, cliente_ou_excecao):
        sys.modules["anthropic"] = _modulo_anthropic_falso(cliente_ou_excecao)

    def test_devolve_o_texto_do_bloco_e_registra_a_chamada(self):
        class ClienteFalso:
            def __init__(self, api_key, timeout):
                self.messages = self

            def create(self, **kwargs):
                self.chamada = kwargs
                return _RespostaFalsa(" uma narração da api direta. ")

        self._instalar(ClienteFalso)
        texto = modelo.narrar(self.cfg, [{"titulo": "T", "canal": "C", "razao": "R"}])
        self.assertEqual("uma narração da api direta.", texto)
        self.assertEqual(1, modelo.chamadas_hoje(self.state))

    def test_sem_chave_vira_modelo_error_sem_importar_o_pacote(self):
        cfg = Config(llm_backend="anthropic", anthropic_api_key="", state_dir=self.state)
        with self.assertRaises(modelo.ModeloError) as erro:
            modelo.narrar(cfg, [])
        self.assertIn("ANTHROPIC_API_KEY", str(erro.exception))
        self.assertEqual(0, modelo.chamadas_hoje(self.state))

    def test_pacote_ausente_vira_modelo_error(self):
        sys.modules.pop("anthropic", None)
        import builtins
        original_import = builtins.__import__

        def fake_import(nome, *args, **kwargs):
            if nome == "anthropic":
                raise ModuleNotFoundError("No module named 'anthropic'")
            return original_import(nome, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            with self.assertRaises(modelo.ModeloError) as erro:
                modelo.narrar(self.cfg, [])
        finally:
            builtins.__import__ = original_import
        self.assertIn("anthropic", str(erro.exception))

    def test_erro_de_autenticacao_vira_modelo_error(self):
        class ClienteFalso:
            def __init__(self, api_key, timeout):
                self.messages = self

            def create(self, **kwargs):
                raise sys.modules["anthropic"].AuthenticationError("chave inválida")

        self._instalar(ClienteFalso)
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])
        self.assertEqual(0, modelo.chamadas_hoje(self.state), "falha não conta como chamada")

    def test_limite_de_taxa_vira_modelo_error(self):
        class ClienteFalso:
            def __init__(self, api_key, timeout):
                self.messages = self

            def create(self, **kwargs):
                raise sys.modules["anthropic"].RateLimitError("429")

        self._instalar(ClienteFalso)
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])

    def test_resposta_sem_bloco_de_texto_vira_modelo_error(self):
        class ClienteFalso:
            def __init__(self, api_key, timeout):
                self.messages = self

            def create(self, **kwargs):
                return _RespostaFalsa("")

        self._instalar(ClienteFalso)
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(self.cfg, [])


class TestBackendDesconhecido(Base):
    def test_backend_nao_implementado_vira_modelo_error_explicito(self):
        cfg = Config(llm_backend="algo-que-nao-existe", state_dir=self.state)
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(cfg, [])


if __name__ == "__main__":
    unittest.main()
