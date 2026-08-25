"""Rode com: python3 -m unittest tests.test_gosto

O perfil de gosto e o ranqueamento léxico.

A regra que este arquivo protege acima de todas: **o radar nunca escreve no vault.** Não
é omissão, é decisão — uma nota por vídeo novo poria ~20 notas por semana em `50_LINKS/`
que ninguém pediu, afogando as que foram curadas à mão, e o deduplicador do projeto irmão
passaria a ver as nossas como links já arquivados. A costura limpa: **nós avisamos; se
ele quiser guardar, ele posta o link e o outro projeto arquiva.** Efeito colateral bom: o
container monta o vault `:ro` de verdade, e há teste que exercita isso.

O número que decide o desenho: as URLs de vídeo do vault resolvem para muito mais canais
do que vídeos por canal (medido: 77 URLs → 60 canais, 53 com um vídeo só). Por isso a
afinidade de canal é sinal de **cauda longa**, não lista de favoritos.
"""

import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import gosto
from ytr.config import Config
from ytr.gosto import LINKS_DIR, carregar, ler_nota
from ytr.lexico import STOPWORDS, Indice, normalizar, tokenizar

NOTA = """---
url: {url}
capturado: 2026-08-20
categoria: video
tags:
  - area/ia
  - "[[MOC - Captura Discord]]"
triagem: inbox
---

> {resumo}

## Por que importa

{porque}
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.links = self.vault / LINKS_DIR
        self.links.mkdir(parents=True)
        self.state = Path(self.tmp.name) / ".state"
        self.cfg = Config(vault_path=self.vault, state_dir=self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def nota(self, nome, url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
             resumo="Um resumo curto.", porque="Porque fala de agente de IA."):
        caminho = self.links / f"{nome}.md"
        caminho.write_text(NOTA.format(url=url, resumo=resumo, porque=porque), encoding="utf-8")
        return caminho


class TestLerNota(Base):
    def test_le_frontmatter_resumo_e_porque_importa(self):
        caminho = self.nota("Nota Um")
        lida = ler_nota(caminho)
        self.assertIsNotNone(lida)
        self.assertEqual("https://www.youtube.com/watch?v=aaaaaaaaaaa", lida.url)
        self.assertEqual("Nota Um", lida.titulo)
        self.assertEqual("2026-08-20", lida.dia)
        self.assertEqual("video", lida.categoria)
        self.assertEqual("Um resumo curto.", lida.resumo)
        self.assertEqual("Porque fala de agente de IA.", lida.porque_importa)

    def test_o_parser_de_frontmatter_engole_wikilink_sem_quebrar(self):
        """Deliberadamente burro em vez de chamar o YAML: o frontmatter do vault tem
        wikilinks e datas soltas, e um parser completo traria os erros do YAML para um
        caminho que só precisa de quatro campos.
        """
        lida = ler_nota(self.nota("Nota Dois"))
        self.assertIn("area/ia", lida.tags)
        self.assertIn("[[MOC - Captura Discord]]", lida.tags)

    def test_arquivo_sem_frontmatter_e_ignorado(self):
        caminho = self.links / "Sem Frontmatter.md"
        caminho.write_text("só texto solto\n", encoding="utf-8")
        self.assertIsNone(ler_nota(caminho))

    def test_arquivo_ilegivel_devolve_none_em_vez_de_levantar(self):
        self.assertIsNone(ler_nota(self.links / "nao_existe.md"))

    def test_o_texto_de_perfil_junta_titulo_resumo_e_porque(self):
        lida = ler_nota(self.nota("Agentes de IA"))
        self.assertIn("Agentes de IA", lida.texto_de_perfil)
        self.assertIn("Um resumo curto.", lida.texto_de_perfil)
        self.assertIn("agente de IA", lida.texto_de_perfil)


class TestCarregarPerfil(Base):
    def test_vault_ausente_devolve_perfil_vazio_e_nao_erro(self):
        cfg = Config(vault_path=Path(self.tmp.name) / "nao_existe", state_dir=self.state)
        perfil = carregar(cfg)
        self.assertEqual([], perfil.notas)
        self.assertEqual(0, perfil.corpus_chars)

    def test_conta_o_corpus_e_o_tempo_de_leitura(self):
        """Os dois números que decidem quando este desenho deixa de servir. Contados,
        não extrapolados — quando o corpus passar do orçamento de prompt, aí se discute
        vetor.
        """
        for numero in range(3):
            self.nota(f"Nota {numero}")
        perfil = carregar(self.cfg)
        self.assertEqual(3, len(perfil.notas))
        self.assertGreater(perfil.corpus_chars, 0)
        self.assertGreaterEqual(perfil.leitura_ms, 0.0)

    def test_afinidade_de_tag_conta_as_notas(self):
        for numero in range(3):
            self.nota(f"Nota {numero}")
        perfil = carregar(self.cfg)
        self.assertEqual(3, perfil.afinidade_tag["area/ia"])

    def test_sem_mapa_de_canal_a_afinidade_de_canal_fica_vazia(self):
        """O vault guarda a URL do **vídeo**, não a do canal.

        Resolver as URLs custa rede que não cabe dentro de um ciclo de 15 minutos — daí
        o mapa ser construído uma vez e guardado em cache por quem chama.
        """
        self.nota("Nota Um")
        perfil = carregar(self.cfg)
        self.assertEqual(1, len(perfil.urls_conhecidas))
        self.assertEqual(0, len(perfil.afinidade_canal))

    def test_com_mapa_de_canal_a_afinidade_e_contada_por_handle(self):
        url_a = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
        url_b = "https://www.youtube.com/watch?v=bbbbbbbbbbb"
        self.nota("Nota A", url=url_a)
        self.nota("Nota B", url=url_b)
        perfil = carregar(self.cfg, mapa_canal={url_a: "@umCanal", url_b: "@umCanal"})
        self.assertEqual(2, perfil.afinidade_canal["umcanal"])
        self.assertEqual(["umcanal"], perfil.canais_do_pool)

    def test_url_que_nao_e_do_youtube_nao_entra_no_perfil(self):
        self.nota("Artigo", url="https://exemplo.test/artigo")
        perfil = carregar(self.cfg)
        self.assertEqual(0, len(perfil.urls_conhecidas))

    def test_polegar_para_baixo_e_contado_separado_do_para_cima(self):
        sinais = [
            {"handle": "@ruim", "reacao": "👎"},
            {"handle": "@bom", "reacao": "👍"},
            {"handle": "@bom", "reacao": "👍"},
        ]
        perfil = carregar(self.cfg, sinais=sinais)
        self.assertEqual(1, perfil.polegar_baixo_canal["ruim"])
        self.assertEqual(2, perfil.polegar_cima_canal["bom"])
        self.assertEqual(2, perfil.afinidade_canal["bom"], "o 👍 também soma afinidade")
        self.assertEqual(0, perfil.afinidade_canal["ruim"])

    def test_sinal_sem_canal_e_ignorado(self):
        perfil = carregar(self.cfg, sinais=[{"reacao": "👍"}, {"handle": "", "reacao": "👎"}])
        self.assertEqual(0, len(perfil.afinidade_canal))
        self.assertEqual(0, len(perfil.polegar_baixo_canal))

    def test_video_postado_soma_afinidade_de_canal(self):
        """`ytr.cadastro._sinalizar_video` grava `{"tipo": "postado", ...}` sem
        `reacao` — é o sinal fraco de "ele postou isso", mesmo peso de "ele salvou
        no vault" (D7)."""
        sinais = [
            {"handle": "@umcanal", "tipo": "postado", "video_id": "v1"},
            {"handle": "@umcanal", "tipo": "postado", "video_id": "v2"},
        ]
        perfil = carregar(self.cfg, sinais=sinais)
        self.assertEqual(2, perfil.afinidade_canal["umcanal"])
        self.assertEqual(["umcanal"], perfil.canais_do_pool)

    def test_video_postado_sem_handle_usa_channel_id(self):
        sinais = [{"channel_id": "UC" + "1" * 22, "tipo": "postado", "video_id": "v1"}]
        perfil = carregar(self.cfg, sinais=sinais)
        self.assertEqual(1, perfil.afinidade_canal[("UC" + "1" * 22).casefold()])

    def test_sinal_sem_reacao_nem_tipo_postado_nao_soma_nada(self):
        """Um sinal desconhecido (nem reação, nem "postado") não deve contar como
        afinidade em silêncio — só os tipos declarados somam."""
        perfil = carregar(self.cfg, sinais=[{"handle": "@umcanal", "tipo": "algo_novo"}])
        self.assertEqual(0, perfil.afinidade_canal["umcanal"])

    def test_a_pontuacao_de_afinidade_penaliza_o_polegar_baixo(self):
        perfil = carregar(self.cfg, sinais=[{"handle": "@ruim", "reacao": "👎"}])
        componentes = perfil.pontuar_afinidade(self.cfg, "UC…", "@ruim")
        self.assertLess(componentes["polegar_baixo"], 0)
        self.assertEqual(-self.cfg.peso_polegar_baixo, componentes["polegar_baixo"])

    def test_a_pontuacao_usa_os_pesos_da_config(self):
        url = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
        self.nota("Nota A", url=url)
        cfg = Config(vault_path=self.vault, state_dir=self.state, peso_canal=7.0, peso_tag=3.0)
        perfil = carregar(cfg, mapa_canal={url: "@umCanal"})
        componentes = perfil.pontuar_afinidade(cfg, "UC…", "@umCanal", tags_do_canal=["area/ia"])
        self.assertEqual(7.0, componentes["canal"])
        self.assertEqual(3.0, componentes["tag"])

    @unittest.skipIf(os.geteuid() == 0, "root lê e escreve em diretório somente-leitura")
    def test_le_o_vault_montado_somente_leitura(self):
        """Critério de pronto da Fase 6: roda com o vault `:ro`.

        Se alguma coisa aqui escrevesse no vault, este teste seria o que quebraria.
        """
        self.nota("Nota Um")
        os.chmod(self.links, stat.S_IRUSR | stat.S_IXUSR)
        os.chmod(self.vault, stat.S_IRUSR | stat.S_IXUSR)
        try:
            perfil = carregar(self.cfg)
            self.assertEqual(1, len(perfil.notas))
        finally:
            os.chmod(self.vault, stat.S_IRWXU)
            os.chmod(self.links, stat.S_IRWXU)

    def test_carregar_nao_cria_nem_altera_nada_no_vault(self):
        self.nota("Nota Um")
        antes = {p.name: p.stat().st_mtime_ns for p in self.links.rglob("*")}
        carregar(self.cfg)
        depois = {p.name: p.stat().st_mtime_ns for p in self.links.rglob("*")}
        self.assertEqual(antes, depois, "o radar só lê o vault; nunca escreve nele")


class TestLexico(unittest.TestCase):
    def test_normalizar_tira_acento_e_caixa(self):
        self.assertEqual("avaliacao", normalizar("Avaliação"))
        self.assertEqual("orgao publico", normalizar("Órgão Público"))

    def test_tokenizar_descarta_stopword_e_token_de_uma_letra(self):
        tokens = tokenizar("O agente de IA e a rede")
        self.assertIn("agente", tokens)
        self.assertIn("ia", tokens)
        self.assertIn("rede", tokens)
        self.assertNotIn("o", tokens)
        self.assertNotIn("de", tokens)
        self.assertNotIn("e", tokens)

    def test_a_stoplist_cobre_portugues_e_ingles(self):
        """Título do YouTube é metade em inglês: uma stoplist só de português deixaria
        "the", "how" e "and" dominarem o IDF.
        """
        for palavra in ("the", "and", "how", "what", "de", "que", "para"):
            self.assertIn(palavra, STOPWORDS)

    def test_o_idf_desvaloriza_o_termo_que_esta_em_tudo(self):
        """Sem IDF, o termo que aparece na maioria das notas pontuaria tanto quanto o
        que de fato distingue um vídeo dos outros.
        """
        documentos = ["ia e agente"] * 9 + ["compilador"]
        indice = Indice(documentos)
        self.assertLess(indice.idf("ia"), indice.idf("compilador"))

    def test_casamento_por_prefixo_a_partir_de_quatro_letras(self):
        """`agente` casa `agentes` sem stemmer — e sem o falso positivo que um prefixo
        de duas letras traria.
        """
        indice = Indice(["um documento qualquer"])
        campos = {"titulo": ("Vários agentes autônomos", 1.0)}
        self.assertGreater(indice.pontuar("agente", campos), 0)

    def test_prefixo_curto_nao_casa_por_prefixo(self):
        indice = Indice(["um documento qualquer"])
        campos = {"titulo": ("Isso não tem a ver", 1.0)}
        self.assertEqual(0.0, indice.pontuar("ia", campos))

    def test_consulta_sem_termo_util_pontua_zero(self):
        indice = Indice(["documento"])
        self.assertEqual(0.0, indice.pontuar("de o e a", {"t": ("qualquer coisa", 1.0)}))

    def test_campo_com_peso_maior_pontua_mais(self):
        indice = Indice(["documento"])
        so_titulo = indice.pontuar("agente", {"titulo": ("um agente", 5.0)})
        so_corpo = indice.pontuar("agente", {"corpo": ("um agente", 1.0)})
        self.assertGreater(so_titulo, so_corpo)

    def test_campo_vazio_nao_pontua(self):
        indice = Indice(["documento"])
        self.assertEqual(0.0, indice.pontuar("agente", {"titulo": ("", 5.0)}))

    def test_indice_de_corpus_vazio_nao_divide_por_zero(self):
        indice = Indice([])
        self.assertEqual(1, indice.total)
        self.assertGreaterEqual(indice.idf("qualquer"), 0.0)


if __name__ == "__main__":
    unittest.main()
