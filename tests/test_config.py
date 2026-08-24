"""Rode com: python3 -m unittest tests.test_config

Leitura da configuração. Duas posturas herdadas do projeto irmão, e as duas deliberadas:

1. **`os.environ.setdefault`, não atribuição.** O que já estava exportado no shell ganha
   do arquivo, e a **primeira** ocorrência de uma chave no arquivo é a que vale. Um leitor
   que resolvesse "a última ganha" mostraria um valor que o sistema não usa.
2. **Erro de configuração é uma frase, não um traceback.** Ninguém depura `.env` lendo
   pilha.

E uma que é deste projeto: **`YTR_CANAL_AVISO` não tem padrão.** Um padrão "seguro" aqui
significaria escolher um canal do Discord de outra pessoa para escrever — não existe
escolha segura, existe recusa.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr.config import BACKENDS_LLM, VAULT_PADRAO, Config, ConfigError, load_dotenv


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ambiente = dict(os.environ)
        for chave in [c for c in os.environ if c.startswith(("YTR_", "DISCORD_", "OBSIDIAN_"))]:
            del os.environ[chave]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.ambiente)
        self.tmp.cleanup()


class TestLoadDotenv(Base):
    def test_a_primeira_ocorrencia_ganha(self):
        """Igual ao `setdefault`: chave repetida vale pela primeira aparição.

        É o defeito que existiu no `.env.example` do irmão, com a mesma chave em `1` na
        linha 15 e `0` na 118.
        """
        arquivo = self.dir / ".env"
        arquivo.write_text("YTR_PISO_SEGUNDOS=111\nYTR_PISO_SEGUNDOS=222\n", encoding="utf-8")
        load_dotenv(arquivo)
        self.assertEqual("111", os.environ["YTR_PISO_SEGUNDOS"])

    def test_o_que_ja_estava_exportado_ganha_do_arquivo(self):
        os.environ["YTR_PISO_SEGUNDOS"] = "999"
        arquivo = self.dir / ".env"
        arquivo.write_text("YTR_PISO_SEGUNDOS=111\n", encoding="utf-8")
        load_dotenv(arquivo)
        self.assertEqual("999", os.environ["YTR_PISO_SEGUNDOS"])

    def test_arquivo_ausente_nao_levanta(self):
        load_dotenv(self.dir / "nao_existe")


class TestFromEnv(Base):
    def test_padroes_sem_nenhuma_variavel(self):
        cfg = Config.from_env()
        self.assertEqual(900, cfg.piso_segundos)
        self.assertFalse(cfg.post_enabled)
        self.assertFalse(cfg.avisar_shorts)
        self.assertEqual("none", cfg.llm_backend)
        self.assertEqual(Path(".state"), cfg.state_dir)

    def test_o_canal_de_aviso_nao_tem_padrao(self):
        """Não existe escolha segura, existe recusa."""
        cfg = Config.from_env()
        self.assertEqual("", cfg.canal_aviso)
        with self.assertRaises(ConfigError) as erro:
            cfg.exigir_canal_aviso()
        self.assertIn("não existe padrão seguro", str(erro.exception))

    def test_inteiro_invalido_e_uma_frase_que_nomeia_a_variavel(self):
        os.environ["YTR_PISO_SEGUNDOS"] = "quinze minutos"
        with self.assertRaises(ConfigError) as erro:
            Config.from_env()
        self.assertIn("YTR_PISO_SEGUNDOS", str(erro.exception))
        self.assertIn("quinze minutos", str(erro.exception))

    def test_numero_invalido_e_uma_frase_que_nomeia_a_variavel(self):
        os.environ["YTR_PESO_CANAL"] = "muito"
        with self.assertRaises(ConfigError) as erro:
            Config.from_env()
        self.assertIn("YTR_PESO_CANAL", str(erro.exception))

    def test_valor_vazio_cai_no_padrao_em_vez_de_quebrar(self):
        """`CHAVE=` no `.env` é o estado normal de uma chave nunca configurada."""
        os.environ["YTR_PISO_SEGUNDOS"] = ""
        os.environ["YTR_PESO_CANAL"] = "  "
        cfg = Config.from_env()
        self.assertEqual(900, cfg.piso_segundos)
        self.assertEqual(5.0, cfg.peso_canal)

    def test_backend_de_llm_desconhecido_lista_os_validos(self):
        os.environ["YTR_LLM_BACKEND"] = "gpt-caseiro"
        with self.assertRaises(ConfigError) as erro:
            Config.from_env()
        for valido in BACKENDS_LLM:
            self.assertIn(valido, str(erro.exception))

    def test_todo_backend_declarado_e_aceito(self):
        for backend in BACKENDS_LLM:
            with self.subTest(backend=backend):
                os.environ["YTR_LLM_BACKEND"] = backend
                self.assertEqual(backend, Config.from_env().llm_backend)

    def test_booleano_aceita_as_formas_negativas_usuais(self):
        for bruto in ("0", "false", "FALSE", "no", ""):
            with self.subTest(bruto=bruto):
                os.environ["YTR_POST_ENABLED"] = bruto
                self.assertFalse(Config.from_env().post_enabled)

    def test_booleano_trata_qualquer_outra_coisa_como_verdadeiro(self):
        for bruto in ("1", "true", "sim", "yes", "on"):
            with self.subTest(bruto=bruto):
                os.environ["YTR_POST_ENABLED"] = bruto
                self.assertTrue(Config.from_env().post_enabled)

    def test_donos_e_lista_separada_por_virgula_sem_vazio(self):
        os.environ["YTR_DONOS"] = "11111111111111111, 22222222222222222 ,,"
        self.assertEqual(
            ("11111111111111111", "22222222222222222"), Config.from_env().donos
        )

    def test_o_til_do_vault_e_expandido(self):
        os.environ["OBSIDIAN_VAULT"] = "~/algum/lugar"  # guarda:exemplo
        self.assertNotIn("~", str(Config.from_env().vault_path))

    def test_o_vault_padrao_e_neutro(self):
        """Caminho com nome de pessoa não existe na máquina de mais ninguém, e o sistema
        falharia em silêncio para todo mundo.
        """
        self.assertEqual("~/Documents/Obsidian/vault", VAULT_PADRAO)
        self.assertNotIn("/home/", VAULT_PADRAO)


class TestExigir(Base):
    def test_exigir_discord_nomeia_a_variavel(self):
        with self.assertRaises(ConfigError) as erro:
            Config().exigir_discord()
        self.assertIn("DISCORD_TOKEN", str(erro.exception))

    def test_exigir_canal_entrada_explica_para_que_serve(self):
        with self.assertRaises(ConfigError) as erro:
            Config().exigir_canal_entrada()
        self.assertIn("lê os links", str(erro.exception))

    def test_exigir_vault_pede_a_pasta_certa(self):
        cfg = Config(vault_path=self.dir)
        with self.assertRaises(ConfigError) as erro:
            cfg.exigir_vault()
        self.assertIn("50_LINKS", str(erro.exception))
        self.assertIn("nunca escreve", str(erro.exception))

    def test_exigir_vault_passa_quando_a_pasta_existe(self):
        (self.dir / "50_LINKS").mkdir()
        Config(vault_path=self.dir).exigir_vault()

    def test_todo_exigir_levanta_uma_frase_e_nao_um_paragrafo_de_pilha(self):
        cfg = Config(vault_path=self.dir)
        for exigir in (cfg.exigir_discord, cfg.exigir_canal_aviso,
                       cfg.exigir_canal_entrada, cfg.exigir_vault):
            with self.subTest(exigir=exigir.__name__):
                with self.assertRaises(ConfigError) as erro:
                    exigir()
                self.assertNotIn("Traceback", str(erro.exception))


if __name__ == "__main__":
    unittest.main()
