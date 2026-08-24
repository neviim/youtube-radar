"""Rode com: python3 -m unittest tests.test_trava

O `flock` que garante escritor único no `canais.yaml` e no `sinais.jsonl` — os dois
arquivos com mais de um **comando** escrevendo.

A trava é tomada **dentro do `main()`, nunca no lançador**, e a distinção não é estilo:
se ela morasse no `ytr.sh`, o ciclo que roda *dentro* do container não a tomaria, e a
colisão que ela existe para impedir (o Dotcom rodando na mão com o container de pé) seria
exatamente a que passaria.

**`flock` não vale em NFS.** Se `.state` estiver num sistema de arquivos de rede, a
garantia de escritor único cai — e nenhum código aqui pode detectar isso por você.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr.trava import TravaOcupada, travar


class TestTrava(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tomar_e_soltar(self):
        with travar(self.dir):
            pass
        with travar(self.dir):
            pass

    def test_o_arquivo_de_lock_fica_em_locks(self):
        with travar(self.dir):
            self.assertTrue((self.dir / "locks" / "ciclo.lock").is_file())

    def test_o_segundo_encontra_ocupada(self):
        with travar(self.dir):
            with self.assertRaises(TravaOcupada):
                with travar(self.dir):
                    pass

    def test_a_mensagem_nomeia_o_detentor(self):
        """"a trava está com outro processo" não ajuda ninguém a decidir o que fazer;
        o pid e o horário ajudam.
        """
        with travar(self.dir):
            with self.assertRaises(TravaOcupada) as erro:
                with travar(self.dir):
                    pass
        mensagem = str(erro.exception)
        self.assertIn(str(os.getpid()), mensagem)
        self.assertIn("desde", mensagem)
        self.assertIn("sai sem fazer nada", mensagem)

    def test_travas_de_nomes_diferentes_nao_colidem(self):
        with travar(self.dir, "ciclo"):
            with travar(self.dir, "digest"):
                pass

    def test_o_mesmo_nome_colide(self):
        """`ciclo`, `digest` e `canais desativar` tomam a **mesma** trava de propósito:
        é ela que garante escritor único em `canais.yaml`, que os três escrevem.
        """
        with travar(self.dir, "digest"):
            with self.assertRaises(TravaOcupada):
                with travar(self.dir, "digest"):
                    pass

    def test_a_trava_e_solta_mesmo_com_excecao_no_corpo(self):
        """Sem o `finally`, um erro no meio do ciclo trancaria o sistema até reiniciar."""
        class Proposital(RuntimeError):
            pass

        with self.assertRaises(Proposital):
            with travar(self.dir):
                raise Proposital("erro no meio do ciclo")
        with travar(self.dir):
            pass

    def test_o_carimbo_e_reescrito_a_cada_tomada(self):
        caminho = self.dir / "locks" / "ciclo.lock"
        with travar(self.dir):
            primeiro = caminho.read_text(encoding="utf-8")
        with travar(self.dir):
            segundo = caminho.read_text(encoding="utf-8")
        self.assertIn(str(os.getpid()), primeiro)
        self.assertIn(str(os.getpid()), segundo)
        # Truncado e reescrito: sem o `truncate`, um carimbo mais curto deixaria resto
        # do anterior no fim do arquivo, e a mensagem de "ocupada" viria embaralhada.
        self.assertEqual(1, segundo.count("pid"))

    def test_o_diretorio_de_locks_e_criado_se_faltar(self):
        alvo = self.dir / "nao" / "existe" / "ainda"
        with travar(alvo):
            self.assertTrue((alvo / "locks").is_dir())


if __name__ == "__main__":
    unittest.main()
