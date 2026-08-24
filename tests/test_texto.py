"""Rode com: python3 -m unittest tests.test_texto

O texto do aviso. **Determinístico, sem modelo** — e isso é decisão de disponibilidade,
não de economia: o login local do `claude` já é disputado por três consumidores do
projeto irmão, e o radar quer rodar de 15 em 15 minutos. Um aviso que depende de quota é
um aviso que some no dia em que a quota acaba.

A invariante que este arquivo mais protege: **toda mensagem carrega a procedência do
resumo.** Se o texto é a descrição que o autor escreveu, a mensagem diz isso; se não
havia descrição, ela diz que só tem o título. O sistema nunca dá a entender que assistiu
ao vídeo.
"""

import unittest

from ytr.feed import Video
from ytr.texto import (
    MINIMO_DESCRICAO, aviso, cabecalho_de_digest, item_de_digest, limpar_descricao,
    resumo, truncar,
)

LONGA = (
    "Neste vídeo eu mostro como montar um sistema de monitoração que não depende de "
    "chave de API, lendo o RSS que o YouTube publica por canal. Falo do custo real de "
    "banda, do que o feed traz de graça, e de por que a deduplicação tem de ser por "
    "identificador e nunca por data de publicação."
)


def video(**campos) -> Video:
    base = dict(video_id="aaaaaaaaaaa", titulo="Um título", url="https://youtu.be/aaaaaaaaaaa",
                canal="Canal de Teste")
    base.update(campos)
    return Video(**base)


class TestLimparDescricao(unittest.TestCase):
    def test_linha_que_e_so_link_sai(self):
        self.assertEqual("texto útil", limpar_descricao("texto útil\nhttps://exemplo.test/x"))

    def test_linha_que_e_so_hashtag_sai(self):
        self.assertEqual("texto útil", limpar_descricao("texto útil\n#tag #outra"))

    def test_rodape_de_inscricao_sai_nos_dois_idiomas(self):
        for rodape in ("Subscribe for more", "Inscreva-se no canal", "Follow us on X",
                       "Siga a gente"):
            with self.subTest(rodape=rodape):
                self.assertEqual("texto útil", limpar_descricao(f"texto útil\n{rodape}"))

    def test_devolve_o_primeiro_paragrafo_util(self):
        """Descrição do YouTube é metade rodapé de patrocínio."""
        bruta = "Primeiro parágrafo.\n\nSegundo parágrafo, que não entra."
        self.assertEqual("Primeiro parágrafo.", limpar_descricao(bruta))

    def test_espaco_repetido_e_normalizado(self):
        self.assertEqual("a b c", limpar_descricao("a   b\n c"))

    def test_descricao_vazia_ou_nula(self):
        self.assertEqual("", limpar_descricao(""))
        self.assertEqual("", limpar_descricao(None))


class TestTruncar(unittest.TestCase):
    def test_texto_curto_passa_inteiro(self):
        self.assertEqual("curto", truncar("curto", limite=100))

    def test_corta_em_fronteira_de_frase_quando_da(self):
        texto = "Primeira frase completa aqui. " + "x" * 100
        cortado = truncar(texto, limite=40)
        self.assertTrue(cortado.endswith("."), cortado)
        self.assertEqual("Primeira frase completa aqui.", cortado)

    def test_a_fronteira_de_frase_e_recusada_quando_gastaria_menos_da_metade(self):
        """`corte > limite // 2` — a fronteira de frase só vale se não desperdiçar o
        orçamento.

        Com `limite=60`, o ponto do texto abaixo cai no caractere 28: cortar ali
        devolveria menos da metade do que cabia, e um resumo curto demais informa menos
        que um resumo cortado na palavra. Então o código prefere a fronteira de palavra,
        mais longa. Está aqui como asserção porque o comportamento parece defeito quando
        se olha só o `endswith(".")` — e não é.
        """
        texto = "Primeira frase completa aqui. " + "x" * 100
        cortado = truncar(texto, limite=60)
        self.assertFalse(cortado.endswith("."))
        self.assertTrue(cortado.endswith("…"))
        self.assertGreater(len(cortado), len("Primeira frase completa aqui."))

    def test_corta_em_fronteira_de_palavra_quando_nao_ha_frase(self):
        """Cortar no meio de uma palavra faz a mensagem parecer quebrada, não resumida."""
        texto = "palavra " * 30
        cortado = truncar(texto, limite=50)
        self.assertTrue(cortado.endswith("…"))
        self.assertNotIn("palav…", cortado)

    def test_o_resultado_nunca_estoura_muito_o_limite(self):
        for limite in (20, 50, 100, 400):
            with self.subTest(limite=limite):
                cortado = truncar("palavra " * 200, limite=limite)
                self.assertLessEqual(len(cortado), limite + 1)


class TestProcedencia(unittest.TestCase):
    def test_descricao_longa_vira_resumo_com_procedencia_descricao(self):
        texto, procedencia = resumo(video(descricao=LONGA))
        self.assertEqual("descricao", procedencia)
        self.assertTrue(texto)

    def test_descricao_curta_nao_e_resumo_e_sim_divulgacao(self):
        """Abaixo do piso, a descrição não resume nada: é uma linha de divulgação."""
        texto, procedencia = resumo(video(descricao="Vídeo novo! Confira."))
        self.assertEqual("so_titulo", procedencia)
        self.assertEqual("", texto)

    def test_o_piso_e_o_declarado_no_modulo(self):
        curta = "a" * (MINIMO_DESCRICAO - 1)
        longa = "a" * MINIMO_DESCRICAO
        self.assertEqual("so_titulo", resumo(video(descricao=curta))[1])
        self.assertEqual("descricao", resumo(video(descricao=longa))[1])

    def test_sem_descricao_e_so_titulo(self):
        self.assertEqual("so_titulo", resumo(video())[1])


class TestAviso(unittest.TestCase):
    """Fase 7.1 — pedido do Dotcom (2026-08-24): mensagem simplificada, uma frase no
    máximo, e link solto para o Discord gerar prévia. A invariante de honestidade
    (nunca dá a entender que assistiu ao vídeo) continua, só que mais compacta: com
    descrição, a frase **é** a própria descrição do autor (citação, não invenção); sem
    descrição, uma frase curta diz isso explicitamente.
    """

    def test_com_descricao_a_mensagem_cita_a_propria_descricao(self):
        mensagem = aviso(video(descricao=LONGA))
        self.assertIn("como montar um sistema de monitoração", mensagem)

    def test_sem_descricao_a_mensagem_diz_que_nao_leu_o_conteudo(self):
        """O sistema nunca dá a entender que assistiu ao vídeo."""
        mensagem = aviso(video())
        self.assertIn("não li o conteúdo", mensagem)
        self.assertIn("só o título", mensagem)

    def test_a_mensagem_traz_titulo_canal_e_url(self):
        mensagem = aviso(video(descricao=LONGA))
        self.assertIn("Um título", mensagem)
        self.assertIn("Canal de Teste", mensagem)
        self.assertIn("https://youtu.be/aaaaaaaaaaa", mensagem)

    def test_o_video_id_continua_recuperavel_por_dentro_da_url(self):
        """Não tem mais linha própria — mas a recuperação depois de um POST ambíguo
        (buscar a mensagem nas últimas do canal) ainda acha por substring na URL.
        """
        self.assertIn("aaaaaaaaaaa", aviso(video()))

    def test_a_url_vai_solta_para_o_discord_mostrar_a_previa(self):
        """Pedido do Dotcom: link "de verdade", clicável, com card — não mais suprimido
        por `<>`. Depende da permissão *Embed Links*, que ele habilitou no bot.
        """
        mensagem = aviso(video())
        self.assertIn("https://youtu.be/aaaaaaaaaaa", mensagem)
        self.assertNotIn("<https://youtu.be/aaaaaaaaaaa>", mensagem)

    def test_e_uma_frase_so(self):
        """"Uma frase no máximo" — mesmo com descrição de várias frases, só a primeira
        entra na mensagem."""
        mensagem = aviso(video(descricao=LONGA))
        self.assertNotIn("Falo do custo real de banda", mensagem)


class TestItemDeDigest(unittest.TestCase):
    def test_traz_o_porque_e_o_par_de_reacoes(self):
        """Mensagem por candidato, e não um digest único com cinco vídeos: um 👍 numa
        mensagem que contém cinco vídeos não diz qual agradou.
        """
        mensagem = item_de_digest(video(descricao=LONGA), razao="canal que você salvou 5×")
        self.assertIn("canal que você salvou 5×", mensagem)
        self.assertIn("👍 / 👎", mensagem)
        self.assertIn("aaaaaaaaaaa", mensagem)

    def test_o_emoji_de_reacao_nao_e_marca_do_bot(self):
        """👍/👎 são reação **humana**, não marca que o bot põe.

        Por isso não entram em `Config.marcas` nem na guarda de interseção com o
        vizinho: nada aqui posta esses emojis em nome do bot.
        """
        from ytr.config import MARCAS_DO_VIZINHO, Config
        self.assertNotIn("👍", Config().marcas)
        self.assertNotIn("👎", Config().marcas)
        self.assertNotIn("👍", MARCAS_DO_VIZINHO)
        self.assertNotIn("👎", MARCAS_DO_VIZINHO)


class TestCabecalhoDeDigest(unittest.TestCase):
    def test_sem_modelo_diz_que_as_regras_decidiram(self):
        cabecalho = cabecalho_de_digest(3, narracao="", com_modelo=False)
        self.assertIn("ranking sem modelo", cabecalho)
        self.assertIn("3 recomendação(ões)", cabecalho)

    def test_com_modelo_traz_a_narracao_e_omite_o_aviso(self):
        cabecalho = cabecalho_de_digest(3, narracao="Hoje o tema é agente.", com_modelo=True)
        self.assertIn("Hoje o tema é agente.", cabecalho)
        self.assertNotIn("ranking sem modelo", cabecalho)

    def test_explica_que_a_reacao_vai_na_mensagem_do_item(self):
        cabecalho = cabecalho_de_digest(3, narracao="", com_modelo=False)
        self.assertIn("na mensagem de cada item", cabecalho)


if __name__ == "__main__":
    unittest.main()
