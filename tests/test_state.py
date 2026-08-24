"""Rode com: python3 -m unittest tests.test_state

Estado local em arquivo. Duas regras transportadas do projeto irmão, e as duas custaram
caro lá:

1. **Escrita atômica** (`tmp` + `os.replace`). `write_text` direto deixa uma janela em
   que o arquivo existe truncado — e aqui, ler estado truncado significa **avisar os 15
   vídeos do feed de novo**.
2. **Nenhum arquivo com dois escritores.** O cursor de cada canal mora no arquivo
   *daquele* canal, e não num JSON único: 20 canais buscados em paralelo seriam 20
   escritores no mesmo arquivo — o read-modify-write concorrente clássico.
"""

import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr.state import (
    Estado, EstadoCanal, EstadoNaoGravavel, Saude, agora_utc, anexar_linha,
    escrever_atomico, ler_linhas, preflight,
)

CANAL_A = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
CANAL_B = "UCbbbbbbbbbbbbbbbbbbbbbb"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestCarimbo(Base):
    def test_o_carimbo_interno_e_sempre_utc(self):
        """Fuso local só na apresentação. Carimbo com fuso de máquina faz dois registros
        do mesmo instante parecerem horas diferentes quando a máquina muda.
        """
        carimbo = agora_utc()
        self.assertTrue(carimbo.endswith("+00:00"), carimbo)
        self.assertNotIn(".", carimbo, "segundos inteiros, sem microssegundo")


class TestEscritaAtomica(Base):
    def test_escreve_e_nao_deixa_temporario(self):
        destino = self.dir / "sub" / "arquivo.json"
        escrever_atomico(destino, "conteúdo")
        self.assertEqual("conteúdo", destino.read_text(encoding="utf-8"))
        self.assertEqual([], list(destino.parent.glob("*.tmp")))

    def test_sobrescrever_nao_deixa_janela_de_arquivo_truncado(self):
        """`os.replace` é atômico no mesmo sistema de arquivos: em nenhum instante o
        arquivo existe com conteúdo parcial.
        """
        destino = self.dir / "arquivo.json"
        escrever_atomico(destino, "primeiro" * 100)
        escrever_atomico(destino, "segundo")
        self.assertEqual("segundo", destino.read_text(encoding="utf-8"))

    def test_cria_o_diretorio_pai(self):
        destino = self.dir / "a" / "b" / "c.json"
        escrever_atomico(destino, "x")
        self.assertTrue(destino.is_file())


class TestJsonl(Base):
    def test_anexar_e_ler_de_volta(self):
        destino = self.dir / "linhas.jsonl"
        anexar_linha(destino, {"a": 1})
        anexar_linha(destino, {"a": 2})
        self.assertEqual([{"a": 1}, {"a": 2}], ler_linhas(destino))

    def test_acento_nao_e_escapado(self):
        destino = self.dir / "linhas.jsonl"
        anexar_linha(destino, {"titulo": "avaliação"})
        self.assertIn("avaliação", destino.read_text(encoding="utf-8"))

    def test_linha_corrompida_e_pulada_e_o_resto_sobrevive(self):
        """Linha meio escrita só existe se o processo morreu no meio de um append.

        Perder o histórico inteiro por causa dela seria pior que perder a linha.
        """
        destino = self.dir / "linhas.jsonl"
        anexar_linha(destino, {"a": 1})
        with destino.open("a", encoding="utf-8") as arquivo:
            arquivo.write('{"a": 2, "incom\n')
        anexar_linha(destino, {"a": 3})
        self.assertEqual([{"a": 1}, {"a": 3}], ler_linhas(destino))

    def test_arquivo_ausente_e_lista_vazia(self):
        self.assertEqual([], ler_linhas(self.dir / "nao_existe.jsonl"))

    def test_linha_em_branco_e_ignorada(self):
        destino = self.dir / "linhas.jsonl"
        destino.write_text('{"a": 1}\n\n\n{"a": 2}\n', encoding="utf-8")
        self.assertEqual([{"a": 1}, {"a": 2}], ler_linhas(destino))


class TestEstadoCanal(Base):
    def test_um_arquivo_por_canal(self):
        """A regra que evita 20 escritores no mesmo arquivo."""
        estado = Estado(self.dir)
        for channel_id in (CANAL_A, CANAL_B):
            atual = estado.carregar(channel_id)
            atual.lembrar("aaaaaaaaaaa", 50)
            estado.salvar(atual)
        arquivos = sorted(p.name for p in (self.dir / "visto").glob("*.json"))
        self.assertEqual([f"{CANAL_A}.json", f"{CANAL_B}.json"], arquivos)

    def test_ida_e_volta_preserva_os_campos(self):
        estado = Estado(self.dir)
        atual = estado.carregar(CANAL_A)
        atual.lembrar("aaaaaaaaaaa", 50)
        atual.semeado = True
        atual.ultimo_publicado = "2026-08-20T10:00:00+00:00"
        atual.bytes_ultimo_ciclo = 4932
        estado.salvar(atual)

        relido = estado.carregar(CANAL_A)
        self.assertEqual(["aaaaaaaaaaa"], relido.avisados)
        self.assertTrue(relido.semeado)
        self.assertEqual("2026-08-20T10:00:00+00:00", relido.ultimo_publicado)
        self.assertEqual(4932, relido.bytes_ultimo_ciclo)
        self.assertTrue(relido.ultima_busca, "salvar carimba a última busca")

    def test_canal_desconhecido_devolve_estado_vazio_e_nao_erro(self):
        atual = Estado(self.dir).carregar(CANAL_A)
        self.assertEqual(CANAL_A, atual.channel_id)
        self.assertEqual([], atual.avisados)
        self.assertFalse(atual.semeado)

    def test_json_corrompido_devolve_estado_vazio(self):
        """Estado ilegível é estado ausente. O preço é reavisar — e é justamente por
        isso que a escrita é atômica: para esta janela não existir na prática.
        """
        caminho = self.dir / "visto" / f"{CANAL_A}.json"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("{isso não é json", encoding="utf-8")
        atual = Estado(self.dir).carregar(CANAL_A)
        self.assertEqual([], atual.avisados)

    def test_json_com_campo_desconhecido_devolve_estado_vazio(self):
        """Formato que mudou entre versões não pode derrubar o ciclo."""
        caminho = self.dir / "visto" / f"{CANAL_A}.json"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps({"campo_de_outra_versao": 1}), encoding="utf-8")
        self.assertEqual([], Estado(self.dir).carregar(CANAL_A).avisados)

    def test_todos_lista_os_canais_com_estado(self):
        estado = Estado(self.dir)
        self.assertEqual([], estado.todos(), "sem pasta `visto`, lista vazia")
        for channel_id in (CANAL_B, CANAL_A):
            estado.salvar(estado.carregar(channel_id))
        self.assertEqual([CANAL_A, CANAL_B], [e.channel_id for e in estado.todos()])


class TestSaude(Base):
    def test_ida_e_volta(self):
        Saude(heartbeat="2026-08-24T12:00:00+00:00", ciclos_com_falha_total=3).salvar(self.dir)
        relida = Saude.carregar(self.dir)
        self.assertEqual("2026-08-24T12:00:00+00:00", relida.heartbeat)
        self.assertEqual(3, relida.ciclos_com_falha_total)

    def test_ausente_devolve_padrao(self):
        saude = Saude.carregar(self.dir)
        self.assertEqual("", saude.heartbeat)
        self.assertFalse(saude.postagem_bloqueada)

    def test_corrompida_devolve_padrao_em_vez_de_levantar(self):
        (self.dir / "saude.json").write_text("{corrompido", encoding="utf-8")
        self.assertFalse(Saude.carregar(self.dir).postagem_bloqueada)

    def test_a_barreira_de_postagem_sobrevive_ao_disco(self):
        """É o estado que impede o "posta de novo a cada 15 minutos, para sempre"."""
        Saude(postagem_bloqueada=True, motivo="avisei e não marquei").salvar(self.dir)
        relida = Saude.carregar(self.dir)
        self.assertTrue(relida.postagem_bloqueada)
        self.assertIn("não marquei", relida.motivo)


class TestPreflight(Base):
    def test_diretorio_gravavel_passa_e_apaga_a_sonda(self):
        preflight(self.dir)
        self.assertFalse((self.dir / ".sonda").exists())

    def test_a_sonda_confere_o_que_leu_e_nao_so_que_escreveu(self):
        """Escreve, **relê** e compara. Um sistema de arquivos que aceita a escrita e
        devolve outra coisa é raro e existe — e o preflight só vale se detectar isso.
        """
        preflight(self.dir)
        preflight(self.dir)  # idempotente

    @unittest.skipIf(os.geteuid() == 0, "root escreve em diretório sem permissão")
    def test_diretorio_sem_permissao_levanta_com_a_razao(self):
        alvo = self.dir / "trancado"
        alvo.mkdir()
        os.chmod(alvo, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with self.assertRaises(EstadoNaoGravavel) as erro:
                preflight(alvo)
            self.assertIn("recusa postar", str(erro.exception))
            self.assertIn("a cada ciclo", str(erro.exception))
        finally:
            os.chmod(alvo, stat.S_IRWXU)


if __name__ == "__main__":
    unittest.main()
