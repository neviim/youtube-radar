"""Rode com: python3 -m unittest tests.test_modelo

A camada de modelo (Fase 8, D5 do plano): opcional, por cima, fora do caminho
crítico. Testa a mecânica — teto diário, resolução de backend, tratamento de falha —
sem invocar nenhum binário de verdade: `subprocess.run` é substituído por um dublê,
mesmo espírito de `_fingir_rede` em `test_cli.py`.
"""

import json
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


class TestBackendDesconhecido(Base):
    def test_backend_nao_implementado_vira_modelo_error_explicito(self):
        cfg = Config(llm_backend="algo-que-nao-existe", state_dir=self.state)
        with self.assertRaises(modelo.ModeloError):
            modelo.narrar(cfg, [])


if __name__ == "__main__":
    unittest.main()
