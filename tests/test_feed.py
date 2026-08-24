"""Rode com: python3 -m unittest tests.test_feed

O parser do RSS de canal. É o único módulo com fixture real — o XML de 26 KB de um
canal público do Google — e por isso é onde as medições do plano viram asserção.

Três invariantes que este arquivo protege, e cada uma custou uma medição:

1. **A deduplicação é por `video_id`, nunca por data.** Medido no feed real: `updated`
   chega 15 minutos depois de `published` no mesmo item. Data se move; id não.
2. **O `yt:channelId` do topo não é confiável.** No feed real do canal
   `UC_x5XG1OV2P6uZZ5FSM9Ttw`, o elemento do topo traz o id **sem o prefixo `UC`**,
   enquanto o de cada `<entry>` traz o id completo. Persistir o do topo daria uma chave
   que nenhuma URL de feed aceita, e o erro só apareceria no ciclo seguinte, como
   "canal que nunca tem vídeo novo".
3. **XML malformado não pode levantar exceção crua.** `ParseError` de `xml.etree` no log
   não diz qual canal quebrou, e derrubaria o ciclo inteiro por causa de um canal.
"""

import unittest
from pathlib import Path

from ytr import feed as mod_feed
from ytr.feed import FeedInvalido, Feed, Video, parse, proxima_em

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "feed.xml"

# O que foi contado no fixture, com o parser rodando. Os números estão aqui como
# asserção e não como comentário porque o plano trouxe um deles errado: um trecho dele
# diz "exatamente 1 marcada SHORT", lido de uma frase que dizia que o **primeiro** item
# era um Short. São coisas diferentes, e a medição é que manda.
ENTRADAS_NO_FIXTURE = 15
SHORTS_NO_FIXTURE = 8
CANAL_DO_FIXTURE = "UC_x5XG1OV2P6uZZ5FSM9Ttw"

CABECA = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"'
    ' xmlns:media="http://search.yahoo.com/mrss/"'
    ' xmlns="http://www.w3.org/2005/Atom">'
)


def montar(entradas: str = "", channel_id: str = CANAL_DO_FIXTURE, titulo: str = "Canal") -> str:
    return (
        f"{CABECA}<yt:channelId>{channel_id}</yt:channelId>"
        f"<title>{titulo}</title>{entradas}</feed>"
    )


def entrada(video_id="abcdefghijk", titulo="Um vídeo", href=None, descricao=None,
            publicado="2026-08-18T16:00:07+00:00", atualizado="2026-08-18T16:15:58+00:00",
            channel_id=CANAL_DO_FIXTURE, views=None, media=None, quantos=None) -> str:
    href = href or f"https://www.youtube.com/watch?v={video_id}"
    partes = [
        "<entry>",
        f"<yt:videoId>{video_id}</yt:videoId>",
        f"<yt:channelId>{channel_id}</yt:channelId>",
        f"<title>{titulo}</title>",
        f'<link rel="alternate" href="{href}"/>',
        f"<published>{publicado}</published>",
        f"<updated>{atualizado}</updated>",
        "<author><name>Nome do Canal</name></author>",
        "<media:group>",
    ]
    if descricao is not None:
        partes.append(f"<media:description>{descricao}</media:description>")
    if views is not None or media is not None:
        partes.append("<media:community>")
        if media is not None:
            partes.append(
                f'<media:starRating count="{quantos or 0}" average="{media}" min="1" max="5"/>'
            )
        if views is not None:
            partes.append(f'<media:statistics views="{views}"/>')
        partes.append("</media:community>")
    partes += ["</media:group>", "</entry>"]
    return "".join(partes)


class TestFixtureReal(unittest.TestCase):
    """As medições do plano, viradas em asserção contra o XML de verdade."""

    @classmethod
    def setUpClass(cls):
        cls.feed = parse(FIXTURE.read_text(encoding="utf-8"))

    def test_o_feed_e_fechado_em_quinze_entradas(self):
        """Medido: 15, e `?playlist_id=UU…` também devolve 15.

        Não existe backfill histórico por RSS — o que sustenta a decisão de semear em
        vez de tentar reconstruir catálogo.
        """
        self.assertEqual(ENTRADAS_NO_FIXTURE, len(self.feed.videos))

    def test_oito_das_quinze_entradas_sao_shorts(self):
        """**Oito**, não uma.

        O critério de pronto da Fase 1 no plano diz "exatamente 1 marcada `SHORT`", e
        isso é erro de transcrição: a §1.4 dele diz que o **primeiro item** era um Short,
        e alguém leu "primeiro" como "único". Contado aqui: 8 de 15 — mais da metade, que
        é justamente o número que justifica `avisar_shorts: false` por padrão. Sem esse
        filtro o radar seria uma torneira de Shorts.
        """
        shorts = [v for v in self.feed.videos if v.is_short]
        self.assertEqual(SHORTS_NO_FIXTURE, len(shorts))
        self.assertTrue(self.feed.videos[0].is_short, "o primeiro item do fixture é Short")

    def test_o_channel_id_vem_das_entradas_e_nao_do_topo(self):
        """O do topo vem **sem o prefixo `UC`** neste feed real.

        Persistir o do topo daria uma chave que nenhuma URL de feed aceita.
        """
        self.assertEqual(CANAL_DO_FIXTURE, self.feed.channel_id)
        self.assertTrue(self.feed.channel_id.startswith("UC"))

    def test_publicado_difere_de_atualizado_no_mesmo_item(self):
        """A medição que obriga a dedup por id: data se move dentro do mesmo vídeo."""
        divergentes = [
            v for v in self.feed.videos
            if v.publicado and v.atualizado and v.publicado != v.atualizado
        ]
        self.assertTrue(
            divergentes,
            "nenhum item do fixture tem `published` != `updated` — se isso mudou, a "
            "justificativa medida para deduplicar por id precisa ser remedida.",
        )

    def test_todo_video_traz_os_doze_campos(self):
        for video in self.feed.videos:
            with self.subTest(video=video.video_id):
                self.assertTrue(video.video_id)
                self.assertTrue(video.titulo)
                self.assertTrue(video.url)
                self.assertEqual(CANAL_DO_FIXTURE, video.channel_id)
                self.assertTrue(video.canal)
                self.assertTrue(video.publicado)
                self.assertTrue(video.descricao)
                self.assertGreater(video.views, 0)

    def test_os_ids_do_fixture_nao_repetem(self):
        ids = [v.video_id for v in self.feed.videos]
        self.assertEqual(len(ids), len(set(ids)))


class TestCasosDeBorda(unittest.TestCase):
    def test_feed_com_zero_entradas(self):
        """Canal novo, ou canal que apagou tudo. Não é erro: é feed vazio."""
        feed = parse(montar())
        self.assertEqual([], feed.videos)
        self.assertEqual(CANAL_DO_FIXTURE, feed.channel_id)

    def test_entrada_sem_media_description(self):
        """Descrição ausente é o caso comum, não a exceção — e vira `so_titulo`."""
        feed = parse(montar(entrada(descricao=None)))
        self.assertEqual(1, len(feed.videos))
        self.assertEqual("", feed.videos[0].descricao)

    def test_entrada_sem_media_community(self):
        feed = parse(montar(entrada()))
        self.assertEqual(0, feed.videos[0].views)
        self.assertEqual(0.0, feed.videos[0].rating_media)
        self.assertEqual(0, feed.videos[0].rating_n)

    def test_xml_malformado_levanta_feed_invalido_e_nao_parse_error(self):
        """Exceção nomeada, nunca `ParseError` cru.

        Um traceback de `xml.etree` no log não diz qual canal quebrou — e quem chama
        precisa distinguir "o YouTube devolveu página de erro" de "o disco corrompeu".
        """
        for bruto in ("<feed", "não é xml de jeito nenhum", "", "<feed><entry></feed>"):
            with self.subTest(bruto=bruto[:20]):
                with self.assertRaises(FeedInvalido):
                    parse(bruto)

    def test_xml_valido_que_nao_e_feed_do_atom(self):
        with self.assertRaises(FeedInvalido) as erro:
            parse('<?xml version="1.0"?><html><body>erro 404</body></html>')
        self.assertIn("esperava <feed>", str(erro.exception))

    def test_entrada_sem_video_id_e_pulada_sem_derrubar_as_outras(self):
        """Tolerante por dentro: perder um item é aceitável, perder o ciclo não.

        O YouTube já mudou este XML antes e vai mudar de novo.
        """
        sem_id = "<entry><title>quebrada</title></entry>"
        feed = parse(montar(sem_id + entrada(video_id="aaaaaaaaaaa")))
        self.assertEqual(1, len(feed.videos))
        self.assertEqual("aaaaaaaaaaa", feed.videos[0].video_id)

    def test_shorts_pelo_href_do_link(self):
        curto = entrada(video_id="bbbbbbbbbbb", href="https://www.youtube.com/shorts/bbbbbbbbbbb")
        longo = entrada(video_id="ccccccccccc", href="https://www.youtube.com/watch?v=ccccccccccc")
        feed = parse(montar(curto + longo))
        self.assertTrue(feed.videos[0].is_short)
        self.assertFalse(feed.videos[1].is_short)

    def test_views_e_rating_ilegiveis_viram_zero_em_vez_de_excecao(self):
        bruto = montar(entrada(views="muitas", media="cinco", quantos="varios"))
        feed = parse(bruto)
        self.assertEqual(0, feed.videos[0].views)
        self.assertEqual(0.0, feed.videos[0].rating_media)

    def test_url_cai_para_watch_quando_o_link_falta(self):
        sem_link = (
            "<entry><yt:videoId>ddddddddddd</yt:videoId>"
            "<title>sem link</title></entry>"
        )
        feed = parse(montar(sem_link))
        self.assertEqual("https://www.youtube.com/watch?v=ddddddddddd", feed.videos[0].url)
        self.assertFalse(feed.videos[0].is_short)


class TestVideo(unittest.TestCase):
    def test_publicado_em_ilegivel_devolve_none_e_idade_menos_um(self):
        video = Video(video_id="x", titulo="t", url="u", publicado="não é data")
        self.assertIsNone(video.publicado_em)
        self.assertEqual(-1.0, video.idade_dias())

    def test_feed_sem_videos_nao_compartilha_lista_entre_instancias(self):
        """`videos: list = None` com `__post_init__` — o remédio para o mutável padrão.

        Se as duas instâncias compartilhassem a lista, semear um canal contaminaria o
        outro, e o sintoma apareceria como "canal que já nasceu semeado".
        """
        a, b = Feed(), Feed()
        a.videos.append(Video(video_id="x", titulo="t", url="u"))
        self.assertEqual([], b.videos)


class TestCadencia(unittest.TestCase):
    """`max(piso, max_age - age)`, com **`age` ausente valendo 0**.

    Medido: `max-age=900` em 5 de 5 canais, e `age` em apenas 3 de 5. Assumir `age=0`
    quando falta é a escolha conservadora — espera o `max-age` inteiro em vez de bater
    cedo e gastar uma requisição garantidamente morna. O erro é para o lado de gastar
    menos banda.
    """

    def test_max_age_menos_age(self):
        self.assertEqual(600, proxima_em({"cache-control": "max-age=900", "age": "300"}, 60))

    def test_age_ausente_vale_zero_e_espera_o_max_age_inteiro(self):
        self.assertEqual(900, proxima_em({"cache-control": "max-age=900"}, 60))

    def test_o_piso_ganha_quando_a_conta_da_menos(self):
        self.assertEqual(
            900, proxima_em({"cache-control": "max-age=900", "age": "890"}, 900)
        )

    def test_sem_cache_control_usa_o_max_age_medido(self):
        self.assertEqual(mod_feed.MAX_AGE_PADRAO, proxima_em({}, 60))
        self.assertEqual(mod_feed.MAX_AGE_PADRAO, proxima_em(None, 60))

    def test_cabecalho_e_lido_sem_depender_de_caixa(self):
        """O `requests` normaliza, mas um dublê de teste não — e aí o guarda cai."""
        self.assertEqual(
            600, proxima_em({"Cache-Control": "max-age=900", "Age": "300"}, 60)
        )

    def test_age_ilegivel_vale_zero(self):
        self.assertEqual(900, proxima_em({"cache-control": "max-age=900", "age": "?"}, 60))


if __name__ == "__main__":
    unittest.main()
