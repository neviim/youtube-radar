"""Rode com: python3 -m unittest tests.test_discord_client

`ytr/discord_client.py` é **vendorado verbatim** de `discord-link-brain:dlb/discord_client.py`
(commit `4674e72`, ver `docs/VENDORADO.md`). Os testes daqui não repetem a suíte de lá:
eles afirmam o **contrato do qual este projeto depende**, para uma re-vendoragem futura
que mude comportamento quebrar aqui em vez de aparecer como defeito de produção.

Três coisas que este projeto depende de verdade:

1. `snowflake_for` — é o que dá o cursor inicial do cadastro (`cadastro.processar`),
   "monitore a partir de agora" sem varrer o histórico do canal.
2. `DiscordError` em 401/403 — `cadastro._reagir` e `PublicadorDiscord.publicar` contam
   com essa exceção para não derrubar o ciclo quando falta permissão.
3. Retry em 429 até um sucesso — sem isto, um rate limit vira falha de POST em vez de
   uma espera.
"""

import unittest

from ytr.discord_client import DISCORD_EPOCH_MS, DiscordClient, DiscordError, snowflake_for


class _RespostaFalsa:
    def __init__(self, status_code, corpo=None, headers=None):
        self.status_code = status_code
        self._corpo = corpo if corpo is not None else {}
        self.headers = headers or {}
        self.content = b"x"

    def json(self):
        return self._corpo

    def raise_for_status(self):
        pass


class _SessaoFalsa:
    """Dublê da `requests.Session`: devolve as respostas na ordem, uma por chamada."""

    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = 0

    def _proxima(self, *a, **k):
        self.chamadas += 1
        return self._respostas.pop(0)

    put = _proxima
    post = _proxima
    get = _proxima
    delete = _proxima


class TestSnowflake(unittest.TestCase):
    def test_snowflake_for_e_a_formula_do_discord(self):
        self.assertEqual(0, int(snowflake_for(DISCORD_EPOCH_MS)) >> 22)
        # Um milissegundo depois da época move só o campo de timestamp, nada mais.
        self.assertEqual(1, int(snowflake_for(DISCORD_EPOCH_MS + 1)) >> 22)


class TestErros(unittest.TestCase):
    def _cliente(self, *respostas):
        client = DiscordClient("Bot x")
        client.session = _SessaoFalsa(respostas)
        return client

    def test_403_ao_reagir_levanta_discord_error(self):
        client = self._cliente(_RespostaFalsa(403))
        with self.assertRaises(DiscordError):
            client.add_reaction("1", "2", "📡")

    def test_401_ao_postar_levanta_discord_error(self):
        client = self._cliente(_RespostaFalsa(401))
        with self.assertRaises(DiscordError):
            client.post_message("1", "oi")

    def test_429_tenta_de_novo_e_depois_da_certo(self):
        client = self._cliente(
            _RespostaFalsa(429, headers={"Retry-After": "0"}),
            _RespostaFalsa(200, corpo={"id": "42"}),
        )
        saida = client.post_message("1", "oi")
        self.assertEqual("42", saida["id"])


if __name__ == "__main__":
    unittest.main()
