"""Rode com: python3 -m unittest tests.test_cli

A linha de comando. Este arquivo é o que faz os critérios de pronto do plano deixarem de
ser frases e passarem a ser asserções.

`main()` recebe `argv` e **devolve** um `int` em vez de chamar `sys.exit`, e é isso que
deixa cada critério rodar em processo — inclusive o de `.state` não-gravável, que precisa
provar que o ciclo recusa **antes** de qualquer POST, e não depois.

Todo teste daqui aponta `YTR_STATE_DIR` e `YTR_CANAIS` para um diretório temporário. Sem
isso a suíte escreveria no `.state` da máquina de quem rodar — que é o tipo de teste que
passa e estraga o estado de produção no caminho.
"""

import io
import os
import re
import stat
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import __main__ as cli
from ytr import cadastro as mod_cadastro
from ytr import config as mod_config
from ytr import rede as mod_rede
from ytr.canal import Canais, ChannelId
from ytr.rede import RedeError, Resposta

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "feed.xml"
CANAL_A = "UC_x5XG1OV2P6uZZ5FSM9Ttw"

ENTRADAS_NO_FIXTURE = 15
SHORTS_NO_FIXTURE = 8


def rodar_cli(argv: list[str]) -> tuple[int, str, str]:
    """Roda um comando e devolve (código, stdout, stderr)."""
    saida, erro = io.StringIO(), io.StringIO()
    with redirect_stdout(saida), redirect_stderr(erro):
        codigo = cli.main(argv)
    return codigo, saida.getvalue(), erro.getvalue()


class Base(unittest.TestCase):
    """Isola `.env`, `.state` e `canais.yaml` num diretório temporário.

    `YTR_CANAL_AVISO` e afins ficam **fora** do ambiente de propósito: os comandos que
    este arquivo exercita não devem exigi-los, e se um dia passarem a exigir, é aqui que
    aparece.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.state = self.raiz / ".state"
        self.canais_yaml = self.raiz / "canais.yaml"
        self.ambiente = dict(os.environ)
        # Limpa qualquer YTR_* herdado do shell de quem roda a suíte: um valor exportado
        # na sessão venceria o padrão e o teste passaria a medir a máquina, não o código.
        for chave in [c for c in os.environ if c.startswith("YTR_")]:
            del os.environ[chave]
        # DISCORD_TOKEN e OBSIDIAN_VAULT não começam com `YTR_`, então o laço acima não
        # os pega — e sem isto eles sobreviveriam do shell de quem roda a suíte.
        os.environ.pop("DISCORD_TOKEN", None)
        os.environ.pop("OBSIDIAN_VAULT", None)
        os.environ["YTR_STATE_DIR"] = str(self.state)
        os.environ["YTR_CANAIS"] = str(self.canais_yaml)

        # `_cfg()` chama `load_dotenv()` sem caminho, que lê `.env` do diretório atual —
        # e o `.env` de verdade deste repo, uma vez preenchido para uso real, teria
        # exatamente as variáveis que estes testes afirmam estar ausentes. Neutralizado
        # aqui, mesmo padrão do `discord-link-brain` (`ComandoCase.setUp`, tests/test_cli.py).
        self._load_dotenv = mod_config.load_dotenv
        mod_config.load_dotenv = lambda *a, **k: None

    def tearDown(self):
        mod_config.load_dotenv = self._load_dotenv
        os.environ.clear()
        os.environ.update(self.ambiente)
        self.tmp.cleanup()

    def com_canal(self, channel_id=CANAL_A, handle="@canal"):
        canais = Canais(self.canais_yaml)
        canais.adicionar(ChannelId(channel_id), handle=handle)
        canais.salvar()


class TestCaminhoPadraoDeCanais(unittest.TestCase):
    def test_mora_em_curadoria_nao_na_raiz(self):
        """Não pode voltar a ser `canais.yaml` solto na raiz: um bind mount Docker de
        arquivo único quebra a escrita atômica (`os.replace` bate em "Device or
        resource busy") — medido ao vivo no primeiro cadastro por Discord dentro do
        container. `curadoria/` é diretório, e diretório montado não tem esse problema.
        """
        self.assertEqual("curadoria/canais.yaml", cli.CANAIS_PADRAO)


class TestSemArgumento(Base):
    def test_sem_subcomando_imprime_ajuda_e_sai_dois(self):
        """Critério de pronto da Fase 0.

        Dois e não zero: um lançador que rode `python3 -m ytr` sem argumento por engano
        não pode receber "deu tudo certo" de volta — em `&&` isso encadearia o passo
        seguinte como se o radar tivesse rodado.
        """
        codigo, saida, _ = rodar_cli([])
        self.assertEqual(2, codigo)
        self.assertIn("usage:", saida)

    def test_a_ajuda_lista_os_subcomandos_com_exemplo(self):
        _, saida, _ = rodar_cli([])
        for comando in ("feed", "resolver", "canais", "ciclo", "perfil", "digest", "sinais", "doctor"):
            self.assertIn(comando, saida)
        self.assertIn("exemplos:", saida)


class TestFeed(Base):
    def test_lista_as_entradas_do_fixture(self):
        """Critério de pronto da Fase 1, com o número **medido**.

        O plano diz "15 linhas com exatamente 1 marcada `SHORT`", e isso está errado: a
        §1.4 dele mediu que o **primeiro item** era um Short, e a frase virou "único" na
        transcrição. Contado no fixture: 8 de 15. Este teste afirma o que o XML tem, não
        o que o plano diz.
        """
        codigo, saida, _ = rodar_cli(["feed", "--arquivo", str(FIXTURE)])
        self.assertEqual(0, codigo)
        # O filtro casa a **forma da linha de item** (`  1 SHORT id  título`), não só
        # "começa com dígito": a linha de resumo começa com "15 entradas" e entrava na
        # conta, dando 16 itens para um feed de 15.
        itens = [l for l in saida.splitlines() if re.match(r"^\s*\d+ (SHORT| {5}) ", l)]
        self.assertEqual(ENTRADAS_NO_FIXTURE, len(itens))
        self.assertEqual(SHORTS_NO_FIXTURE, sum(1 for l in itens if "SHORT" in l))
        self.assertTrue(itens[0].split()[1] == "SHORT", "o primeiro item é Short")

    def test_o_resumo_traz_canal_e_contagem(self):
        _, saida, _ = rodar_cli(["feed", "--arquivo", str(FIXTURE)])
        self.assertIn(f"{ENTRADAS_NO_FIXTURE} entradas", saida)
        self.assertIn(f"{SHORTS_NO_FIXTURE} Shorts", saida)
        self.assertIn(CANAL_A, saida)

    def test_o_resumo_nao_casa_a_contagem_de_shorts_em_maiusculas(self):
        """`grep -c SHORT` tem de contar itens, não a linha de resumo.

        Se o resumo escrevesse "8 SHORTS", o critério de pronto contado por `grep`
        daria 9 para um feed de 8 Shorts — e o número estaria errado por um, o tipo de
        erro que ninguém confere duas vezes.
        """
        _, saida, _ = rodar_cli(["feed", "--arquivo", str(FIXTURE)])
        self.assertEqual(SHORTS_NO_FIXTURE, saida.count("SHORT"))

    def test_json_traz_os_campos_estruturados(self):
        import json
        codigo, saida, _ = rodar_cli(["feed", "--arquivo", str(FIXTURE), "--json"])
        self.assertEqual(0, codigo)
        dados = json.loads(saida)
        self.assertEqual(CANAL_A, dados["channel_id"])
        self.assertEqual(ENTRADAS_NO_FIXTURE, len(dados["videos"]))
        self.assertTrue(dados["videos"][0]["is_short"])

    def test_arquivo_inexistente_sai_um_com_uma_frase(self):
        codigo, _, erro = rodar_cli(["feed", "--arquivo", str(self.raiz / "nao_existe.xml")])
        self.assertEqual(1, codigo)
        self.assertIn("não achei o arquivo", erro)
        self.assertNotIn("Traceback", erro)

    def test_xml_malformado_sai_um_com_uma_frase(self):
        quebrado = self.raiz / "quebrado.xml"
        quebrado.write_text("<feed isso não fecha", encoding="utf-8")
        codigo, _, erro = rodar_cli(["feed", "--arquivo", str(quebrado)])
        self.assertEqual(1, codigo)
        self.assertIn("XML malformado", erro)
        self.assertNotIn("Traceback", erro)

    def test_sem_arquivo_e_sem_channel_id_sai_um(self):
        codigo, _, erro = rodar_cli(["feed"])
        self.assertEqual(1, codigo)
        self.assertIn("--arquivo", erro)


class TestResolver(Base):
    def test_link_de_video_e_recusado_com_a_razao(self):
        """Link de vídeo registra sinal de gosto; só link de canal cadastra.

        Medido: as 77 URLs de vídeo do vault são 60 canais, 53 deles com um vídeo só —
        cadastrar canal a partir de vídeo daria uma lista dominada por canais que ele
        tocou uma vez.
        """
        codigo, _, erro = rodar_cli(["resolver", "https://youtu.be/aaaaaaaaaaa"])
        self.assertEqual(1, codigo)
        self.assertIn("não é link de canal", erro)
        self.assertIn("registra sinal", erro)

    def test_url_que_nao_e_do_youtube_e_recusada(self):
        codigo, _, erro = rodar_cli(["resolver", "https://example.com/@canal"])
        self.assertEqual(1, codigo)
        self.assertIn("não é link de canal", erro)


class TestCanais(Base):
    def test_lista_vazia_diz_como_cadastrar(self):
        codigo, saida, _ = rodar_cli(["canais"])
        self.assertEqual(0, codigo)
        self.assertIn("--salvar", saida)

    def test_lista_traz_o_canal_cadastrado(self):
        self.com_canal()
        codigo, saida, _ = rodar_cli(["canais"])
        self.assertEqual(0, codigo)
        self.assertIn("@canal", saida)
        self.assertIn(CANAL_A, saida)
        self.assertIn("a semear", saida)
        self.assertIn("1 ativo(s) de 1", saida)

    def test_desativar_por_handle_sem_arroba(self):
        self.com_canal()
        codigo, saida, _ = rodar_cli(["canais", "desativar", "canal"])
        self.assertEqual(0, codigo)
        self.assertIn("ativo=False", saida)
        self.assertEqual([], Canais(self.canais_yaml).ativos())

    def test_reativar_devolve_o_canal_a_lista_ativa(self):
        self.com_canal()
        rodar_cli(["canais", "desativar", "@canal"])
        codigo, saida, _ = rodar_cli(["canais", "ativar", "@canal"])
        self.assertEqual(0, codigo)
        self.assertIn("ativo=True", saida)
        self.assertEqual(1, len(Canais(self.canais_yaml).ativos()))

    def test_desativar_sem_alvo_sai_um(self):
        codigo, _, erro = rodar_cli(["canais", "desativar"])
        self.assertEqual(1, codigo)
        self.assertIn("precisa de um id", erro)

    def test_desativar_canal_inexistente_sai_um(self):
        self.com_canal()
        codigo, _, erro = rodar_cli(["canais", "desativar", "@naoexisteisso"])
        self.assertEqual(1, codigo)
        self.assertIn("não achei canal", erro)

    def test_yaml_invalido_sai_um_sem_reescrever_o_arquivo(self):
        original = "canais: [{{{ não é yaml"
        self.canais_yaml.write_text(original, encoding="utf-8")
        codigo, _, erro = rodar_cli(["canais"])
        self.assertEqual(1, codigo)
        self.assertNotIn("Traceback", erro)
        self.assertEqual(original, self.canais_yaml.read_text(encoding="utf-8"),
                         "o arquivo editado à mão não pode ser sobrescrito")


class TestCiclo(Base):
    def setUp(self):
        super().setUp()
        self.original_get = mod_rede.Cliente.get

    def tearDown(self):
        mod_rede.Cliente.get = self.original_get
        super().tearDown()

    def _fingir_rede(self, status=200, texto=None):
        corpo = texto if texto is not None else FIXTURE.read_text(encoding="utf-8")

        def get(self_cliente, url, navegador=False):
            self_cliente.requisicoes += 1
            self_cliente.bytes_gastos += len(corpo)
            return Resposta(url=url, status=status, texto=corpo,
                            cabecalhos={"cache-control": "max-age=900"},
                            bytes_no_fio=len(corpo))
        mod_rede.Cliente.get = get

    def test_sem_canal_ativo_nao_faz_nada_e_sai_zero(self):
        codigo, saida, _ = rodar_cli(["ciclo", "--seco"])
        self.assertEqual(0, codigo)
        self.assertIn("nada a fazer", saida)

    def test_sem_discord_token_o_cliente_e_none_e_o_cadastro_e_pulado(self):
        chamadas = []
        original = mod_cadastro.processar

        def fake(cfg, canais, estado, cliente, discord, seco=False):
            chamadas.append(discord)
            return original(cfg, canais, estado, cliente, discord, seco=seco)

        mod_cadastro.processar = fake
        try:
            codigo, saida, _ = rodar_cli(["ciclo", "--seco"])
        finally:
            mod_cadastro.processar = original
        self.assertEqual(0, codigo)
        self.assertEqual([None], chamadas, "sem DISCORD_TOKEN, `_discord_cliente` devolve None")

    def test_cadastro_roda_mesmo_sem_canal_ativo(self):
        """O cadastro pode registrar o primeiro canal deste ciclo: não pode esperar por
        um que já exista — é por isso que ele corre antes do "nenhum canal ativo"."""
        chamadas = []
        original = mod_cadastro.processar
        mod_cadastro.processar = lambda *a, **k: (chamadas.append(True), mod_cadastro.RelatorioDeCadastro())[1]
        try:
            codigo, saida, _ = rodar_cli(["ciclo", "--seco"])
        finally:
            mod_cadastro.processar = original
        self.assertEqual([True], chamadas)
        self.assertEqual(0, codigo)
        self.assertIn("nada a fazer", saida)

    def test_linhas_e_erros_do_cadastro_aparecem_na_saida_do_ciclo(self):
        self.com_canal()
        self._fingir_rede()
        relatorio_falso = mod_cadastro.RelatorioDeCadastro(
            linhas=["📡 cadastrado pelo Discord: @novo (Canal Novo)"],
            erros=["cadastro: não consegui ler 111: 403"],
        )
        original = mod_cadastro.processar
        mod_cadastro.processar = lambda *a, **k: relatorio_falso
        try:
            codigo, saida, erro = rodar_cli(["ciclo", "--seco"])
        finally:
            mod_cadastro.processar = original
        self.assertIn("cadastrado pelo Discord", saida)
        self.assertIn("não consegui ler 111", erro)
        self.assertEqual(1, codigo, "erro do cadastro também faz o ciclo sair 1")

    def test_o_primeiro_ciclo_semeia_quinze_e_avisa_zero(self):
        """Critério de pronto da Fase 3, primeira metade."""
        self.com_canal()
        self._fingir_rede()
        codigo, saida, _ = rodar_cli(["ciclo", "--seco"])
        self.assertEqual(0, codigo)
        self.assertIn("15 semeados", saida)
        self.assertIn("0 avisados", saida)
        self.assertIn("bytes", saida)

    def test_o_segundo_ciclo_no_mesmo_estado_da_zero_novos(self):
        """Critério de pronto da Fase 3, segunda metade.

        O canal é pulado por cadência na segunda chamada — o que já prova "0 novos", e
        de graça: nem requisição foi gasta.
        """
        self.com_canal()
        self._fingir_rede()
        rodar_cli(["ciclo", "--seco"])
        codigo, saida, _ = rodar_cli(["ciclo", "--seco"])
        self.assertEqual(0, codigo)
        self.assertIn("0 novos", saida)
        self.assertIn("0 avisados", saida)

    @unittest.skipIf(os.geteuid() == 0, "root escreve em diretório sem permissão")
    def test_state_nao_gravavel_recusa_com_codigo_dois_antes_de_postar(self):
        """Critério de pronto da Fase 3, o guarda.

        A ordem importa mais que o código: o `preflight` corre **antes** da trava e do
        ciclo, então nenhum POST pode ter saído quando isto falha. É o que impede o modo
        de falha "posta com sucesso, falha ao marcar, repete a cada 15 minutos".
        """
        self.com_canal()
        self._fingir_rede()
        self.state.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state, stat.S_IRUSR | stat.S_IXUSR)
        try:
            codigo, _, erro = rodar_cli(["ciclo"])
        finally:
            os.chmod(self.state, stat.S_IRWXU)
        self.assertEqual(2, codigo)
        self.assertIn("recusa postar", erro)
        self.assertNotIn("Traceback", erro)

    def test_a_trava_faz_o_segundo_ciclo_sair_tres(self):
        """Critério de pronto da Fase 3: o segundo sai **3** nomeando o detentor.

        A trava é tomada dentro do comando, não no lançador — se morasse no `ytr.sh`, o
        ciclo que roda *dentro* do container não a tomaria, e a colisão que ela existe
        para impedir (execução à mão com o container de pé) seria justamente a que
        passaria.
        """
        from ytr import trava
        self.com_canal()
        self._fingir_rede()
        with trava.travar(self.state):
            codigo, _, erro = rodar_cli(["ciclo", "--seco"])
        self.assertEqual(3, codigo)
        self.assertIn("trava", erro)
        self.assertIn(str(os.getpid()), erro, "a mensagem nomeia quem detém a trava")

    def test_canal_com_http_500_sai_um_e_nomeia_o_canal(self):
        self.com_canal()
        self._fingir_rede(status=500, texto="erro do servidor")
        codigo, _, erro = rodar_cli(["ciclo", "--seco"])
        self.assertEqual(1, codigo)
        self.assertIn("500", erro)
        self.assertIn("@canal", erro)


class TestDigest(Base):
    def test_seco_sem_pool_nao_falha(self):
        """Sem vault e sem canal do pool resolvido, o digest degrada — não quebra."""
        codigo, saida, _ = rodar_cli(["digest", "--seco"])
        self.assertEqual(0, codigo)
        self.assertIn("nenhum candidato", saida)

    def test_imprime_a_razao_de_cada_recomendado_e_nao_persiste_em_seco(self):
        from ytr import pool as mod_pool
        from ytr.feed import Video

        candidato = mod_pool.Candidato(
            video=Video(video_id="v1", titulo="Vídeo Teste", url="https://youtu.be/v1"),
            handle="canalteste", componentes={"canal": 5.0}, score=5.0,
            liveness="vivo", decisao="enviado", razao="canal que você já acompanha",
        )
        original = mod_pool.selecionar
        mod_pool.selecionar = lambda cfg, candidatos, cliente: [candidato]
        try:
            codigo, saida, _ = rodar_cli(["digest", "--seco"])
        finally:
            mod_pool.selecionar = original

        self.assertEqual(0, codigo)
        self.assertIn("Vídeo Teste", saida)
        self.assertIn("canal que você já acompanha", saida)
        self.assertFalse((self.state / "digests").exists(), "--seco não persiste o digest")

    def test_sem_seco_persiste_o_digest_avaliado(self):
        from ytr import pool as mod_pool
        from ytr.feed import Video

        candidato = mod_pool.Candidato(
            video=Video(video_id="v1", titulo="Vídeo Teste", url="https://youtu.be/v1"),
            handle="canalteste", componentes={"canal": 5.0}, score=5.0,
            liveness="vivo", decisao="enviado", razao="canal que você já acompanha",
        )
        original = mod_pool.selecionar
        mod_pool.selecionar = lambda cfg, candidatos, cliente: [candidato]
        try:
            codigo, _, _ = rodar_cli(["digest"])
        finally:
            mod_pool.selecionar = original

        self.assertEqual(0, codigo)
        from ytr import ledger as mod_ledger
        from datetime import datetime, timezone
        hoje = datetime.now(timezone.utc).date().isoformat()
        digest = mod_ledger.carregar_digest(self.state, hoje)
        self.assertIsNotNone(digest)
        self.assertEqual(1, len(digest.enviados))

    def test_rodar_de_novo_no_mesmo_dia_nao_regenera_nem_perde_message_id(self):
        """Sem este guarda, a segunda chamada do dia reescreveria o digest sem os
        `message_id` que a captura de feedback de amanhã precisa ler."""
        from ytr import ledger as mod_ledger
        from datetime import datetime, timezone

        hoje = datetime.now(timezone.utc).date().isoformat()
        item = mod_ledger.ItemDeDigest(video_id="v1", decisao="enviado", message_id="777")
        mod_ledger.salvar_digest(self.state, mod_ledger.Digest(data=hoje, itens=[item]))

        codigo, saida, _ = rodar_cli(["digest"])

        self.assertEqual(0, codigo)
        self.assertIn("já existe", saida)
        digest_relido = mod_ledger.carregar_digest(self.state, hoje)
        self.assertEqual("777", digest_relido.enviados[0].message_id, "não foi sobrescrito")

    def _digest_com_um_candidato_falso(self, titulo="Vídeo Teste", razao="canal que você já acompanha"):
        """Injeta um único candidato aprovado, sem tocar rede — para os testes da
        Fase 8, que não precisam exercitar o pool inteiro de novo."""
        from ytr import pool as mod_pool
        from ytr.feed import Video

        candidato = mod_pool.Candidato(
            video=Video(video_id="v1", titulo=titulo, url="https://youtu.be/v1"),
            handle="canalteste", componentes={"canal": 5.0}, score=5.0,
            liveness="vivo", decisao="enviado", razao=razao,
        )
        original = mod_pool.selecionar
        mod_pool.selecionar = lambda cfg, candidatos, cliente: [candidato]
        return original

    def _fingir_claude(self, resposta_do_modelo):
        """Dublê de `subprocess.run` para o backend `claude-cli` — devolve o envelope
        `--output-format json` que `modelo._rodar_claude` espera."""
        import json as _json

        from ytr import modelo as mod_modelo

        original = mod_modelo.subprocess.run
        mod_modelo.subprocess.run = lambda cmd, **kw: types.SimpleNamespace(
            returncode=0, stdout=_json.dumps({"result": resposta_do_modelo}), stderr=""
        )
        return original

    def test_llm_backend_none_digest_sai_igual_sem_narracao(self):
        """Critério de pronto da Fase 8, primeira metade."""
        from ytr import pool as mod_pool
        from ytr import modelo as mod_modelo

        original_selecionar = self._digest_com_um_candidato_falso()
        try:
            codigo, saida, _ = rodar_cli(["digest"])
        finally:
            mod_pool.selecionar = original_selecionar

        self.assertEqual(0, codigo)
        self.assertIn("Vídeo Teste", saida)
        self.assertEqual(0, mod_modelo.chamadas_hoje(self.state), "backend none nunca chama")

        from datetime import datetime, timezone

        from ytr import ledger as mod_ledger
        hoje = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual("", mod_ledger.carregar_digest(self.state, hoje).narracao)

    def test_com_claude_cli_o_digest_ganha_prosa_e_o_doctor_mostra_a_chamada(self):
        """Critério de pronto da Fase 8, segunda metade."""
        from ytr import pool as mod_pool
        from ytr import modelo as mod_modelo

        os.environ["YTR_LLM_BACKEND"] = "claude-cli"
        original_selecionar = self._digest_com_um_candidato_falso()
        original_run = self._fingir_claude("Hoje tem um vídeo sobre o assunto que você gosta.")
        try:
            codigo, saida, _ = rodar_cli(["digest"])
        finally:
            mod_pool.selecionar = original_selecionar
            mod_modelo.subprocess.run = original_run

        self.assertEqual(0, codigo)
        self.assertIn("Hoje tem um vídeo sobre o assunto que você gosta.", saida)

        from datetime import datetime, timezone

        from ytr import ledger as mod_ledger
        hoje = datetime.now(timezone.utc).date().isoformat()
        digest = mod_ledger.carregar_digest(self.state, hoje)
        self.assertEqual("Hoje tem um vídeo sobre o assunto que você gosta.", digest.narracao)
        self.assertEqual("claude-cli", digest.backend_llm)

        _, saida_doctor, _ = rodar_cli(["doctor"])
        self.assertIn("chamadas_llm_hoje: 1", saida_doctor)

    def test_o_modelo_nao_pode_acrescentar_video_que_as_regras_nao_escolheram(self):
        """Critério de pronto da Fase 8, o guarda. A prosa do modelo alcança **só** o
        cabeçalho — os itens do digest vêm de `pool.selecionar`, nunca do texto livre.
        """
        from ytr import pool as mod_pool
        from ytr import modelo as mod_modelo

        os.environ["YTR_LLM_BACKEND"] = "claude-cli"
        original_selecionar = self._digest_com_um_candidato_falso(titulo="Vídeo Real")
        narracao_maliciosa = "Veja também Vídeo Inventado Pelo Modelo, imperdível e novo!"
        original_run = self._fingir_claude(narracao_maliciosa)
        try:
            rodar_cli(["digest"])
        finally:
            mod_pool.selecionar = original_selecionar
            mod_modelo.subprocess.run = original_run

        from datetime import datetime, timezone

        from ytr import ledger as mod_ledger
        hoje = datetime.now(timezone.utc).date().isoformat()
        digest = mod_ledger.carregar_digest(self.state, hoje)
        self.assertEqual(1, len(digest.itens), "o modelo não pode acrescentar item")
        self.assertEqual("Vídeo Real", digest.itens[0].titulo)
        self.assertNotIn("Vídeo Inventado", " ".join(i.titulo for i in digest.itens))
        self.assertEqual(narracao_maliciosa, digest.narracao, "a prosa fica só no cabeçalho")


class TestSinais(Base):
    def test_sem_sinal_diz_onde_procurou(self):
        codigo, saida, _ = rodar_cli(["sinais"])
        self.assertEqual(0, codigo)
        self.assertIn("sinais.jsonl", saida)

    def test_lista_os_sinais_registrados_com_a_conta(self):
        from ytr import ledger
        ledger.registrar_sinal(self.state, {
            "message_id": "11111111111111111", "user_id": "22222222222222222",
            "reacao": "👍", "video_id": "aaaaaaaaaaa", "handle": "@canal",
        })
        ledger.registrar_sinal(self.state, {
            "message_id": "11111111111111111", "user_id": "22222222222222222",
            "reacao": "👎", "video_id": "bbbbbbbbbbb", "handle": "@outro",
        })
        codigo, saida, _ = rodar_cli(["sinais"])
        self.assertEqual(0, codigo)
        self.assertIn("aaaaaaaaaaa", saida)
        self.assertIn("2 sinal(is)", saida)
        self.assertIn("1 👍", saida)
        self.assertIn("1 👎", saida)


class TestDoctor(Base):
    def test_roda_e_diagnostica_com_env_invalido(self):
        """Critério de pronto da Fase 9, e o modo de falha mais fácil de escrever sem
        perceber: um `doctor` que morre pela mesma razão que o sistema morreu não
        diagnostica nada. Qualquer `Config.from_env()` no topo o aborta exatamente no
        caso que ele existe para explicar.
        """
        os.environ["YTR_LLM_BACKEND"] = "isso-não-existe"
        codigo, saida, erro = rodar_cli(["doctor"])
        self.assertEqual(2, codigo)
        self.assertIn("INVÁLIDA", saida)
        self.assertIn("YTR_LLM_BACKEND", erro)

    def test_com_env_invalido_o_erro_e_frase_e_nao_traceback(self):
        os.environ["YTR_PISO_SEGUNDOS"] = "não é número"
        codigo, _, erro = rodar_cli(["doctor"])
        self.assertEqual(2, codigo)
        self.assertNotIn("Traceback", erro)
        self.assertIn("YTR_PISO_SEGUNDOS", erro)

    def test_imprime_cada_gatilho_com_o_numero_ao_lado_do_limiar(self):
        self.com_canal()
        codigo, saida, _ = rodar_cli(["doctor"])
        self.assertEqual(2, codigo, "sem DISCORD_TOKEN nem vault, o doctor acusa")
        self.assertIn(f"1 de {cli.TETO_MONITORADOS}", saida)
        for rotulo in ("banda", "falhas", "heartbeat", "llm", "postagem", ".state", "pool", "corpus"):
            self.assertIn(rotulo, saida)

    def test_gatilho_de_pool_e_corpus_mostram_o_numero_contra_o_limiar(self):
        """Fase 9: cada gatilho do plano vira número ao lado do seu limiar — não só um
        rótulo verde. `pool`/`corpus` são os dois que faltavam (banda, monitorados,
        falhas, heartbeat e llm já existiam de fases anteriores)."""
        codigo, saida, _ = rodar_cli(["doctor"])
        self.assertIn("pool", saida)
        self.assertIn(f"corpus          0 de {cli.Config().corpus_max_chars} chars (ok)", saida)

    def test_nomeia_a_variavel_que_falta_em_vez_de_um_rotulo_generico(self):
        codigo, saida, _ = rodar_cli(["doctor"])
        self.assertEqual(2, codigo)
        for variavel in ("DISCORD_TOKEN", "YTR_CANAL_AVISO", "YTR_CANAL_ENTRADA",
                         "OBSIDIAN_VAULT"):
            self.assertIn(variavel, saida)

    def test_o_canal_de_aviso_nao_tem_padrao_e_o_doctor_explica_por_que(self):
        """Não existe escolha segura, existe recusa.

        Um padrão "seguro" aqui significaria escolher um canal do Discord de outra
        pessoa para escrever — e cair no canal de captura encheria o arquivo de outro
        projeto com ruído de bot.
        """
        _, saida, _ = rodar_cli(["doctor"])
        self.assertIn("YTR_CANAL_AVISO", saida)
        self.assertIn("não existe padrão seguro", saida)

    def test_emoji_do_vizinho_no_env_e_acusado(self):
        """O guarda de marcas roda em todo comando, inclusive no diagnóstico."""
        os.environ["YTR_EMOJI_VIDEO"] = "✅"
        codigo, saida, erro = rodar_cli(["doctor"])
        self.assertEqual(2, codigo)
        self.assertIn("✅", erro + saida)
        self.assertIn("discord-link-brain", erro + saida)

    def test_state_nao_gravavel_aparece_no_diagnostico(self):
        if os.geteuid() == 0:
            self.skipTest("root escreve em diretório sem permissão")
        self.state.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state, stat.S_IRUSR | stat.S_IXUSR)
        try:
            codigo, saida, _ = rodar_cli(["doctor"])
        finally:
            os.chmod(self.state, stat.S_IRWXU)
        self.assertEqual(2, codigo)
        self.assertIn("NÃO GRAVÁVEL", saida)


class TestGuardaDeMarcasEmTodoComando(Base):
    def test_emoji_do_vizinho_impede_o_ciclo_de_rodar(self):
        """O guarda não pode depender de qual subcomando alguém digitou."""
        os.environ["YTR_EMOJI_CADASTRADO"] = "📚"
        self.com_canal()
        codigo, _, erro = rodar_cli(["ciclo", "--seco"])
        self.assertEqual(2, codigo)
        self.assertIn("📚", erro)
        self.assertIn("em silêncio", erro)

    def test_emoji_do_vizinho_impede_listar_canais(self):
        os.environ["YTR_EMOJI_ERRO"] = "❌"
        codigo, _, erro = rodar_cli(["canais"])
        self.assertEqual(2, codigo)
        self.assertIn("❌", erro)


if __name__ == "__main__":
    unittest.main()
