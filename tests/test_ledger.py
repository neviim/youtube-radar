"""Rode com: python3 -m unittest tests.test_ledger

O mapa `message_id → vídeo`, e o registro do digest.

Por que ele é obrigatório: quem publica é um processo (`ciclo`, de 15 em 15 min;
`digest`, 1×/dia) e quem lê a reação é outro, **minutos ou horas depois**. O leitor
recebe um id de mensagem e uma lista de reações; sem mapa persistido, não tem como saber
a que vídeo aquilo se refere. A alternativa seria reler o texto da nossa própria mensagem
e extrair a URL — parsing do que nós mesmos escrevemos, que quebra na primeira vez que
alguém mexer no formato do aviso.

A invariante mais caviosa aqui é a **idempotência do sinal**: a captura relê a mesma
janela de 7 dias a cada ciclo, então sem ela um único 👍 viraria ~670 sinais por semana,
e o peso desse polegar dominaria o ranking inteiro.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ytr import ledger
from ytr.canal import Canal
from ytr.feed import Video
from ytr.ledger import (
    DECISOES, Digest, ItemDeDigest, avisos_recentes, caminho_avisos, caminho_digest,
    caminho_pool2, candidatos_pool2_recentes, carregar_digest, digests_recentes,
    registrar_aviso, registrar_candidato_pool2, registrar_sinal, salvar_digest, sinais,
)
from ytr.state import anexar_linha

CANAL_A = "UC_x5XG1OV2P6uZZ5FSM9Ttw"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestAvisos(Base):
    def test_registrar_e_reler(self):
        registrar_aviso(self.dir, "11111111111111111", "aaaaaaaaaaa", CANAL_A)
        avisos = avisos_recentes(self.dir, dias=1)
        self.assertEqual(1, len(avisos))
        self.assertEqual("11111111111111111", avisos[0]["message_id"])
        self.assertEqual("aaaaaaaaaaa", avisos[0]["video_id"])
        self.assertEqual(CANAL_A, avisos[0]["channel_id"])

    def test_o_arquivo_e_mensal(self):
        registrar_aviso(self.dir, "11111111111111111", "aaaaaaaaaaa", CANAL_A)
        esperado = caminho_avisos(self.dir)
        self.assertTrue(esperado.is_file())
        self.assertRegex(esperado.name, r"^\d{4}-\d{2}\.jsonl$")

    def test_a_janela_le_dois_arquivos_mensais(self):
        """Uma janela de 7 dias que começa dia 28 pega o mês seguinte.

        Ler só o mês corrente perderia justamente os avisos mais velhos da janela — que
        são os que já tiveram tempo de receber reação.
        """
        hoje = datetime.now(timezone.utc)
        mes_passado = hoje - timedelta(days=31)
        anexar_linha(caminho_avisos(self.dir, mes_passado.date()), {
            "em": (hoje - timedelta(days=2)).isoformat(timespec="seconds"),
            "message_id": "22222222222222222", "video_id": "bbbbbbbbbbb",
            "channel_id": CANAL_A,
        })
        registrar_aviso(self.dir, "11111111111111111", "aaaaaaaaaaa", CANAL_A)
        ids = {a["video_id"] for a in avisos_recentes(self.dir, dias=7)}
        self.assertEqual({"aaaaaaaaaaa", "bbbbbbbbbbb"}, ids)

    def test_aviso_fora_da_janela_e_descartado(self):
        anexar_linha(caminho_avisos(self.dir), {
            "em": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
            "message_id": "22222222222222222", "video_id": "antigo", "channel_id": CANAL_A,
        })
        self.assertEqual([], avisos_recentes(self.dir, dias=7))

    def test_carimbo_ilegivel_e_pulado_sem_derrubar(self):
        anexar_linha(caminho_avisos(self.dir), {
            "em": "não é data", "message_id": "x", "video_id": "y", "channel_id": CANAL_A,
        })
        registrar_aviso(self.dir, "11111111111111111", "aaaaaaaaaaa", CANAL_A)
        self.assertEqual(["aaaaaaaaaaa"], [a["video_id"] for a in avisos_recentes(self.dir, 7)])

    def test_a_ordem_e_cronologica(self):
        for numero in range(3):
            anexar_linha(caminho_avisos(self.dir), {
                "em": f"2026-08-2{numero}T10:00:00+00:00",
                "message_id": str(numero), "video_id": f"video{numero}", "channel_id": CANAL_A,
            })
        avisos = avisos_recentes(self.dir, dias=3650)
        self.assertEqual(["video0", "video1", "video2"], [a["video_id"] for a in avisos])


class TestSinais(Base):
    def _sinal(self, **campos):
        base = {
            "message_id": "11111111111111111", "user_id": "22222222222222222",
            "reacao": "👍", "video_id": "aaaaaaaaaaa", "handle": "@canal",
        }
        base.update(campos)
        return base

    def test_registrar_e_reler(self):
        self.assertTrue(registrar_sinal(self.dir, self._sinal()))
        registros = sinais(self.dir)
        self.assertEqual(1, len(registros))
        self.assertEqual("👍", registros[0]["reacao"])
        self.assertTrue(registros[0]["em"], "o carimbo é preenchido sozinho")

    def test_o_mesmo_sinal_duas_vezes_nao_duplica(self):
        """Sem isto, um único 👍 viraria ~670 sinais por semana."""
        self.assertTrue(registrar_sinal(self.dir, self._sinal()))
        self.assertFalse(registrar_sinal(self.dir, self._sinal()))
        self.assertEqual(1, len(sinais(self.dir)))

    def test_a_chave_de_idempotencia_e_mensagem_usuario_reacao_e_video(self):
        registrar_sinal(self.dir, self._sinal())
        for mudanca in ({"message_id": "99999999999999999"},
                        {"user_id": "99999999999999999"},
                        {"reacao": "👎"},
                        {"video_id": "bbbbbbbbbbb"}):
            with self.subTest(mudanca=mudanca):
                self.assertTrue(registrar_sinal(self.dir, self._sinal(**mudanca)))
        self.assertEqual(5, len(sinais(self.dir)))

    def test_o_handle_nao_entra_na_chave_de_idempotencia(self):
        """O handle é enfeite de apresentação: o mesmo polegar no mesmo vídeo é o mesmo
        sinal, ainda que o nome do canal tenha sido resolvido diferente entre ciclos.
        """
        registrar_sinal(self.dir, self._sinal())
        self.assertFalse(registrar_sinal(self.dir, self._sinal(handle="@outro_nome")))
        self.assertEqual(1, len(sinais(self.dir)))

    def test_o_conjunto_de_existentes_e_atualizado_entre_chamadas(self):
        """Quem passa `existentes` evita reler o arquivo por sinal — mas o conjunto tem
        de crescer, senão duas chamadas seguidas com o mesmo sinal duplicam.
        """
        existentes = set()
        self.assertTrue(registrar_sinal(self.dir, self._sinal(), existentes))
        self.assertFalse(registrar_sinal(self.dir, self._sinal(), existentes))
        self.assertEqual(1, len(sinais(self.dir)))

    def test_sem_arquivo_a_lista_e_vazia(self):
        self.assertEqual([], sinais(self.dir))


class TestPool2(Base):
    def _video(self, video_id="v1", titulo="Um short qualquer"):
        return Video(video_id=video_id, titulo=titulo, url=f"https://youtu.be/{video_id}",
                      channel_id=CANAL_A, descricao="descrição", publicado="2026-08-24T10:00:00+00:00",
                      views=123)

    def _canal(self, handle="algumcanal"):
        return Canal(channel_id=CANAL_A, handle=handle)

    def test_registrar_e_reler(self):
        registrar_candidato_pool2(self.dir, self._video(), self._canal())
        candidatos = candidatos_pool2_recentes(self.dir, dias=1)
        self.assertEqual(1, len(candidatos))
        self.assertEqual("v1", candidatos[0]["video_id"])
        self.assertEqual("algumcanal", candidatos[0]["handle"])
        self.assertEqual("short", candidatos[0]["motivo"])

    def test_o_arquivo_e_mensal(self):
        registrar_candidato_pool2(self.dir, self._video(), self._canal())
        esperado = caminho_pool2(self.dir)
        self.assertTrue(esperado.is_file())
        self.assertRegex(esperado.name, r"^\d{4}-\d{2}\.jsonl$")

    def test_o_mesmo_video_registrado_duas_vezes_nao_duplica_na_leitura(self):
        """Não devia acontecer — marcar como visto impede reaparecer — mas a leitura
        dedupa por `video_id` de qualquer jeito, ficando com o registro mais recente."""
        registrar_candidato_pool2(self.dir, self._video(), self._canal())
        registrar_candidato_pool2(self.dir, self._video(), self._canal())
        candidatos = candidatos_pool2_recentes(self.dir, dias=1)
        self.assertEqual(1, len(candidatos))

    def test_fora_da_janela_e_descartado(self):
        anexar_linha(caminho_pool2(self.dir), {
            "em": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
            "video_id": "antigo", "channel_id": CANAL_A, "handle": "algumcanal",
        })
        self.assertEqual([], candidatos_pool2_recentes(self.dir, dias=3))

    def test_a_janela_le_dois_arquivos_mensais(self):
        hoje = datetime.now(timezone.utc)
        mes_passado = hoje - timedelta(days=31)
        anexar_linha(caminho_pool2(self.dir, mes_passado.date()), {
            "em": (hoje - timedelta(days=1)).isoformat(timespec="seconds"),
            "video_id": "vAntigo", "channel_id": CANAL_A, "handle": "algumcanal",
        })
        registrar_candidato_pool2(self.dir, self._video(), self._canal())
        ids = {c["video_id"] for c in candidatos_pool2_recentes(self.dir, dias=3)}
        self.assertEqual({"v1", "vAntigo"}, ids)


class TestDigest(Base):
    def _digest(self, dia="2026-08-24") -> Digest:
        return Digest(data=dia, itens=[
            ItemDeDigest(video_id="aaaaaaaaaaa", channel_id=CANAL_A, titulo="Um",
                         score=9.5, decisao="enviado", razao="canal salvo 5×",
                         componentes={"canal": 5.0, "tag": 4.5}),
            ItemDeDigest(video_id="bbbbbbbbbbb", channel_id=CANAL_A, titulo="Dois",
                         score=1.0, decisao="cortado_por_canal", razao="já tem um deste canal"),
        ])

    def test_ida_e_volta_pelo_disco(self):
        salvar_digest(self.dir, self._digest())
        relido = carregar_digest(self.dir, "2026-08-24")
        self.assertIsNotNone(relido)
        self.assertEqual(2, len(relido.itens))
        self.assertEqual("aaaaaaaaaaa", relido.enviados[0].video_id)
        self.assertEqual({"canal": 5.0, "tag": 4.5}, relido.itens[0].componentes)

    def test_o_gerado_em_e_preenchido_no_salvar(self):
        digest = self._digest()
        self.assertEqual("", digest.gerado_em)
        salvar_digest(self.dir, digest)
        self.assertTrue(digest.gerado_em)

    def test_guarda_os_cortados_com_a_decisao(self):
        """É o que faz o `doctor` responder "por que não recomendou X?" sem adivinhação."""
        salvar_digest(self.dir, self._digest())
        relido = carregar_digest(self.dir, "2026-08-24")
        cortados = [i for i in relido.itens if i.decisao != "enviado"]
        self.assertEqual(1, len(cortados))
        self.assertEqual("cortado_por_canal", cortados[0].decisao)
        self.assertTrue(cortados[0].razao, "o corte tem de dizer por quê")

    def test_toda_decisao_usada_esta_no_vocabulario_declarado(self):
        salvar_digest(self.dir, self._digest())
        relido = carregar_digest(self.dir, "2026-08-24")
        for item in relido.itens:
            self.assertIn(item.decisao, DECISOES)

    def test_dia_sem_digest_devolve_none(self):
        self.assertIsNone(carregar_digest(self.dir, "1999-01-01"))

    def test_digest_corrompido_devolve_none_em_vez_de_levantar(self):
        caminho = caminho_digest(self.dir, "2026-08-24")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("{corrompido", encoding="utf-8")
        self.assertIsNone(carregar_digest(self.dir, "2026-08-24"))

    def test_recentes_acha_o_digest_de_hoje(self):
        hoje = datetime.now(timezone.utc).date().isoformat()
        salvar_digest(self.dir, Digest(data=hoje))
        recentes = digests_recentes(self.dir, dias=7)
        self.assertEqual(1, len(recentes))
        self.assertEqual(hoje, recentes[0].data)

    def test_recentes_ignora_dia_fora_da_janela(self):
        antigo = (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat()
        salvar_digest(self.dir, Digest(data=antigo))
        self.assertEqual([], digests_recentes(self.dir, dias=7))

    def test_um_documento_por_dia(self):
        salvar_digest(self.dir, self._digest("2026-08-24"))
        salvar_digest(self.dir, self._digest("2026-08-25"))
        arquivos = sorted(p.name for p in (self.dir / "digests").glob("*.json"))
        self.assertEqual(["2026-08-24.json", "2026-08-25.json"], arquivos)


if __name__ == "__main__":
    unittest.main()
