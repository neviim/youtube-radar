"""Rode com: python3 -m unittest tests.test_env_io

O `env_io` é **vendorado verbatim** de `discord-link-brain:dlb/env_io.py` (commit
`4674e72`). Os testes daqui não repetem a suíte de lá: eles afirmam o **contrato do qual
este projeto depende**, para uma re-vendoragem futura que mude o comportamento quebrar
aqui em vez de aparecer como defeito no `.env` de alguém.

Três coisas que este projeto depende de verdade:

1. **A primeira ocorrência ganha**, igual ao `setdefault` do `config.load_dotenv`.
2. **A escrita cria `.env.bak` e `.env.tmp`.** É por isso que o `.gitignore` cobre os
   dois — e há teste em `test_segredos.py` afirmando isso. Ignorar só `.env` deixaria o
   `.bak`, com o mesmo conteúdo, rastreável.
3. **A escrita preserva comentário e ordem.** Um `.env` que perde os comentários perde a
   documentação que ele carrega, e ninguém percebe até precisar dela.
"""

import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import env_io


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"

    def tearDown(self):
        self.tmp.cleanup()


class TestLeitura(Base):
    def test_a_primeira_ocorrencia_ganha(self):
        self.env.write_text("CHAVE=primeiro\nCHAVE=segundo\n", encoding="utf-8")
        self.assertEqual("primeiro", env_io.ler(self.env)["CHAVE"])

    def test_aspas_saem_das_pontas_sem_interpretar_escape(self):
        """O shell do compose e o `load_dotenv` fazem isso; um parser mais esperto aqui
        divergiria do que realmente chega ao processo.
        """
        self.env.write_text('A="com aspas"\nB=\'simples\'\n', encoding="utf-8")
        valores = env_io.ler(self.env)
        self.assertEqual("com aspas", valores["A"])
        self.assertEqual("simples", valores["B"])

    def test_comentario_na_mesma_linha_faz_parte_do_valor(self):
        """Regra de formato que não é óbvia, e está aqui para não ser "consertada" por
        engano: tudo depois do `=` é valor.
        """
        self.env.write_text("CHAVE=1  # isto é comentário\n", encoding="utf-8")
        self.assertEqual("1  # isto é comentário", env_io.ler(self.env)["CHAVE"])

    def test_linha_comentada_nao_entra_nos_valores(self):
        self.env.write_text("# CHAVE=sugestao\nOUTRA=valor\n", encoding="utf-8")
        valores = env_io.ler(self.env)
        self.assertNotIn("CHAVE", valores)
        self.assertEqual("valor", valores["OUTRA"])

    def test_arquivo_ausente_e_dicionario_vazio(self):
        self.assertEqual({}, env_io.ler(self.env))

    def test_linhas_preserva_comentario_e_branco(self):
        self.env.write_text("# topo\n\nCHAVE=valor\n", encoding="utf-8")
        linhas = env_io.linhas(self.env)
        self.assertEqual(3, len(linhas))
        self.assertTrue(linhas[0].comentada)
        self.assertEqual("CHAVE", linhas[2].chave)
        self.assertTrue(linhas[2].e_atribuicao)

    def test_chave_repetida_e_reportada(self):
        self.env.write_text("CHAVE=a\nCHAVE=b\n", encoding="utf-8")
        self.assertEqual([1, 2], env_io.duplicadas(self.env)["CHAVE"])


class TestEscrita(Base):
    def test_cria_o_backup_e_nao_deixa_temporario(self):
        """O `.bak` e o `.tmp` são criados pelo próprio código — daí os dois estarem no
        `.gitignore`, e não só o `.env`.
        """
        self.env.write_text("CHAVE=antigo\n", encoding="utf-8")
        resultado = env_io.escrever(self.env, {"CHAVE": "novo"})
        self.assertEqual("novo", env_io.ler(self.env)["CHAVE"])
        self.assertIsNotNone(resultado.backup)
        self.assertEqual("CHAVE=antigo\n", resultado.backup.read_text(encoding="utf-8"))
        self.assertEqual([], list(self.env.parent.glob("*.tmp")))

    def test_o_backup_nasce_com_modo_seiscentos(self):
        self.env.write_text("CHAVE=antigo\n", encoding="utf-8")
        resultado = env_io.escrever(self.env, {"CHAVE": "novo"})
        modo = stat.S_IMODE(os.stat(resultado.backup).st_mode)
        self.assertEqual(0o600, modo, "o backup tem o mesmo segredo do `.env`")

    def test_preserva_comentario_ordem_e_linha_em_branco(self):
        original = "# um comentário\n\nA=1\n# outro\nB=2\n"
        self.env.write_text(original, encoding="utf-8")
        env_io.escrever(self.env, {"B": "3"})
        depois = self.env.read_text(encoding="utf-8")
        self.assertIn("# um comentário", depois)
        self.assertIn("# outro", depois)
        self.assertIn("A=1", depois)
        self.assertIn("B=3", depois)
        self.assertLess(depois.index("A=1"), depois.index("B=3"), "a ordem é preservada")

    def test_valor_igual_nao_mexe_no_arquivo(self):
        """O formulário posta todas as chaves de uma vez, a maioria em branco. Sem esta
        regra, salvar qualquer coisa acrescentaria dezenas de linhas `CHAVE=` de uma vez.
        """
        self.env.write_text("CHAVE=valor\n", encoding="utf-8")
        antes = self.env.stat().st_mtime_ns
        resultado = env_io.escrever(self.env, {"CHAVE": "valor"})
        self.assertEqual(("CHAVE",), resultado.iguais)
        self.assertIsNone(resultado.backup, "nada mudou, então nem backup foi feito")
        self.assertEqual(antes, self.env.stat().st_mtime_ns)

    def test_chave_nova_vai_para_o_fim(self):
        self.env.write_text("A=1\n", encoding="utf-8")
        resultado = env_io.escrever(self.env, {"NOVA": "2"})
        self.assertEqual(("NOVA",), resultado.acrescentadas)
        self.assertIn("NOVA=2", self.env.read_text(encoding="utf-8"))

    def test_chave_so_comentada_ganha_linha_ativa_depois_da_sugestao(self):
        """A documentação fica: a linha sugerida continua lá, e a ativa nasce embaixo."""
        self.env.write_text("# SUGERIDA=exemplo\n", encoding="utf-8")
        env_io.escrever(self.env, {"SUGERIDA": "de verdade"})
        depois = self.env.read_text(encoding="utf-8")
        self.assertIn("# SUGERIDA=exemplo", depois)
        self.assertIn("SUGERIDA=de verdade", depois)

    def test_chave_repetida_perde_a_segunda_ocorrencia(self):
        """A primeira é a que vale (`setdefault`), então a segunda é ruído que confunde."""
        self.env.write_text("CHAVE=a\nCHAVE=b\n", encoding="utf-8")
        env_io.escrever(self.env, {"CHAVE": "c"})
        depois = self.env.read_text(encoding="utf-8")
        self.assertEqual(1, depois.count("CHAVE="))
        self.assertIn("CHAVE=c", depois)

    def test_espaco_nas_pontas_e_preservado_com_aspas(self):
        self.env.write_text("CHAVE=x\n", encoding="utf-8")
        env_io.escrever(self.env, {"CHAVE": " com espaço "})
        self.assertIn('CHAVE=" com espaço "', self.env.read_text(encoding="utf-8"))


class TestRestaurar(Base):
    def test_restaura_byte_a_byte(self):
        """Um rollback que reformatasse o arquivo não seria rollback: seria uma terceira
        versão.
        """
        original = "# comentário\nCHAVE=antigo\n"
        self.env.write_text(original, encoding="utf-8")
        env_io.escrever(self.env, {"CHAVE": "novo"})
        self.assertTrue(env_io.restaurar(self.env))
        self.assertEqual(original, self.env.read_text(encoding="utf-8"))

    def test_sem_backup_devolve_falso(self):
        self.env.write_text("CHAVE=x\n", encoding="utf-8")
        self.assertFalse(env_io.restaurar(self.env))

    def test_tem_backup_responde_a_pergunta_sem_restaurar(self):
        self.env.write_text("CHAVE=antigo\n", encoding="utf-8")
        self.assertIsNone(env_io.tem_backup(self.env))
        env_io.escrever(self.env, {"CHAVE": "novo"})
        self.assertIsNotNone(env_io.tem_backup(self.env))
        self.assertEqual("novo", env_io.ler(self.env)["CHAVE"], "consultar não restaura")


class TestAmbienteLimpo(Base):
    def test_remove_do_ambiente_as_chaves_que_o_arquivo_define(self):
        """Todo processo longo carrega em si os valores que o arquivo tinha quando subiu
        — então um filho herdaria o valor **antigo** sem esta limpeza.
        """
        self.env.write_text("YTR_TESTE_ENV_IO=doarquivo\n", encoding="utf-8")
        os.environ["YTR_TESTE_ENV_IO"] = "doambiente"
        try:
            limpo = env_io.ambiente_limpo(self.env)
            self.assertNotIn("YTR_TESTE_ENV_IO", limpo)
            self.assertIn("PATH", limpo, "o resto do ambiente continua")
        finally:
            del os.environ["YTR_TESTE_ENV_IO"]


if __name__ == "__main__":
    unittest.main()
