"""Rode com: python3 -m unittest tests.test_canal

Classificação de URL, resolução de `channel_id` e a escrita do `canais.yaml`.

Os dois guardas que este arquivo existe para provar, e os dois vêm de medição:

1. **Saída vazia do `yt-dlp` é recusada.** O `yt-dlp` desta máquina (2024.04.09)
   responde `--print channel_id` com **saída vazia e código 0**. Um fallback que
   confiasse no código de saída persistiria `channel_id: ""` e reportaria sucesso — um
   canal cadastrado que nunca teria vídeo novo, sem nenhum erro no log.
2. **`/channel/UC…` sintaticamente válido não basta.** Vinte e dois caracteres na forma
   certa passam pela regex e o canal pode simplesmente não existir. A regex evita id
   **vazio**; só a leitura do RSS evita id **errado**. E essa leitura não é trabalho
   extra: é a mesma que semeia os 15 ids atuais.
"""

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import canal as mod_canal
from ytr.canal import (
    Alvo, Canais, CanalError, ChannelId, classificar, handle_por_oembed, resolver, vivo,
)
from ytr.rede import RedeError, Resposta

CANAL_A = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
CANAL_B = "UCbbbbbbbbbbbbbbbbbbbbbb"

FEED_MINIMO = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"'
    ' xmlns:media="http://search.yahoo.com/mrss/"'
    ' xmlns="http://www.w3.org/2005/Atom">'
    f"<yt:channelId>{CANAL_A}</yt:channelId><title>Canal de Teste</title>"
    "<entry><yt:videoId>aaaaaaaaaaa</yt:videoId>"
    f"<yt:channelId>{CANAL_A}</yt:channelId><title>Um</title>"
    '<link rel="alternate" href="https://www.youtube.com/watch?v=aaaaaaaaaaa"/>'
    "<published>2026-08-18T16:00:07+00:00</published></entry>"
    "<entry><yt:videoId>bbbbbbbbbbb</yt:videoId>"
    f"<yt:channelId>{CANAL_A}</yt:channelId><title>Dois</title>"
    '<link rel="alternate" href="https://www.youtube.com/watch?v=bbbbbbbbbbb"/>'
    "<published>2026-08-19T16:00:07+00:00</published></entry>"
    "</feed>"
)


class ClienteFalso:
    """Dublê do `Cliente`. Registra o que foi pedido, para a ordem ser afirmável."""

    def __init__(self, respostas: dict):
        self.respostas = respostas
        self.pedidos: list[str] = []
        self.bytes_gastos = 0
        self.requisicoes = 0

    def get(self, url: str, navegador: bool = False) -> Resposta:
        self.pedidos.append(url)
        self.requisicoes += 1
        achado = self.respostas.get(url)
        if achado is None:
            for chave, valor in self.respostas.items():
                if chave in url:
                    achado = valor
                    break
        if achado is None:
            raise RedeError(f"nada configurado para {url}")
        if isinstance(achado, Exception):
            raise achado
        status, texto = achado
        self.bytes_gastos += len(texto)
        return Resposta(url=url, status=status, texto=texto, cabecalhos={},
                        bytes_no_fio=len(texto))


class TestClassificar(unittest.TestCase):
    def test_link_de_canal_por_id(self):
        alvo = classificar(f"https://www.youtube.com/channel/{CANAL_A}")
        self.assertEqual("canal", alvo.tipo)
        self.assertEqual(CANAL_A, alvo.channel_id)

    def test_link_de_canal_por_handle(self):
        for url in ("https://www.youtube.com/@algumcanal",
                    "youtube.com/@algumcanal",
                    "https://m.youtube.com/@algumcanal/videos"):
            with self.subTest(url=url):
                alvo = classificar(url)
                self.assertEqual("canal", alvo.tipo)
                self.assertEqual("@algumcanal", alvo.handle)

    def test_link_de_canal_por_c_e_por_user(self):
        for url in ("https://www.youtube.com/c/AlgumNome",
                    "https://www.youtube.com/user/AlgumNome"):
            with self.subTest(url=url):
                self.assertEqual("canal", classificar(url).tipo)
                self.assertEqual("AlgumNome", classificar(url).handle)

    def test_as_tres_formas_de_um_video_canonizam_para_o_mesmo_id(self):
        """`watch?v=`, `youtu.be/` e `/shorts/` são o mesmo vídeo.

        Tratá-los como três faria o mesmo link contar três vezes no perfil de gosto.
        """
        esperado = "aaaaaaaaaaa"
        for url in (f"https://www.youtube.com/watch?v={esperado}",
                    f"https://youtu.be/{esperado}",
                    f"https://www.youtube.com/shorts/{esperado}",
                    f"https://www.youtube.com/live/{esperado}",
                    f"https://www.youtube.com/embed/{esperado}"):
            with self.subTest(url=url):
                alvo = classificar(url)
                self.assertEqual("video", alvo.tipo)
                self.assertEqual(esperado, alvo.video_id)

    def test_url_que_nao_e_do_youtube(self):
        for url in ("https://vimeo.com/12345", "https://example.com/@canal", "", None):
            with self.subTest(url=url):
                self.assertEqual("nenhum", classificar(url).tipo)

    def test_id_de_video_com_tamanho_errado_e_recusado(self):
        """Id de vídeo tem 11 caracteres. Dez ou doze é lixo, não vídeo."""
        for vid in ("curto", "aaaaaaaaaa", "aaaaaaaaaaaa"):
            with self.subTest(vid=vid):
                self.assertEqual("nenhum", classificar(f"https://youtu.be/{vid}").tipo)

    def test_channel_id_com_forma_errada_na_url_e_recusado(self):
        self.assertEqual("nenhum", classificar("https://youtube.com/channel/UCcurto").tipo)
        self.assertEqual("nenhum", classificar("https://youtube.com/channel/XX_naoeUC").tipo)


class TestChannelId(unittest.TestCase):
    """O construtor é o portão. Não existe `ChannelId` inválido em lugar nenhum."""

    def test_id_valido_passa_e_serve_de_string(self):
        cid = ChannelId(CANAL_A)
        self.assertEqual(CANAL_A, cid)
        self.assertIn(CANAL_A, cid.url_feed)

    def test_string_vazia_e_recusada(self):
        """O caso exato da saída do `yt-dlp` desta máquina."""
        for bruto in ("", "   ", None, 0):
            with self.subTest(bruto=bruto):
                with self.assertRaises(CanalError):
                    ChannelId(bruto)

    def test_id_truncado_ou_sem_prefixo_e_recusado(self):
        for bruto in ("UC", "UCcurto", "_x5XG1OV2P6uZZ5FSM9Ttw", CANAL_A + "x"):
            with self.subTest(bruto=bruto):
                with self.assertRaises(CanalError):
                    ChannelId(bruto)

    def test_a_mensagem_de_erro_nomeia_a_causa_provavel(self):
        with self.assertRaises(CanalError) as erro:
            ChannelId("")
        self.assertIn("código 0", str(erro.exception))


class TestYtDlp(unittest.TestCase):
    """A saída é validada **por forma**, nunca pelo código de saída."""

    def setUp(self):
        self.original = subprocess.run

    def tearDown(self):
        mod_canal.subprocess.run = self.original

    def _fingir(self, stdout: str, returncode: int = 0):
        class Processo:
            pass
        processo = Processo()
        processo.stdout = stdout
        processo.returncode = returncode
        mod_canal.subprocess.run = lambda *a, **k: processo

    def test_saida_vazia_com_codigo_zero_e_recusada(self):
        """Medido nesta máquina: `--print channel_id` devolve vazio e EXIT=0.

        Este é o teste que impede o sistema de cadastrar um canal e dizer que deu certo.
        """
        self._fingir("", returncode=0)
        self.assertEqual("", mod_canal._yt_dlp_channel_id("https://youtube.com/@x"))

    def test_saida_que_nao_tem_forma_de_id_e_recusada(self):
        for stdout in ("NA\n", "ERROR: unable to extract\n", "UCcurto\n", "\n\n"):
            with self.subTest(stdout=stdout.strip()):
                self._fingir(stdout, returncode=0)
                self.assertEqual("", mod_canal._yt_dlp_channel_id("https://youtube.com/@x"))

    def test_saida_com_forma_de_id_e_aceita_mesmo_com_ruido_em_volta(self):
        self._fingir(f"WARNING: algo\n{CANAL_A}\n", returncode=0)
        self.assertEqual(CANAL_A, mod_canal._yt_dlp_channel_id("https://youtube.com/@x"))

    def test_yt_dlp_ausente_na_maquina_nao_levanta(self):
        def explode(*a, **k):
            raise FileNotFoundError("yt-dlp")
        mod_canal.subprocess.run = explode
        self.assertEqual("", mod_canal._yt_dlp_channel_id("https://youtube.com/@x"))


class TestResolver(unittest.TestCase):
    def test_channel_id_na_url_ainda_passa_pela_confirmacao_por_feed(self):
        """Nenhum canal é persistido sem uma leitura de RSS bem-sucedida."""
        cliente = ClienteFalso({"feeds/videos.xml": (200, FEED_MINIMO)})
        achado = resolver(Alvo("canal", "x", channel_id=CANAL_A), cliente)
        self.assertEqual(CANAL_A, achado.channel_id)
        self.assertEqual("url", achado.fonte)
        self.assertEqual(["aaaaaaaaaaa", "bbbbbbbbbbb"], achado.videos_atuais)
        self.assertEqual(1, cliente.requisicoes, "só a confirmação por feed")

    def test_id_com_forma_valida_e_rss_404_nao_e_persistido(self):
        """O caminho "barato" também é confirmado. A regex evita vazio, o feed evita
        errado — e um `UC` + 22 caracteres válidos pode simplesmente não existir.
        """
        cliente = ClienteFalso({"feeds/videos.xml": (404, "não achei")})
        with self.assertRaises(CanalError) as erro:
            resolver(Alvo("canal", "x", channel_id=CANAL_B), cliente)
        self.assertIn("não existe", str(erro.exception))

    def test_rss_que_responde_erro_de_servidor_e_recusado(self):
        cliente = ClienteFalso({"feeds/videos.xml": (503, "indisponível")})
        with self.assertRaises(CanalError) as erro:
            resolver(Alvo("canal", "x", channel_id=CANAL_A), cliente)
        self.assertIn("503", str(erro.exception))

    def test_rss_que_nao_e_feed_valido_e_recusado(self):
        cliente = ClienteFalso({"feeds/videos.xml": (200, "<html>bloqueado</html>")})
        with self.assertRaises(CanalError) as erro:
            resolver(Alvo("canal", "x", channel_id=CANAL_A), cliente)
        self.assertIn("não é um feed válido", str(erro.exception))

    def test_rss_inalcancavel_e_recusado_com_frase(self):
        cliente = ClienteFalso({"feeds/videos.xml": RedeError("timeout")})
        with self.assertRaises(CanalError) as erro:
            resolver(Alvo("canal", "x", channel_id=CANAL_A), cliente)
        self.assertIn("não cadastro canal que não posso ler", str(erro.exception).lower())

    def test_handle_resolvido_pelo_external_id_da_pagina(self):
        pagina = f'<html>lixo{{"externalId":"{CANAL_A}"}}mais lixo</html>'
        cliente = ClienteFalso({
            "https://www.youtube.com/@x": (200, pagina),
            "feeds/videos.xml": (200, FEED_MINIMO),
        })
        achado = resolver(Alvo("canal", "https://www.youtube.com/@x", handle="@x"), cliente)
        self.assertEqual(CANAL_A, achado.channel_id)
        self.assertEqual("pagina", achado.fonte)

    def test_pagina_sem_external_id_e_sem_yt_dlp_da_uma_frase(self):
        cliente = ClienteFalso({"https://www.youtube.com/@x": (200, "<html>nada</html>")})
        with self.assertRaises(CanalError) as erro:
            resolver(Alvo("canal", "https://www.youtube.com/@x", handle="@x"), cliente)
        self.assertIn("não achei o id do canal", str(erro.exception))
        # Uma frase, não um traceback: quem lê é quem digitou a URL errada.
        self.assertLessEqual(len(str(erro.exception).splitlines()), 1)

    def test_a_banda_gasta_e_contada_na_resolucao(self):
        cliente = ClienteFalso({"feeds/videos.xml": (200, FEED_MINIMO)})
        achado = resolver(Alvo("canal", "x", channel_id=CANAL_A), cliente)
        self.assertEqual(len(FEED_MINIMO), achado.bytes_gastos)


class TestOembed(unittest.TestCase):
    def test_handle_extraido_do_author_url(self):
        cliente = ClienteFalso({"oembed": (200, '{"author_url":"https://www.youtube.com/@Alguem"}')})
        self.assertEqual("@Alguem", handle_por_oembed("https://youtu.be/aaaaaaaaaaa", cliente))

    def test_json_quebrado_devolve_vazio_em_vez_de_levantar(self):
        cliente = ClienteFalso({"oembed": (200, "não é json")})
        self.assertEqual("", handle_por_oembed("https://youtu.be/aaaaaaaaaaa", cliente))

    def test_author_url_sem_handle_devolve_vazio(self):
        cliente = ClienteFalso({"oembed": (200, '{"author_url":"https://youtube.com/channel/X"}')})
        self.assertEqual("", handle_por_oembed("https://youtu.be/aaaaaaaaaaa", cliente))

    def test_vivo_e_falso_para_video_apagado(self):
        """Obrigatório antes de recomendar: 5 dos 77 links salvos já são 404.

        Recomendar um link morto uma vez ensina a ignorar o digest para sempre.
        """
        cliente = ClienteFalso({"oembed": (404, "")})
        self.assertFalse(vivo("https://youtu.be/aaaaaaaaaaa", cliente))

    def test_vivo_e_falso_quando_a_rede_falha(self):
        cliente = ClienteFalso({"oembed": RedeError("timeout")})
        self.assertFalse(vivo("https://youtu.be/aaaaaaaaaaa", cliente))


class TestCanaisYaml(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.caminho = Path(self.tmp.name) / "canais.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_adicionar_recusa_string_crua(self):
        """Sem o `isinstance`, a garantia do construtor valeria só para quem lembrou.

        Parece redundante numa linguagem sem tipos em runtime, e é por isso que está lá.
        """
        canais = Canais(self.caminho)
        with self.assertRaises(CanalError) as erro:
            canais.adicionar(CANAL_A)
        self.assertIn("ChannelId", str(erro.exception))

    def test_ida_e_volta_pelo_disco(self):
        canais = Canais(self.caminho)
        canais.adicionar(ChannelId(CANAL_A), handle="@um", nome="Um")
        canais.salvar()
        relido = Canais(self.caminho)
        self.assertEqual(1, len(relido))
        self.assertIn(CANAL_A, relido)
        self.assertEqual("@um", relido.get(CANAL_A).handle)

    def test_arquivo_ausente_e_lista_vazia_e_nao_erro(self):
        self.assertEqual(0, len(Canais(self.caminho)))

    def test_yaml_invalido_recusa_em_vez_de_reescrever_por_cima(self):
        """Este arquivo é editado à mão de propósito. Sobrescrever apagaria curadoria."""
        self.caminho.write_text("canais: [{{{ isso não é yaml", encoding="utf-8")
        with self.assertRaises(CanalError) as erro:
            Canais(self.caminho)
        self.assertIn("não vou reescrevê-lo", str(erro.exception).lower())

    def test_campo_desconhecido_no_yaml_e_ignorado_sem_derrubar(self):
        """Alguém acrescentou uma chave à mão. Não é motivo para o ciclo não rodar."""
        self.caminho.write_text(
            f"canais:\n- channel_id: {CANAL_A}\n  handle: '@um'\n  invencao: 42\n",
            encoding="utf-8",
        )
        canais = Canais(self.caminho)
        self.assertEqual(1, len(canais))
        self.assertEqual("@um", canais.get(CANAL_A).handle)

    def test_entrada_sem_channel_id_e_pulada(self):
        self.caminho.write_text("canais:\n- handle: '@sem_id'\n- 'nem é dict'\n", encoding="utf-8")
        self.assertEqual(0, len(Canais(self.caminho)))

    def test_por_apelido_acha_por_id_handle_com_ou_sem_arroba_e_nome(self):
        """Existe para ninguém ter de copiar 24 caracteres para desativar um canal.

        Esse atrito é o que faz alguém editar o YAML à mão com o container de pé — que é
        exatamente a corrida que o `flock` existe para impedir.
        """
        canais = Canais(self.caminho)
        canais.adicionar(ChannelId(CANAL_A), handle="@AlgumNome", nome="Nome Bonito")
        for chave in (CANAL_A, "@AlgumNome", "AlgumNome", "algumnome", "Nome Bonito"):
            with self.subTest(chave=chave):
                self.assertIsNotNone(canais.por_apelido(chave))

    def test_por_apelido_devolve_none_para_desconhecido_e_para_vazio(self):
        canais = Canais(self.caminho)
        canais.adicionar(ChannelId(CANAL_A), handle="@um")
        self.assertIsNone(canais.por_apelido("@naoexiste"))
        self.assertIsNone(canais.por_apelido(""))

    def test_ativos_nao_traz_desativado(self):
        canais = Canais(self.caminho)
        canais.adicionar(ChannelId(CANAL_A), handle="@um")
        canais.adicionar(ChannelId(CANAL_B), handle="@dois")
        canais.get(CANAL_B).ativo = False
        canais.salvar()
        relido = Canais(self.caminho)
        self.assertEqual(2, len(relido))
        self.assertEqual([CANAL_A], [c.channel_id for c in relido.ativos()])

    def test_o_arquivo_salvo_explica_como_editar_a_mao(self):
        canais = Canais(self.caminho)
        canais.adicionar(ChannelId(CANAL_A), handle="@um")
        canais.salvar()
        texto = self.caminho.read_text(encoding="utf-8")
        self.assertIn("Editável à mão", texto)
        self.assertIn("mesmo lock", texto)

    def test_salvar_e_atomico_e_nao_deixa_tmp_para_tras(self):
        canais = Canais(self.caminho)
        canais.adicionar(ChannelId(CANAL_A), handle="@um")
        canais.salvar()
        sobrando = list(Path(self.tmp.name).glob("*.tmp"))
        self.assertEqual([], sobrando, f"sobrou temporário: {sobrando}")


if __name__ == "__main__":
    unittest.main()
