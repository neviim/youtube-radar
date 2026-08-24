"""Rode com: python3 -m unittest tests.test_pool

O pool de recomendação e o digest (Fase 7, D7 do plano):

- `resolver_pool` resolve cada handle do pool **uma vez** e guarda no mapa.
- `buscar_pool` busca um feed por canal mapeado — grade lenta, não monitora.
- `montar_candidatos` combina afinidade de canal/tag, léxico e engajamento — cada
  canal contribui no máximo `CANDIDATOS_POR_CANAL` candidatos.
- `selecionar` respeita o teto do digest e o teto por canal, e só verifica liveness de
  quem entraria.
- `capturar_feedback` relê 👍/👎 de itens já enviados e grava sinal, idempotente.
"""

import unittest
from dataclasses import replace

from ytr import ledger, pool
from ytr.canal import CanalError, Resolucao
from ytr.canal import ChannelId
from ytr.config import Config
from ytr.feed import Video
from ytr.gosto import Perfil
from ytr.lexico import Indice


def _video(video_id, titulo="", publicado="", canal="", channel_id="", descricao="", views=0):
    return Video(
        video_id=video_id, titulo=titulo, url=f"https://youtu.be/{video_id}",
        channel_id=channel_id, canal=canal, publicado=publicado, descricao=descricao,
        views=views,
    )


class TestResolverPool(unittest.TestCase):
    def test_resolve_so_o_que_ainda_nao_esta_no_mapa(self):
        chamadas = []

        def fake_resolver(alvo, cliente):
            chamadas.append(alvo.url)
            return Resolucao(channel_id=ChannelId("UC" + "9" * 22), nome="X")

        original = pool.resolver
        pool.resolver = fake_resolver
        try:
            perfil = Perfil()
            perfil.afinidade_canal["novocanal"] = 1
            perfil.afinidade_canal["jaresolvido"] = 1
            mapa = pool.resolver_pool(perfil, cliente=None, mapa={"jaresolvido": "UC" + "1" * 22})
        finally:
            pool.resolver = original

        self.assertEqual(["https://youtube.com/@novocanal"], chamadas)
        self.assertEqual("UC" + "9" * 22, mapa["novocanal"])
        self.assertEqual("UC" + "1" * 22, mapa["jaresolvido"], "não resolveu de novo")

    def test_falha_de_resolucao_nao_derruba_o_resto(self):
        def fake_resolver(alvo, cliente):
            raise CanalError("não achei")

        original = pool.resolver
        pool.resolver = fake_resolver
        try:
            perfil = Perfil()
            perfil.afinidade_canal["fantasma"] = 1
            mapa = pool.resolver_pool(perfil, cliente=None, mapa={})
        finally:
            pool.resolver = original
        self.assertEqual({}, mapa)


class TestBuscarPool(unittest.TestCase):
    def test_busca_um_feed_por_canal_mapeado(self):
        from ytr.feed import Feed

        chamados = []

        def fake_buscar(cfg, canal, cliente, estado):
            chamados.append(canal.channel_id)
            return Feed(channel_id=canal.channel_id, videos=[_video("v1")]), None, ""

        original = pool.buscar
        pool.buscar = fake_buscar
        try:
            saida = pool.buscar_pool(cfg=None, mapa={"a": "UC" + "1" * 22, "b": "UC" + "2" * 22}, cliente=None)
        finally:
            pool.buscar = original

        self.assertEqual(2, len(saida))
        self.assertEqual({"UC" + "1" * 22, "UC" + "2" * 22}, set(chamados))

    def test_canal_com_erro_e_pulado(self):
        def fake_buscar(cfg, canal, cliente, estado):
            return None, None, "500"

        original = pool.buscar
        pool.buscar = fake_buscar
        try:
            saida = pool.buscar_pool(cfg=None, mapa={"a": "UC" + "1" * 22}, cliente=None)
        finally:
            pool.buscar = original
        self.assertEqual([], saida)


class TestMontarCandidatos(unittest.TestCase):
    def _cfg(self):
        return Config(peso_canal=5.0, peso_tag=2.0, peso_lexico=1.0,
                      peso_engajamento=1.5, peso_recencia=1.0, peso_polegar_baixo=8.0)

    def test_canal_afim_pontua_mais_que_canal_desconhecido(self):
        cfg = self._cfg()
        perfil = Perfil()
        perfil.afinidade_canal["favorito"] = 5

        pool_dados = [
            ("favorito", [_video("v1", titulo="qualquer coisa", publicado="2026-08-20T00:00:00+00:00")]),
            ("desconhecido", [_video("v2", titulo="qualquer coisa", publicado="2026-08-20T00:00:00+00:00")]),
        ]
        candidatos = pool.montar_candidatos(cfg, perfil, pool_dados)
        por_handle = {c.handle: c for c in candidatos}
        self.assertGreater(por_handle["favorito"].score, por_handle["desconhecido"].score)
        self.assertEqual("canal que você já acompanha", por_handle["favorito"].razao)

    def test_indice_lexico_favorece_titulo_parecido_com_o_perfil(self):
        from pathlib import Path

        from ytr.gosto import NotaDeLink

        cfg = self._cfg()
        perfil = Perfil()
        # `montar_candidatos` monta a consulta a partir de `perfil.notas` (é o que
        # `gosto.carregar` também popularia) — o índice tem de vir do mesmo texto,
        # senão a consulta fica vazia e o léxico pontua zero para todo mundo.
        perfil.notas = [NotaDeLink(caminho=Path("x.md"),
                                    resumo="agente de inteligência artificial para escritório")]
        perfil.indice = Indice([n.texto_de_perfil for n in perfil.notas])

        pool_dados = [
            ("a", [_video("v1", titulo="agente de ia review completo",
                           publicado="2026-08-20T00:00:00+00:00")]),
            ("b", [_video("v2", titulo="receita de bolo de cenoura",
                           publicado="2026-08-20T00:00:00+00:00")]),
        ]
        candidatos = pool.montar_candidatos(cfg, perfil, pool_dados)
        por_handle = {c.handle: c for c in candidatos}
        self.assertGreater(por_handle["a"].componentes["lexico"], por_handle["b"].componentes["lexico"])

    def test_so_os_n_mais_recentes_por_canal_entram(self):
        cfg = self._cfg()
        perfil = Perfil()
        videos = [
            _video(f"v{i}", publicado=f"2026-08-{i:02d}T00:00:00+00:00")
            for i in range(1, 10)
        ]
        candidatos = pool.montar_candidatos(cfg, perfil, [("canal", videos)])
        self.assertEqual(pool.CANDIDATOS_POR_CANAL, len(candidatos))
        # os mais recentes (dias maiores) são os escolhidos
        ids = {c.video.video_id for c in candidatos}
        self.assertEqual({"v9", "v8", "v7"}, ids)


class TestSelecionar(unittest.TestCase):
    def _cfg(self, itens=2, por_canal=1):
        return Config(digest_itens=itens, digest_por_canal=por_canal)

    def _candidatos(self, *handles_e_scores):
        return [
            pool.Candidato(video=_video(f"v{i}"), handle=h, score=s)
            for i, (h, s) in enumerate(handles_e_scores)
        ]

    def test_respeita_o_teto_do_digest(self):
        original = pool.vivo
        pool.vivo = lambda url, cliente: True
        try:
            candidatos = self._candidatos(("a", 3.0), ("b", 2.0), ("c", 1.0))
            saida = pool.selecionar(self._cfg(itens=2), candidatos, cliente=None)
        finally:
            pool.vivo = original
        decisoes = {c.handle: c.decisao for c in saida}
        self.assertEqual({"a": "enviado", "b": "enviado", "c": "cortado_por_teto"}, decisoes)

    def test_respeita_o_teto_por_canal(self):
        original = pool.vivo
        pool.vivo = lambda url, cliente: True
        try:
            candidatos = [
                pool.Candidato(video=_video("v1"), handle="a", score=3.0),
                pool.Candidato(video=_video("v2"), handle="a", score=2.9),
                pool.Candidato(video=_video("v3"), handle="b", score=2.0),
            ]
            saida = pool.selecionar(self._cfg(itens=3, por_canal=1), candidatos, cliente=None)
        finally:
            pool.vivo = original
        decisoes = {c.video.video_id: c.decisao for c in saida}
        self.assertEqual(
            {"v1": "enviado", "v2": "cortado_por_canal", "v3": "enviado"}, decisoes
        )

    def test_link_morto_e_cortado_e_nao_conta_no_teto(self):
        chamados = []

        def fake_vivo(url, cliente):
            chamados.append(url)
            return "v1" not in url  # o primeiro (mais bem pontuado) está morto

        original = pool.vivo
        pool.vivo = fake_vivo
        try:
            candidatos = [
                pool.Candidato(video=_video("v1"), handle="a", score=3.0),
                pool.Candidato(video=_video("v2"), handle="b", score=2.0),
            ]
            saida = pool.selecionar(self._cfg(itens=1), candidatos, cliente=None)
        finally:
            pool.vivo = original
        decisoes = {c.handle: c.decisao for c in saida}
        self.assertEqual("cortado_por_liveness", decisoes["a"])
        self.assertEqual("enviado", decisoes["b"], "o morto não ocupa a vaga")


class DiscordFalso:
    def __init__(self, reacoes):
        self._reacoes = reacoes  # {(message_id, emoji): [user, ...]}

    def reacted_users(self, channel_id, message_id, emoji):
        return self._reacoes.get((message_id, emoji), [])


class TestCapturarFeedback(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.cfg = Config(canal_aviso="222", state_dir=self.state, janela_feedback_dias=7)

    def tearDown(self):
        self.tmp.cleanup()

    def _digest_com_item_enviado(self, dia):
        item = ledger.ItemDeDigest(video_id="v1", channel_id="UC1", canal="algumcanal",
                                    decisao="enviado", message_id="555")
        digest = ledger.Digest(data=dia, itens=[item])
        ledger.salvar_digest(self.state, digest)

    def test_grava_sinal_de_reacao_humana(self):
        self._digest_com_item_enviado("2026-08-23")
        discord = DiscordFalso({("555", "👍"): [{"id": "42", "username": "jads"}]})

        linhas = pool.capturar_feedback(self.cfg, discord)

        self.assertEqual(1, len(linhas))
        sinais = ledger.sinais(self.state)
        self.assertEqual(1, len(sinais))
        self.assertEqual("👍", sinais[0]["reacao"])
        self.assertEqual("42", sinais[0]["user_id"])
        self.assertEqual("v1", sinais[0]["video_id"])

    def test_ignora_reacao_do_proprio_bot(self):
        self._digest_com_item_enviado("2026-08-23")
        discord = DiscordFalso({("555", "👍"): [{"id": "99", "bot": True}]})
        pool.capturar_feedback(self.cfg, discord)
        self.assertEqual([], ledger.sinais(self.state))

    def test_reler_a_mesma_janela_nao_duplica(self):
        self._digest_com_item_enviado("2026-08-23")
        discord = DiscordFalso({("555", "👍"): [{"id": "42"}]})
        pool.capturar_feedback(self.cfg, discord)
        pool.capturar_feedback(self.cfg, discord)
        self.assertEqual(1, len(ledger.sinais(self.state)))

    def test_sem_discord_e_no_op(self):
        self._digest_com_item_enviado("2026-08-23")
        self.assertEqual([], pool.capturar_feedback(self.cfg, discord=None))


if __name__ == "__main__":
    unittest.main()
