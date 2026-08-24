"""Rode com: python3 -m unittest tests.test_ciclo

O ciclo de monitoração. É onde as decisões de disponibilidade viram comportamento.

A semântica é **at-least-once**, e a ordem nunca se inverte: aviso duplicado é incômodo
cosmético, vídeo perdido é a falha que o sistema existe para impedir. Daí
`avisa-depois-marca` — o id entra no conjunto de avisados **depois** de o POST dar certo.

Mas "at-least-once" não pode virar "infinitas vezes", e é isso que os testes de barreira
aqui protegem:

- **`preflight`** prova que `.state` aceita escrita antes do primeiro POST. Sem ele, um
  `.state` read-only deixa o sistema postar com sucesso e falhar ao marcar, a cada 15
  minutos, para sempre.
- **`postagem_bloqueada`** para tudo se o save falhar *depois* de uma mensagem ter saído.
  A falha vira barreira, não retentativa.
"""

import json
import os
import stat
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import ciclo as mod_ciclo
from ytr.canal import Canais, ChannelId
from ytr.ciclo import (
    PostagemBloqueada, agendar, agendar_falha, devido, novidades, rodar, semear,
)
from ytr.config import Config
from ytr.feed import Feed, Video, parse
from ytr.rede import RedeError, Resposta
from ytr.state import Estado, EstadoCanal, EstadoNaoGravavel, Saude, preflight

CANAL_A = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
CANAL_B = "UCbbbbbbbbbbbbbbbbbbbbbb"

DESCRICAO_LONGA = (
    "Esta é uma descrição escrita pelo canal, longa o bastante para passar do piso de "
    "duzentos caracteres que separa resumo de linha de divulgação. Ela existe para o "
    "aviso poder dizer, com honestidade, que o texto é a descrição do autor e não algo "
    "que o radar inventou a partir do título do vídeo."
)


def montar_feed(ids, channel_id=CANAL_A, shorts=(), com_descricao=True) -> str:
    entradas = []
    for numero, video_id in enumerate(ids):
        href = (f"https://www.youtube.com/shorts/{video_id}" if video_id in shorts
                else f"https://www.youtube.com/watch?v={video_id}")
        descricao = (f"<media:description>{DESCRICAO_LONGA}</media:description>"
                     if com_descricao else "")
        entradas.append(
            "<entry>"
            f"<yt:videoId>{video_id}</yt:videoId>"
            f"<yt:channelId>{channel_id}</yt:channelId>"
            f"<title>Vídeo {video_id}</title>"
            f'<link rel="alternate" href="{href}"/>'
            f"<published>2026-08-{10 + numero:02d}T16:00:07+00:00</published>"
            f"<updated>2026-08-{10 + numero:02d}T16:15:58+00:00</updated>"
            "<author><name>Canal de Teste</name></author>"
            f"<media:group>{descricao}</media:group>"
            "</entry>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"'
        ' xmlns:media="http://search.yahoo.com/mrss/"'
        ' xmlns="http://www.w3.org/2005/Atom">'
        f"<yt:channelId>{channel_id}</yt:channelId><title>Canal de Teste</title>"
        + "".join(entradas) + "</feed>"
    )


class ClienteFalso:
    def __init__(self, por_canal: dict, cabecalhos: dict | None = None):
        self.por_canal = por_canal
        self.cabecalhos = cabecalhos or {"cache-control": "max-age=900"}
        self.pedidos: list[str] = []
        self.bytes_gastos = 0
        self.requisicoes = 0

    def get(self, url: str, navegador: bool = False) -> Resposta:
        self.pedidos.append(url)
        self.requisicoes += 1
        for channel_id, resultado in self.por_canal.items():
            if channel_id in url:
                if isinstance(resultado, Exception):
                    raise resultado
                status, texto = resultado
                self.bytes_gastos += len(texto)
                return Resposta(url=url, status=status, texto=texto,
                                cabecalhos=self.cabecalhos, bytes_no_fio=len(texto))
        raise RedeError(f"nada configurado para {url}")


class PublicadorFalso:
    """Dublê do Discord. É o que deixa a Fase 4 ser exercitada antes do id do canal."""

    def __init__(self, falhar_em=()):
        self.mensagens: list[str] = []
        self.falhar_em = set(falhar_em)

    def publicar(self, mensagem: str) -> str:
        for alvo in self.falhar_em:
            if alvo in mensagem:
                raise RuntimeError(f"o Discord recusou {alvo}")
        self.mensagens.append(mensagem)
        return f"msg{len(self.mensagens)}"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.state = self.raiz / ".state"
        self.cfg = Config(state_dir=self.state, post_enabled=True, lembrar_ids=50)
        self.caminho_canais = self.raiz / "canais.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def canais_com(self, *ids) -> Canais:
        canais = Canais(self.caminho_canais)
        for numero, channel_id in enumerate(ids, 1):
            canais.adicionar(ChannelId(channel_id), handle=f"@canal{numero}")
        canais.salvar()
        return Canais(self.caminho_canais)


class TestSemear(Base):
    def test_o_primeiro_ciclo_semeia_quinze_e_avisa_zero(self):
        """Uma linha de código, e é a diferença entre parecer intencional e quebrado.

        Sem isto, um canal recém-cadastrado avisaria o catálogo inteiro do feed.
        """
        ids = [f"video{n:06d}" for n in range(15)]
        cliente = ClienteFalso({CANAL_A: (200, montar_feed(ids))})
        publicador = PublicadorFalso()
        relatorio = rodar(self.cfg, self.canais_com(CANAL_A), Estado(self.state),
                          cliente, publicador)
        self.assertEqual(15, relatorio.semeados)
        self.assertEqual(0, relatorio.avisados)
        self.assertEqual(0, relatorio.novos)
        self.assertEqual([], publicador.mensagens)

    def test_a_linha_de_semeadura_diz_que_o_aviso_comeca_no_proximo(self):
        cliente = ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))})
        relatorio = rodar(self.cfg, self.canais_com(CANAL_A), Estado(self.state), cliente)
        self.assertTrue(any("semeados" in l for l in relatorio.linhas))
        self.assertTrue(any("próximo" in l for l in relatorio.linhas))

    def test_o_segundo_ciclo_com_o_mesmo_feed_da_zero_novos(self):
        """"0 novos" na segunda execução — o critério de pronto da Fase 3."""
        ids = [f"video{n:06d}" for n in range(15)]
        canais = self.canais_com(CANAL_A)
        estado = Estado(self.state)
        primeiro = rodar(self.cfg, canais, estado,
                         ClienteFalso({CANAL_A: (200, montar_feed(ids))}), PublicadorFalso())
        self.assertEqual(15, primeiro.semeados)

        # O agendamento gravado pelo primeiro ciclo faria o segundo pular o canal. Zerar
        # `proxima_busca` isola o que se quer medir: a dedup, não a cadência.
        atual = estado.carregar(CANAL_A)
        atual.proxima_busca = None
        estado.salvar(atual)

        segundo = rodar(self.cfg, canais, estado,
                        ClienteFalso({CANAL_A: (200, montar_feed(ids))}), PublicadorFalso())
        self.assertEqual(0, segundo.novos)
        self.assertEqual(0, segundo.avisados)
        self.assertEqual(0, segundo.semeados)

    def test_semear_guarda_o_ultimo_publicado(self):
        estado = EstadoCanal(channel_id=CANAL_A)
        feed = parse(montar_feed(["aaaaaaaaaaa", "bbbbbbbbbbb"]))
        semear(self.cfg, estado, feed)
        self.assertTrue(estado.semeado)
        self.assertEqual(2, len(estado.avisados))
        self.assertEqual("2026-08-11T16:00:07+00:00", estado.ultimo_publicado)


class TestDedupPorId(Base):
    def test_data_que_recua_nao_reavisa(self):
        """A dedup é pelo conjunto de ids, **não** pelo carimbo de tempo.

        Medido: `updated` chega 15 min depois de `published` no mesmo item, e uma
        estreia retroativa aparece com data anterior ao cursor. Data se move; id não.
        """
        estado = EstadoCanal(channel_id=CANAL_A, semeado=True, avisados=["aaaaaaaaaaa"])
        feed = parse(montar_feed(["aaaaaaaaaaa"]))
        feed.videos[0].publicado = "1999-01-01T00:00:00+00:00"
        self.assertEqual([], novidades(self.cfg, Canais(self.caminho_canais).get(CANAL_A)
                                       or _canal_falso(), feed, estado))

    def test_id_novo_com_data_antiga_e_avisado(self):
        estado = EstadoCanal(channel_id=CANAL_A, semeado=True, avisados=["aaaaaaaaaaa"])
        feed = parse(montar_feed(["aaaaaaaaaaa", "zzzzzzzzzzz"]))
        novos = novidades(self.cfg, _canal_falso(), feed, estado)
        self.assertEqual(["zzzzzzzzzzz"], [v.video_id for v in novos])

    def test_avisados_respeita_o_teto_descartando_os_mais_antigos(self):
        estado = EstadoCanal(channel_id=CANAL_A)
        for numero in range(10):
            estado.lembrar(f"video{numero:06d}", teto=3)
        self.assertEqual(3, len(estado.avisados))
        self.assertEqual(["video000007", "video000008", "video000009"], estado.avisados)

    def test_lembrar_o_mesmo_id_duas_vezes_nao_duplica(self):
        estado = EstadoCanal(channel_id=CANAL_A)
        estado.lembrar("aaaaaaaaaaa", teto=50)
        estado.lembrar("aaaaaaaaaaa", teto=50)
        self.assertEqual(["aaaaaaaaaaa"], estado.avisados)


def _canal_falso():
    from ytr.canal import Canal
    return Canal(channel_id=CANAL_A, handle="@canal")


class TestShorts(Base):
    def test_short_e_suprimido_por_padrao(self):
        ids = ["aaaaaaaaaaa", "bbbbbbbbbbb"]
        cliente = ClienteFalso({CANAL_A: (200, montar_feed(ids, shorts={"bbbbbbbbbbb"}))})
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado, cliente, PublicadorFalso())

        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso()
        relatorio = rodar(self.cfg, canais, estado,
                          ClienteFalso({CANAL_A: (200, montar_feed(ids, shorts={"bbbbbbbbbbb"}))}),
                          publicador)
        self.assertEqual(1, relatorio.avisados)
        self.assertEqual(1, relatorio.suprimidos_short)
        self.assertIn("aaaaaaaaaaa", publicador.mensagens[0])

    def test_short_suprimido_entra_em_avisados_para_nao_ser_recontado(self):
        """Senão o contador de suprimidos cresce a cada ciclo enquanto o Short está no
        feed — e o relatório passa a contar **estado** em vez de **evento**.
        """
        ids = ["aaaaaaaaaaa", "bbbbbbbbbbb"]
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(ids, shorts={"bbbbbbbbbbb"}))}),
              PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(ids, shorts={"bbbbbbbbbbb"}))}),
              PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.proxima_busca = None
        estado.salvar(atual)

        segundo = rodar(self.cfg, canais, estado,
                        ClienteFalso({CANAL_A: (200, montar_feed(ids, shorts={"bbbbbbbbbbb"}))}),
                        PublicadorFalso())
        self.assertEqual(0, segundo.suprimidos_short)

    def test_avisar_shorts_por_canal_vence_o_padrao_global(self):
        estado = EstadoCanal(channel_id=CANAL_A, semeado=True)
        feed = parse(montar_feed(["aaaaaaaaaaa"], shorts={"aaaaaaaaaaa"}))
        from ytr.canal import Canal
        self.assertEqual([], novidades(self.cfg, Canal(channel_id=CANAL_A), feed, estado))
        permissivo = Canal(channel_id=CANAL_A, avisar_shorts=True)
        self.assertEqual(1, len(novidades(self.cfg, permissivo, feed, estado)))


class TestAvisaDepoisMarca(Base):
    def test_o_id_so_e_marcado_depois_do_post(self):
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso()
        relatorio = rodar(self.cfg, canais, estado,
                          ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}),
                          publicador)
        self.assertEqual(1, relatorio.avisados)
        self.assertEqual(1, len(publicador.mensagens))
        self.assertTrue(estado.carregar(CANAL_A).ja_avisou("aaaaaaaaaaa"))

    def test_post_que_falha_nao_marca_o_id(self):
        """Se a mensagem não saiu, o id não pode ser dado como avisado — senão o vídeo
        se perde em silêncio, que é a falha que o sistema existe para impedir.
        """
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso(falhar_em={"aaaaaaaaaaa"})
        relatorio = rodar(self.cfg, canais, estado,
                          ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}),
                          publicador)
        self.assertEqual(0, relatorio.avisados)
        self.assertFalse(estado.carregar(CANAL_A).ja_avisou("aaaaaaaaaaa"))
        self.assertTrue(relatorio.erros)

    def test_falha_de_save_depois_do_post_bloqueia_a_postagem(self):
        """A barreira. Uma mensagem já foi lida por alguém — repostar a cada 15 minutos
        para sempre é pior que parar e pedir socorro.
        """
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso()
        original = estado.salvar
        chamadas = {"n": 0}

        def salvar_que_falha_depois_do_post(alvo):
            # As chamadas de dentro do laço de busca passam; a de depois do POST falha.
            chamadas["n"] += 1
            if publicador.mensagens:
                raise OSError("disco cheio")
            return original(alvo)

        estado.salvar = salvar_que_falha_depois_do_post
        with self.assertRaises(PostagemBloqueada):
            rodar(self.cfg, canais, estado,
                  ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), publicador)

        self.assertEqual(1, len(publicador.mensagens), "o POST saiu — é o que cria o problema")
        saude = Saude.carregar(self.state)
        self.assertTrue(saude.postagem_bloqueada)
        self.assertIn("não consegui marcar", saude.motivo)

    def test_com_a_postagem_bloqueada_nenhum_post_novo_sai(self):
        saude = Saude(postagem_bloqueada=True, motivo="estado quebrado")
        saude.salvar(self.state)
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso()
        relatorio = rodar(self.cfg, canais, estado,
                          ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}),
                          publicador)
        self.assertEqual([], publicador.mensagens)
        self.assertEqual(0, relatorio.avisados)
        self.assertTrue(any("postagem bloqueada" in e for e in relatorio.erros))

    def test_post_enabled_zero_nao_posta_nada(self):
        cfg = Config(state_dir=self.state, post_enabled=False)
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso()
        relatorio = rodar(cfg, canais, estado,
                          ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}),
                          publicador)
        self.assertEqual([], publicador.mensagens)
        self.assertTrue(any("[seco]" in l for l in relatorio.linhas))

    def test_modo_seco_imprime_o_que_avisaria_e_nao_posta(self):
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso()
        relatorio = rodar(self.cfg, canais, estado,
                          ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}),
                          publicador, seco=True)
        self.assertEqual([], publicador.mensagens)
        self.assertTrue(any("aaaaaaaaaaa" in l for l in relatorio.linhas))

    def test_o_ledger_registra_o_mapa_mensagem_para_video(self):
        """Quem publica e quem lê a reação são processos diferentes, horas depois."""
        from ytr import ledger
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        avisos = ledger.avisos_recentes(self.state, dias=1)
        self.assertEqual(1, len(avisos))
        self.assertEqual("aaaaaaaaaaa", avisos[0]["video_id"])
        self.assertEqual(CANAL_A, avisos[0]["channel_id"])


class TestTetos(Base):
    def test_o_teto_por_canal_atrasa_o_aviso_e_nao_o_cancela(self):
        """O que passou do teto **não** é marcado: volta no próximo ciclo."""
        cfg = Config(state_dir=self.state, post_enabled=True, max_avisos_canal=2)
        ids = [f"video{n:06d}" for n in range(5)]
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        rodar(cfg, canais, estado, ClienteFalso({CANAL_A: (200, montar_feed(ids))}),
              PublicadorFalso())
        atual = estado.carregar(CANAL_A)
        atual.avisados, atual.proxima_busca = [], None
        estado.salvar(atual)

        publicador = PublicadorFalso()
        relatorio = rodar(cfg, canais, estado,
                          ClienteFalso({CANAL_A: (200, montar_feed(ids))}), publicador)
        self.assertEqual(2, relatorio.avisados)
        self.assertEqual(3, relatorio.suprimidos_teto)
        restante = estado.carregar(CANAL_A)
        self.assertEqual(2, len(restante.avisados), "só os dois avisados foram marcados")


class TestFalhaDeCanal(Base):
    def test_canal_com_falha_nao_derruba_o_ciclo_do_outro(self):
        cliente = ClienteFalso({
            CANAL_A: RedeError("timeout"),
            CANAL_B: (200, montar_feed(["aaaaaaaaaaa"], channel_id=CANAL_B)),
        })
        relatorio = rodar(self.cfg, self.canais_com(CANAL_A, CANAL_B), Estado(self.state),
                          cliente, PublicadorFalso())
        self.assertEqual(2, relatorio.canais_buscados)
        self.assertEqual(1, relatorio.canais_com_falha)
        self.assertEqual(1, relatorio.semeados)

    def test_canal_com_falha_nunca_posta_mensagem(self):
        """Canal morto viraria spam eterno. A falha recua; não avisa."""
        publicador = PublicadorFalso()
        rodar(self.cfg, self.canais_com(CANAL_A), Estado(self.state),
              ClienteFalso({CANAL_A: RedeError("timeout")}), publicador)
        self.assertEqual([], publicador.mensagens)

    def test_http_404_e_falha_e_nao_feed_vazio(self):
        relatorio = rodar(self.cfg, self.canais_com(CANAL_A), Estado(self.state),
                          ClienteFalso({CANAL_A: (404, "não achei")}), PublicadorFalso())
        self.assertEqual(1, relatorio.canais_com_falha)
        self.assertTrue(any("404" in e for e in relatorio.erros))

    def test_o_recuo_cresce_com_a_falha_e_tem_teto(self):
        estado = EstadoCanal(channel_id=CANAL_A)
        agora = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        esperas = []
        for _ in range(7):
            agendar_falha(estado, "timeout", agora)
            esperas.append(
                (datetime.fromisoformat(estado.proxima_busca) - agora).total_seconds()
            )
        self.assertEqual(list(mod_ciclo.RECUO_POR_FALHA), esperas[:5])
        self.assertEqual(mod_ciclo.RECUO_POR_FALHA[-1], esperas[5])
        self.assertEqual(mod_ciclo.RECUO_POR_FALHA[-1], esperas[6], "o recuo tem teto")

    def test_um_ciclo_bem_sucedido_zera_o_contador_de_falhas(self):
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        atual = estado.carregar(CANAL_A)
        atual.falhas, atual.ultimo_erro = 3, "timeout"
        estado.salvar(atual)
        rodar(self.cfg, canais, estado,
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        self.assertEqual(0, estado.carregar(CANAL_A).falhas)
        self.assertEqual("", estado.carregar(CANAL_A).ultimo_erro)

    def test_ciclo_com_falha_total_e_contado_na_saude(self):
        rodar(self.cfg, self.canais_com(CANAL_A), Estado(self.state),
              ClienteFalso({CANAL_A: RedeError("timeout")}), PublicadorFalso())
        self.assertEqual(1, Saude.carregar(self.state).ciclos_com_falha_total)


class TestCadenciaDoCiclo(Base):
    def test_canal_sem_proxima_busca_e_devido(self):
        self.assertTrue(devido(EstadoCanal(channel_id=CANAL_A)))

    def test_proxima_busca_ilegivel_e_devido(self):
        """Estado corrompido não pode congelar um canal para sempre."""
        self.assertTrue(devido(EstadoCanal(channel_id=CANAL_A, proxima_busca="não é data")))

    def test_canal_agendado_para_o_futuro_e_pulado(self):
        agora = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        futuro = (agora + timedelta(minutes=10)).isoformat()
        self.assertFalse(devido(EstadoCanal(channel_id=CANAL_A, proxima_busca=futuro), agora))

    def test_canal_pulado_nao_gasta_requisicao(self):
        canais, estado = self.canais_com(CANAL_A), Estado(self.state)
        atual = estado.carregar(CANAL_A)
        atual.proxima_busca = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(timespec="seconds")
        estado.salvar(atual)
        cliente = ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))})
        relatorio = rodar(self.cfg, canais, estado, cliente, PublicadorFalso())
        self.assertEqual(0, cliente.requisicoes, "canal não devido não deve custar banda")
        self.assertEqual(1, relatorio.canais_pulados)

    def test_agendar_usa_max_age_menos_age_quando_o_piso_deixa(self):
        estado = EstadoCanal(channel_id=CANAL_A)
        agora = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        resposta = Resposta(url="u", status=200, texto="",
                            cabecalhos={"cache-control": "max-age=900", "age": "300"})
        cfg = Config(state_dir=self.state, piso_segundos=60)
        agendar(cfg, estado, resposta, agora)
        espera = (datetime.fromisoformat(estado.proxima_busca) - agora).total_seconds()
        self.assertEqual(600, espera)

    def test_no_padrao_o_piso_torna_o_age_inerte(self):
        """Consequência da configuração padrão, e vale estar dita como asserção.

        `YTR_PISO_SEGUNDOS=900` e `max-age=900` — medido em 5 de 5 canais — fazem
        `max(900, 900 - age)` valer 900 para **qualquer** `age`. Ou seja: no padrão de
        fábrica, a subtração do `age` não muda nada, e o header faltar em 2 dos 5 canais
        também não. Ela só passa a importar se alguém baixar o piso.

        Isto não é defeito: o piso existe justamente para ser o limite inferior. Está
        aqui como asserção para ninguém "consertar" a leitura do `age` procurando um
        efeito que a configuração padrão não deixa aparecer.
        """
        agora = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        for age in ("0", "300", "890"):
            with self.subTest(age=age):
                estado = EstadoCanal(channel_id=CANAL_A)
                resposta = Resposta(url="u", status=200, texto="",
                                    cabecalhos={"cache-control": "max-age=900", "age": age})
                agendar(Config(state_dir=self.state), estado, resposta, agora)
                espera = (datetime.fromisoformat(estado.proxima_busca) - agora).total_seconds()
                self.assertEqual(900, espera)

    def test_canal_quieto_recua_para_a_espera_lenta(self):
        """Heurística declarada como tal: pode atrasar canal que publica em rajada
        depois de um mês parado — e é por isso que está atrás de `YTR_RECUO_LENTO`.
        """
        agora = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        antigo = (agora - timedelta(days=30)).isoformat()
        estado = EstadoCanal(channel_id=CANAL_A, ultimo_publicado=antigo)
        resposta = Resposta(url="u", status=200, texto="",
                            cabecalhos={"cache-control": "max-age=900"})
        cfg = Config(state_dir=self.state, recuo_lento=True,
                     recuo_lento_dias=7, recuo_lento_segundos=3600)
        agendar(cfg, estado, resposta, agora)
        espera = (datetime.fromisoformat(estado.proxima_busca) - agora).total_seconds()
        self.assertEqual(3600, espera)

    def test_recuo_lento_desligado_mantem_a_cadencia_do_cache(self):
        agora = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        antigo = (agora - timedelta(days=30)).isoformat()
        estado = EstadoCanal(channel_id=CANAL_A, ultimo_publicado=antigo)
        resposta = Resposta(url="u", status=200, texto="",
                            cabecalhos={"cache-control": "max-age=900"})
        cfg = Config(state_dir=self.state, recuo_lento=False)
        agendar(cfg, estado, resposta, agora)
        espera = (datetime.fromisoformat(estado.proxima_busca) - agora).total_seconds()
        self.assertEqual(900, espera)


class TestPreflight(Base):
    def test_state_gravavel_passa(self):
        preflight(self.state)
        self.assertEqual([], list(self.state.glob(".sonda")), "a sonda é apagada")

    @unittest.skipIf(os.geteuid() == 0, "root escreve em diretório sem permissão")
    def test_state_nao_gravavel_levanta_antes_de_qualquer_post(self):
        """A barreira que evita o modo de falha acontecer **uma primeira vez**."""
        self.state.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with self.assertRaises(EstadoNaoGravavel) as erro:
                preflight(self.state)
            self.assertIn("recusa postar", str(erro.exception))
        finally:
            os.chmod(self.state, stat.S_IRWXU)


class TestRelatorio(Base):
    def test_o_resumo_imprime_a_banda_em_bytes(self):
        """O log imprime a banda gasta em bytes — critério de pronto da Fase 3."""
        cliente = ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))})
        relatorio = rodar(self.cfg, self.canais_com(CANAL_A), Estado(self.state),
                          cliente, PublicadorFalso())
        self.assertGreater(relatorio.bytes_gastos, 0)
        self.assertIn("bytes", relatorio.resumo())
        self.assertIn(str(relatorio.bytes_gastos), relatorio.resumo())

    def test_o_heartbeat_e_gravado_ao_fim_do_ciclo(self):
        rodar(self.cfg, self.canais_com(CANAL_A), Estado(self.state),
              ClienteFalso({CANAL_A: (200, montar_feed(["aaaaaaaaaaa"]))}), PublicadorFalso())
        self.assertTrue(Saude.carregar(self.state).heartbeat)


if __name__ == "__main__":
    unittest.main()
