"""Rode com: python3 -m unittest tests.test_limitador

O freio de circuito contra o YouTube: abre quando o bloqueio global se confirma,
escala se continuar ruim, fecha numa sonda bem-sucedida.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import limitador


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestEstadoInicial(Base):
    def test_sem_arquivo_comeca_fechado(self):
        self.assertFalse(limitador.bloqueado(self.state))
        self.assertEqual(0, limitador.carregar(self.state).segundos_restantes())

    def test_arquivo_corrompido_conta_como_fechado(self):
        (self.state / "limitador.json").parent.mkdir(parents=True, exist_ok=True)
        (self.state / "limitador.json").write_text("isto não é json", encoding="utf-8")
        self.assertFalse(limitador.bloqueado(self.state))


class TestAbrirEEscalar(Base):
    def test_abrir_bloqueia_pelo_primeiro_degrau(self):
        # `segundos_restantes()` sempre compara contra o agora **real** — passar um
        # `agora` fixo aqui só faria sentido se a leitura também aceitasse um agora
        # injetado, e ela não aceita (de propósito: quem chama não finge o relógio).
        freio = limitador.abrir(self.state, "teste")
        self.assertEqual(1, freio.nivel)
        self.assertAlmostEqual(limitador.RECUO_GLOBAL[0], freio.segundos_restantes(), delta=2)

    def test_abrir_de_novo_escala_para_o_proximo_degrau(self):
        limitador.abrir(self.state, "primeira")
        segundo = limitador.abrir(self.state, "segunda")
        self.assertEqual(2, segundo.nivel)
        self.assertAlmostEqual(limitador.RECUO_GLOBAL[1], segundo.segundos_restantes(), delta=2)

    def test_nivel_nao_passa_do_teto_da_escada(self):
        for _ in range(len(limitador.RECUO_GLOBAL) + 5):
            freio = limitador.abrir(self.state, "de novo")
        self.assertEqual(len(limitador.RECUO_GLOBAL), freio.nivel)
        self.assertAlmostEqual(limitador.RECUO_GLOBAL[-1], freio.segundos_restantes(), delta=2)

    def test_bloqueado_e_verdadeiro_logo_apos_abrir(self):
        limitador.abrir(self.state, "teste")
        self.assertTrue(limitador.bloqueado(self.state))

    def test_bloqueado_vira_falso_depois_que_o_tempo_passa(self):
        passado = datetime.now(timezone.utc) - timedelta(days=1)
        limitador.abrir(self.state, "teste", agora=passado)
        self.assertFalse(limitador.bloqueado(self.state), "o degrau já passou — pronto pra sonda")


class TestFechar(Base):
    def test_fechar_depois_de_aberto_volta_ao_normal(self):
        limitador.abrir(self.state, "teste")
        limitador.fechar(self.state)
        self.assertFalse(limitador.bloqueado(self.state))
        self.assertEqual(0, limitador.carregar(self.state).nivel)

    def test_fechar_sem_nunca_ter_aberto_nao_e_erro(self):
        limitador.fechar(self.state)  # não levanta
        self.assertFalse(limitador.bloqueado(self.state))


if __name__ == "__main__":
    unittest.main()
