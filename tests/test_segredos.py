"""Rode com: python3 -m unittest tests.test_segredos

Nada que pareça segredo — nem identidade de terceiro — entra em arquivo rastreado.

Este arquivo é a tradução para teste de uma assimetria: um `git push` leva um segundo, e
tirar um segredo do histórico depois exige reescrever a história, forçar push e
**rotacionar a credencial de qualquer forma**, porque o que foi público uma vez está
capturado. Varredura à mão não sobrevive à semana que vem; teste sobrevive.

## Por que lista-branca e forma, nunca lista-negra

Este arquivo é rastreado. Listar aqui o token, o nome ou o id que se quer proibir
*publicaria exatamente aquilo que se está tirando* — a lista-negra viraria o vazamento.
Por isso todo teste daqui pergunta **"isto tem a forma de um segredo?"** ou **"o padrão
é um destes valores neutros?"**. Nenhum valor pessoal aparece neste arquivo, de propósito.

## O guarda varre o próprio arquivo

Não existe lista de isentos por nome de arquivo. No projeto irmão existiu
(`ISENTOS = {"tests/test_segredos.py"}`), e isso fazia do único arquivo que a varredura
não olhava o esconderijo perfeito — provado por mutação: um valor realista colado nele
deixava a suíte verde. Aqui a isenção é por **linha marcada**, e há um teste que afirma
que o próprio arquivo está no conjunto varrido.

## A guarda de emoji, que é de segurança e não de estilo

`ytr` e `discord-link-brain` rodam com o **mesmo bot**, e o campo `me` das reações do
Discord significa "este usuário-bot", não "este processo". Se o radar puser ✅ ou 📚 numa
mensagem, o `ja_marcada()` do vizinho passa a ver a mensagem como resolvida e **para de
arquivar aquele link, em silêncio**. O `validar_marcas()` do vizinho não pode nos ver —
estamos em outro repo — então o guarda mora aqui, e os cinco emojis dele estão escritos
à mão porque um guarda que só roda quando o vizinho está instalado não é guarda.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from ytr.config import MARCAS_DO_VIZINHO, Config, ConfigError

RAIZ = Path(__file__).resolve().parent.parent

# Cada padrão casa uma família específica. Regex genérica demais (`[A-Za-z0-9]{32}`)
# acusaria hash de commit e viraria ruído — e teste com ruído é teste que alguém desliga.
CREDENCIAIS = [
    ("chave privada PEM", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("nsec do Nostr", r"\bnsec1[02-9ac-hj-np-z]{20,}"),
    ("token da Anthropic", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("chave da OpenAI", r"\bsk-[A-Za-z0-9]{32,}"),
    ("token do GitHub", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    ("token do Slack", r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    ("chave da AWS", r"\bAKIA[0-9A-Z]{16}\b"),
    ("chave do Google", r"\bAIza[0-9A-Za-z_-]{30,}"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"),
    ("token de bot do Discord",
     r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}\b"),
]

# Três detalhes deste padrão vieram de erro dele mesmo no projeto irmão, e cada um
# vale a linha:
#
# - `[ \t]*` e não `\s*` — `\s` casa `\n`, então em `CHAVE=` vazio ele pulava a quebra
#   e capturava a linha seguinte como se fosse o valor;
# - **sem `^`** — ancorar no início da linha fazia só a *primeira* atribuição de cada
#   linha ser conferida, e numa linha de fonte Python como `"A_TOKEN=x\nB_TOKEN=…"` a
#   segunda passava sem exame;
# - **`\\` fora do valor** — o valor para no `\n` escapado em vez de engolir a
#   atribuição seguinte, que então vira um achado independente.
ATRIBUICAO = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|APIKEY|PASSWORD|SENHA|PASSWD))"
    r"[ \t]*=[ \t]*[\"']?([^\s\"'#\\]+)",
)

PLACEHOLDERS = re.compile(
    r"^(<.*|x|xx+|\.\.\.|todo|placeholder|exemplo|example|seu[-_].*|"
    r"COLOQUE.*|CHANGEME|changeme|0|1|none|nao-definido)$",
    re.I,
)

# O piso **é** um enfraquecimento, e está declarado em vez de escondido: um segredo com
# menos de 12 caracteres passa por este teste. A troca é deliberada — credencial de
# verdade é longa (token de bot do Discord tem ~59, chave de provedor 32+) e sem o piso
# todo dublê de teste (`x`, `abc`) viraria alarme.
PISO_DE_TAMANHO = 12

# Rede privada: não é credencial, é o mapa de uma rede. **Por forma e sem caixa** — no
# projeto irmão a primeira neutralização trocou o nome em minúsculas e deixou passar a
# mesma string em MAIÚSCULAS, que sobreviveu em três versões de um teste.
REDE_PRIVADA = [
    ("nome de tailnet", r"\b[a-z0-9-]+\.ts\.net\b"),
    ("IP de CGNAT", r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
]

# `ytr` é o usuário que o container cria (docker/Dockerfile) — `/home/ytr/` é caminho
# de imagem, não a casa de uma pessoa. Mesma isenção que o discord-link-brain faz para
# `dlb` no seu próprio `CASA_DE_ALGUEM`.
CASA_DE_ALGUEM = re.compile(r"/home/(?!<|\$|usuario\b|user\b|você\b|ytr\b)[a-z][a-z0-9_-]{1,}/")

# Snowflake do Discord tem 17 a 20 dígitos. Id de canal não abre porta nenhuma sem
# token, mas revela que o servidor existe — e colar um id real num teste durante
# depuração é a coisa mais fácil do mundo.
ID_LONGO = re.compile(r"\b[0-9]{17,20}\b")
IDS_SINTETICOS = {
    "11111111111111111", "22222222222222222", "99999999999999999",
    "12345678901234567", "10000000000000000",
}

# Fuso embutido faz o sistema mentir a hora para todo mundo que não mora onde o autor
# mora. Só `UTC` é neutro.
REGIAO = r"(?:America|Europe|Asia|Africa|Australia|Pacific|Atlantic|Indian|Antarctica)"
FUSO_COMO_VALOR = re.compile(rf"""(?:=\s*["']?|:-)({REGIAO}/\w+)""")

VAULT_NEUTRO = ("~/Documents/Obsidian/vault", "Documents/Obsidian/vault")

# `%h/projeto` e `$HOME/projeto` passam; o que se proíbe é o **segmento a mais** dentro
# da casa de alguém, porque `~/Developer/projeto` só existe na máquina de quem  # guarda:exemplo
# escreveu
# — e num `WorkingDirectory` de systemd o efeito não é cosmético: o serviço não sobe.
PASTA_PRESUMIDA = re.compile(r"(?:~|\$HOME|%h)/[A-Za-z][\w.-]*/[A-Za-z][\w.-]*")

PERFIL_EM_URL = re.compile(r"github\.com/[A-Za-z0-9_.-]+(?![A-Za-z0-9_./-])")

# Marca de isenção, montada por concatenação para o próprio marcador não virar uma
# string que alguém possa copiar sem entender. Uma linha marcada é isenta; **um arquivo
# nunca é**.
MARCA_ISENTA = "guarda" ":exemplo"

BINARIOS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".xml", ".woff", ".woff2"}


def _rastreados() -> list[Path]:
    """Os arquivos que o git conhece. É o conjunto que um push publicaria."""
    saida = subprocess.run(
        ["git", "ls-files"], cwd=str(RAIZ), capture_output=True, text=True, check=False
    )
    return [RAIZ / linha for linha in saida.stdout.splitlines() if linha.strip()]


def _texto(caminho: Path) -> str:
    if caminho.suffix.lower() in BINARIOS or not caminho.is_file():
        return ""
    try:
        return caminho.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _varrer(regex, arquivos=None):
    """(caminho relativo, número da linha, achado) para cada linha não isenta."""
    achados = []
    for caminho in arquivos if arquivos is not None else _rastreados():
        for numero, linha in enumerate(_texto(caminho).splitlines(), 1):
            if MARCA_ISENTA in linha:
                continue
            for achado in regex.findall(linha):
                achados.append((caminho.relative_to(RAIZ).as_posix(), numero, achado))
    return achados


@unittest.skipUnless(shutil.which("git"), "sem git nesta máquina: nada para auditar")
class TestNadaDeSegredoNoRepositorio(unittest.TestCase):
    def test_o_guarda_varre_o_proprio_arquivo(self):
        """Sem isto, o único arquivo não varrido seria o esconderijo perfeito.

        Não é hipótese: no projeto irmão a isenção por nome de arquivo existiu, e um
        valor realista colado neste mesmo arquivo deixava a suíte verde.
        """
        eu = Path(__file__).resolve().relative_to(RAIZ).as_posix()
        varridos = {c.relative_to(RAIZ).as_posix() for c in _rastreados()}
        self.assertIn(
            eu, varridos,
            f"{eu} não está entre os arquivos rastreados que a varredura olha — "
            "commite-o, senão o guarda não audita a si mesmo.",
        )

    def test_nenhum_padrao_de_credencial(self):
        for nome, padrao in CREDENCIAIS:
            achados = _varrer(re.compile(padrao))
            self.assertEqual(
                [], achados,
                f"{nome} em arquivo rastreado: {achados}. Rotacione a credencial — "
                "tirar do arquivo não desfaz o que já foi commitado.",
            )

    def test_nenhuma_atribuicao_de_segredo_com_valor_real(self):
        suspeitas = [
            (arquivo, linha, chave)
            for arquivo, linha, (chave, valor) in _varrer(ATRIBUICAO)
            if len(valor) >= PISO_DE_TAMANHO and not PLACEHOLDERS.match(valor)
        ]
        self.assertEqual(
            [], suspeitas,
            f"atribuição de segredo com valor que parece real: {suspeitas}. "
            "Use um placeholder no `.env.example` e o valor só no `.env`, ignorado.",
        )

    def test_nenhum_identificador_de_rede_privada(self):
        for nome, padrao in REDE_PRIVADA:
            achados = _varrer(re.compile(padrao, re.I))
            self.assertEqual(
                [], achados,
                f"{nome} em arquivo rastreado: {achados}. Não é credencial, é o mapa "
                "de uma rede privada — um placeholder ensina a mesma coisa.",
            )

    def test_o_guarda_de_rede_privada_nao_depende_de_caixa(self):
        """A brecha por onde um vazamento já passou: a mesma string em MAIÚSCULAS."""
        for nome, padrao in REDE_PRIVADA:
            regex = re.compile(padrao, re.I)
            amostra = {"nome de tailnet": "EXEMPLO.TS.NET",  # guarda:exemplo
                       "IP de CGNAT": "100.64.0.1"}[nome]  # guarda:exemplo
            self.assertTrue(
                regex.search(amostra),
                f"o padrão de {nome} não casa {amostra!r} — se ele depender de caixa, "
                "a mesma string em maiúsculas passa pela varredura.",
            )

    def test_nenhum_caminho_absoluto_de_casa(self):
        achados = _varrer(CASA_DE_ALGUEM)
        self.assertEqual(
            [], achados,
            f"caminho absoluto da casa de alguém: {achados}. Vaza o usuário e só "
            "funciona na máquina de quem escreveu.",
        )

    def test_nenhum_caminho_presume_a_pasta_de_quem_escreveu(self):
        """Proíbe o segmento a mais **dentro de um caminho nosso**, não todo aninhamento.

        O caminho padrão do vault (`~/Documents/Obsidian/vault`) casa a mesma forma e
        **não** é o defeito que este teste caça: ele não é uma pasta que presumimos, é o
        lugar padrão de um aplicativo de terceiro, e já tem guarda próprio em
        `test_o_caminho_padrao_do_vault_e_neutro`, que o compara contra a lista-branca
        `VAULT_NEUTRO`. Isentá-lo aqui não abre buraco: trocar aquele valor por um
        caminho com nome de pessoa continua quebrando a suíte, pelo outro teste.
        """
        achados = [
            (arquivo, linha, achado)
            for arquivo, linha, achado in _varrer(PASTA_PRESUMIDA)
            if not any(neutro.startswith(achado) for neutro in VAULT_NEUTRO)
        ]
        self.assertEqual(
            [], achados,
            f"caminho com pasta intermediária presumida: {achados}. Use `~/projeto` ou "
            "`%h/projeto` — o segmento a mais só existe numa máquina.",
        )

    def test_nenhum_perfil_de_pessoa_em_url(self):
        achados = _varrer(PERFIL_EM_URL)
        self.assertEqual(
            [], achados,
            f"perfil de pessoa em URL: {achados}. Num `User-Agent` isso sai pela rede "
            "em toda requisição, e apagar o repo depois não desfaz — já viajou.",
        )

    def test_nenhum_id_longo_fora_da_lista_sintetica(self):
        achados = [
            (arquivo, linha, achado)
            for arquivo, linha, achado in _varrer(ID_LONGO)
            if achado not in IDS_SINTETICOS
        ]
        self.assertEqual(
            [], achados,
            f"id longo que parece real: {achados}. Em teste, use um dos sintéticos: "
            f"{sorted(IDS_SINTETICOS)}.",
        )

    def test_o_fuso_padrao_nao_e_de_um_lugar_so(self):
        achados = _varrer(FUSO_COMO_VALOR)
        self.assertEqual(
            [], achados,
            f"fuso regional como valor padrão: {achados}. Use `UTC` — fuso embutido faz "
            "o sistema mentir a hora para quem não mora lá.",
        )

    def test_o_caminho_padrao_do_vault_e_neutro(self):
        from ytr.config import VAULT_PADRAO
        self.assertIn(
            VAULT_PADRAO, VAULT_NEUTRO,
            f"VAULT_PADRAO é {VAULT_PADRAO!r}, que não está na lista-branca de caminhos "
            "neutros. Caminho com nome de pessoa não existe na máquina de mais ninguém, "
            "e o sistema falha em silêncio para todo mundo.",
        )


@unittest.skipUnless(shutil.which("git"), "sem git nesta máquina")
class TestGitignoreCobreOQueOCodigoEscreve(unittest.TestCase):
    """`.env` sozinho **não** cobre `.env.bak`, e esse buraco já existiu no irmão.

    O `env_io` deste projeto cria os dois derivados; ignorar só `.env` deixaria ambos
    rastreáveis — e o `.env.bak` tem exatamente o mesmo conteúdo do `.env`.
    """

    def ignorado(self, nome: str) -> bool:
        return subprocess.run(
            ["git", "check-ignore", "-q", nome], cwd=str(RAIZ), check=False
        ).returncode == 0

    def test_o_env_e_seus_derivados_sao_ignorados(self):
        for nome in (".env", ".env.bak", ".env.tmp"):
            self.assertTrue(self.ignorado(nome), f"{nome} não está no .gitignore.")

    def test_o_estado_local_e_ignorado(self):
        for nome in (".state/saude.json", ".state/visto/x.json", ".state/sinais.jsonl"):
            self.assertTrue(self.ignorado(nome), f"{nome} não está no .gitignore.")

    def test_o_exemplo_continua_versionado(self):
        self.assertFalse(
            self.ignorado(".env.example"),
            "o `.env.example` tem de ficar rastreado: é a documentação executável da "
            "configuração. Se ele for ignorado, ninguém sabe o que preencher.",
        )

    def test_nada_perigoso_esta_rastreado(self):
        rastreados = {c.relative_to(RAIZ).as_posix() for c in _rastreados()}
        for proibido in (".env", ".env.bak", ".env.tmp"):
            self.assertNotIn(proibido, rastreados, f"{proibido} está rastreado.")


class TestMarcasNaoEncostamNoVizinho(unittest.TestCase):
    """A interseção com o `discord-link-brain`. Guarda de segurança, não de estilo.

    Se o radar puser um emoji do vizinho numa mensagem, o `ja_marcada()` dele vê a
    mensagem como resolvida e **para de arquivar aquele link, em silêncio**. Os dois
    rodam com o mesmo bot, e o campo `me` das reações não distingue processo.
    """

    def test_o_vizinho_tem_exatamente_cinco_marcas(self):
        """São **cinco**, não seis.

        `marcas = (PROCESSED, DUPLICATE)` do sync mais
        `marcas_responder = (RESPONDER, PENDENTE, ERRO)` do responder. O número importa
        porque esta lista é um guarda, e guarda cuja aritmética ninguém confere não
        protege nada.
        """
        self.assertEqual(5, len(MARCAS_DO_VIZINHO))
        self.assertEqual(5, len(set(MARCAS_DO_VIZINHO)))
        self.assertEqual(("✅", "📚", "💬", "⏳", "❌"), MARCAS_DO_VIZINHO)

    def test_o_vocabulario_padrao_do_radar_passa_na_guarda(self):
        """O nosso 📡/🔁/🎬/⛔ tem de sobreviver ao próprio guarda."""
        cfg = Config()
        cfg.validar_marcas()
        self.assertEqual(("📡", "🔁", "🎬", "⛔"), cfg.marcas)
        self.assertEqual(set(), set(cfg.marcas) & set(MARCAS_DO_VIZINHO))

    def test_emoji_do_vizinho_e_recusado_um_por_um(self):
        """Cada um dos cinco, em cada uma das quatro posições. 20 casos.

        Um por um e não "algum": um guarda que só olha o primeiro campo deixa passar a
        colisão nos outros três, e é o tipo de defeito que nenhuma amostra revela.
        """
        campos = ("emoji_cadastrado", "emoji_repetido", "emoji_video", "emoji_erro")
        for marca in MARCAS_DO_VIZINHO:
            for campo in campos:
                with self.subTest(marca=marca, campo=campo):
                    cfg = Config(**{campo: marca})
                    with self.assertRaises(ConfigError) as erro:
                        cfg.validar_marcas()
                    self.assertIn(marca, str(erro.exception))

    def test_duas_marcas_iguais_entre_si_sao_recusadas(self):
        cfg = Config(emoji_video="📡")
        with self.assertRaises(ConfigError):
            cfg.validar_marcas()

    def test_marca_vazia_nao_conta_como_colisao(self):
        """Campo vazio significa "não marca", e vazio não colide com vazio.

        Sem esta distinção, desligar duas marcas de propósito levantaria erro de
        duplicidade — e a mensagem falaria de colisão onde não há emoji nenhum.
        """
        cfg = Config(emoji_repetido="", emoji_erro="")
        cfg.validar_marcas()


if __name__ == "__main__":
    unittest.main()
