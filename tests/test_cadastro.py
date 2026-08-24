"""Rode com: python3 -m unittest tests.test_cadastro

O cadastro de canal a partir de mensagens do Discord (Fase 4, D3 do plano):

- link de **canal** novo → cadastra em `canais.yaml`, semeia sem avisar, reage 📡.
- o **mesmo** canal postado de novo → não duplica, reage 🔁.
- canal que **não resolve** → reage ⛔, e só posta a mensagem nomeando a URL com
  `YTR_POST_ENABLED=1` (a reação é PUT; a mensagem é POST, e só o POST obedece a
  variável — D8/D3 do plano).
- link de **vídeo** → não cadastra canal, grava sinal fraco em `sinais.jsonl`, reage 🎬.
- `--seco` não escreve nada: nem `canais.yaml`, nem o cursor, nem uma reação.

`resolver` e `handle_por_oembed` são substituídos por dublês: nenhum destes testes
toca rede.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import cadastro
from ytr import ledger
from ytr.canal import Alvo, Canais, CanalError, ChannelId, Resolucao
from ytr.config import Config
from ytr.discord_client import DiscordError
from ytr.state import Estado

CANAL_ID = ChannelId("UC" + "1" * 22)


def _mensagem(msg_id, autor_id, conteudo, bot=False):
    return {"id": str(msg_id), "content": conteudo, "author": {"id": str(autor_id), "bot": bot}}


class DiscordFalso:
    """Dublê mínimo: só os três métodos que `cadastro.processar` chama."""

    def __init__(self, mensagens=()):
        self.mensagens = list(mensagens)
        self.reacoes: list[tuple] = []
        self.postadas: list[tuple] = []

    def iter_messages(self, channel_id, after=None, limit_total=None):
        for m in self.mensagens:
            if after is None or int(m["id"]) > int(after):
                yield m

    def add_reaction(self, channel_id, message_id, emoji):
        self.reacoes.append((channel_id, message_id, emoji))

    def post_message(self, channel_id, content, responder_a=None):
        self.postadas.append((channel_id, content, responder_a))
        return {"id": "999"}


def _resolver_ok(alvo, cliente):
    return Resolucao(
        channel_id=CANAL_ID, nome="Canal Teste", handle=alvo.handle or "@canalteste",
        fonte="teste", videos_atuais=["v1", "v2"], bytes_gastos=0,
    )


def _resolver_falha(alvo, cliente):
    raise CanalError("não achei o channel_id")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.state = self.raiz / ".state"
        self.canais_yaml = self.raiz / "canais.yaml"
        self.canais = Canais(self.canais_yaml)
        self.estado = Estado(self.state)
        self.cfg = Config(canal_entrada="111", state_dir=self.state)
        cadastro._salvar_cursor(self.state, "0")  # cursor já existe: pula o "primeira vez"

        self._resolver_original = cadastro.resolver
        self._oembed_original = cadastro.handle_por_oembed
        cadastro.handle_por_oembed = lambda url, cliente: "@canalteste"

    def tearDown(self):
        cadastro.resolver = self._resolver_original
        cadastro.handle_por_oembed = self._oembed_original
        self.tmp.cleanup()

    def _processar(self, discord, seco=False):
        return cadastro.processar(self.cfg, self.canais, self.estado, cliente=None, discord=discord, seco=seco)


class TestCanalNovo(Base):
    def setUp(self):
        super().setUp()
        cadastro.resolver = _resolver_ok

    def test_cadastra_semeia_e_reage(self):
        discord = DiscordFalso([_mensagem(1, "42", "olha esse canal https://youtube.com/@canalteste")])
        relatorio = self._processar(discord)

        self.assertEqual(1, relatorio.canais_novos)
        canais_relidos = Canais(self.canais_yaml)
        self.assertIn(CANAL_ID, canais_relidos)
        self.assertEqual([("111", "1", "📡")], discord.reacoes)

        atual = self.estado.carregar(str(CANAL_ID))
        self.assertTrue(atual.semeado)
        self.assertTrue(atual.ja_avisou("v1"))

    def test_seco_nao_escreve_nada(self):
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@canalteste")])
        relatorio = self._processar(discord, seco=True)

        self.assertEqual(1, relatorio.canais_novos, "conta, mas não age")
        self.assertEqual(0, len(Canais(self.canais_yaml)))
        self.assertEqual([], discord.reacoes)
        self.assertEqual("0", cadastro._ler_cursor(self.state), "cursor não avança em seco")

    def test_mensagem_de_bot_e_ignorada(self):
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@canalteste", bot=True)])
        relatorio = self._processar(discord)
        self.assertEqual(0, relatorio.canais_novos)
        self.assertEqual(0, len(Canais(self.canais_yaml)))

    def test_dono_nao_autorizado_e_ignorado_em_silencio(self):
        self.cfg.donos = ("999",)
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@canalteste")])
        relatorio = self._processar(discord)
        self.assertEqual(0, relatorio.canais_novos)
        self.assertEqual([], discord.reacoes)

    def test_mensagem_sem_url_e_ignorada(self):
        discord = DiscordFalso([_mensagem(1, "42", "gostei desse vídeo, muito bom")])
        relatorio = self._processar(discord)
        self.assertEqual(0, relatorio.mensagens)
        self.assertEqual([], discord.reacoes)


class TestCanalRepetido(Base):
    def setUp(self):
        super().setUp()
        cadastro.resolver = _resolver_ok
        self.canais.adicionar(CANAL_ID, handle="@canalteste", nome="Canal Teste")
        self.canais.salvar()

    def test_nao_duplica_e_reage_repetido(self):
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@canalteste de novo")])
        relatorio = self._processar(discord)

        self.assertEqual(1, relatorio.canais_repetidos)
        self.assertEqual(0, relatorio.canais_novos)
        self.assertEqual(1, len(Canais(self.canais_yaml)), "não duplicou a entrada")
        self.assertEqual([("111", "1", "🔁")], discord.reacoes)


class TestCanalQueNaoResolve(Base):
    def setUp(self):
        super().setUp()
        cadastro.resolver = _resolver_falha

    def test_reage_erro_e_conta_falha(self):
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@naoexiste")])
        relatorio = self._processar(discord)

        self.assertEqual(1, relatorio.falhas)
        self.assertEqual([("111", "1", "⛔")], discord.reacoes)

    def test_post_enabled_zero_nao_posta_a_mensagem_de_erro(self):
        self.cfg.post_enabled = False
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@naoexiste")])
        self._processar(discord)
        self.assertEqual([], discord.postadas, "a reação não é POST, mas a mensagem é — e está desligada")

    def test_post_enabled_um_posta_a_mensagem_nomeando_a_url(self):
        self.cfg.post_enabled = True
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@naoexiste")])
        self._processar(discord)
        self.assertEqual(1, len(discord.postadas))
        self.assertIn("https://youtube.com/@naoexiste", discord.postadas[0][1])


class TestVideo(Base):
    def test_grava_sinal_fraco_e_reage_sem_cadastrar_canal(self):
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/watch?v=dQw4w9WgXcQ")])
        relatorio = self._processar(discord)

        self.assertEqual(1, relatorio.videos_sinalizados)
        self.assertEqual(0, len(Canais(self.canais_yaml)), "vídeo não cadastra canal")
        self.assertEqual([("111", "1", "🎬")], discord.reacoes)

        sinais = ledger.sinais(self.state)
        self.assertEqual(1, len(sinais))
        self.assertEqual("dQw4w9WgXcQ", sinais[0]["video_id"])
        self.assertEqual("postado", sinais[0]["tipo"])


class TestDesligado(Base):
    def test_sem_discord_e_no_op(self):
        relatorio = self._processar(discord=None)
        self.assertEqual(0, relatorio.mensagens)
        self.assertEqual([], relatorio.erros)

    def test_sem_canal_entrada_e_no_op(self):
        self.cfg.canal_entrada = ""
        relatorio = self._processar(DiscordFalso([_mensagem(1, "42", "https://youtube.com/@x")]))
        self.assertEqual(0, relatorio.mensagens)


class TestPrimeiraVez(Base):
    def setUp(self):
        # Sem o cursor que a `Base` semeia: este é o cenário "cadastro ligado agora".
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.state = self.raiz / ".state"
        self.canais_yaml = self.raiz / "canais.yaml"
        self.canais = Canais(self.canais_yaml)
        self.estado = Estado(self.state)
        self.cfg = Config(canal_entrada="111", state_dir=self.state)
        self._resolver_original = cadastro.resolver
        cadastro.resolver = _resolver_ok

    def tearDown(self):
        cadastro.resolver = self._resolver_original
        self.tmp.cleanup()

    def test_nao_varre_o_historico_e_grava_cursor(self):
        discord = DiscordFalso([_mensagem(1, "42", "https://youtube.com/@canalteste")])
        relatorio = cadastro.processar(self.cfg, self.canais, self.estado, cliente=None, discord=discord)

        self.assertEqual(0, relatorio.mensagens, "primeira vez: não processa o que já estava lá")
        self.assertEqual(0, len(Canais(self.canais_yaml)))
        self.assertNotEqual("", cadastro._ler_cursor(self.state))


if __name__ == "__main__":
    unittest.main()
