# ---------------------------------------------------------------------------
# VENDORADO de discord-link-brain:dlb/discord_client.py
#   commit de origem: 4674e72c1892e944237937bf86865e57d445aa18
#   copiado: 2026-08-24 · adaptado: só `USER_AGENT`, o resto é verbatim.
# Regra: conserta em cima (no projeto de origem), depois re-vendora. Ver
# docs/VENDORADO.md.
# ---------------------------------------------------------------------------
"""Cliente mínimo da REST API do Discord — só o que precisamos: ler mensagens."""

from __future__ import annotations

import time
from typing import Iterator
from urllib.parse import quote

import requests

API = "https://discord.com/api/v10"
USER_AGENT = "youtube-radar/0.1"

# Discord usa snowflakes: os 42 bits mais altos são o timestamp em ms desde
# 2015-01-01. Isso deixa a gente pedir "mensagens depois de <data>" sem cursor.
DISCORD_EPOCH_MS = 1420070400000


def snowflake_for(timestamp_ms: int) -> str:
    return str((timestamp_ms - DISCORD_EPOCH_MS) << 22)


def ja_marcada(message: dict, *emojis: str) -> bool:
    """A mensagem já recebeu **do próprio bot** alguma destas marcas?

    O campo `me` do Discord é justamente 'esta reação inclui o usuário
    autenticado'. Reação do humano com o mesmo emoji não conta — senão você
    marcaria um link à mão e ele nunca seria arquivado.

    Aceita várias marcas porque qualquer uma delas significa a mesma coisa para
    o pipeline: esta mensagem já foi resolvida, não precisa ser reprocessada.
    """
    alvo = set(emojis)
    for reacao in message.get("reactions") or []:
        if reacao.get("me") and (reacao.get("emoji") or {}).get("name") in alvo:
            return True
    return False


class DiscordError(RuntimeError):
    pass


class DiscordClient:
    def __init__(self, auth_header: str, timeout: float = 20.0):
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": auth_header, "User-Agent": USER_AGENT}
        )
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{API}{path}"
        for attempt in range(6):
            resp = self.session.get(url, params=params, timeout=self.timeout)

            if resp.status_code == 429:
                # Respeita o rate limit em vez de martelar a API.
                retry_after = float(resp.headers.get("Retry-After", "1"))
                try:
                    retry_after = float(resp.json().get("retry_after", retry_after))
                except Exception:
                    pass
                time.sleep(min(retry_after, 30) + 0.25)
                continue

            if resp.status_code in (401, 403):
                raise DiscordError(
                    f"{resp.status_code} em {path}: token inválido ou sem permissão "
                    "(o bot precisa de View Channel + Read Message History no canal)."
                )
            if resp.status_code == 404:
                raise DiscordError(f"404 em {path}: canal não encontrado.")
            if resp.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue

            resp.raise_for_status()
            return resp.json()

        raise DiscordError(f"Desisti após várias tentativas em {path}.")

    def channel_name(self, channel_id: str) -> str:
        data = self._get(f"/channels/{channel_id}")
        return data.get("name", channel_id) if isinstance(data, dict) else channel_id

    def guild_id(self, channel_id: str) -> str | None:
        """A que servidor o canal pertence. `None` em DM.

        `GET /channels/{id}/messages` **não** devolve `guild_id` em cada
        mensagem, então sem esta chamada o link para a mensagem original sai
        como `/channels/@me/...` e não abre num canal de servidor.
        """
        data = self._get(f"/channels/{channel_id}")
        return data.get("guild_id") if isinstance(data, dict) else None

    def remove_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """Tira uma reação do bot. Silencioso se ela não estava lá."""
        caminho = (
            f"/channels/{channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji, safe='')}/@me"
        )
        resp = self.session.delete(f"{API}{caminho}", timeout=self.timeout)
        if resp.status_code in (204, 404):
            return
        if resp.status_code in (401, 403):
            raise DiscordError(f"{resp.status_code} ao remover reação: sem permissão.")
        resp.raise_for_status()

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """Marca a mensagem com um emoji, em nome do bot.

        Levanta DiscordError em 403 (falta a permissão *Adicionar reações*) —
        quem chama decide se isso interrompe algo. Aqui não interrompe: a
        marcação é sinal para humano, o cursor é que garante idempotência.
        """
        caminho = (
            f"/channels/{channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji, safe='')}/@me"
        )
        for tentativa in range(4):
            resp = self.session.put(f"{API}{caminho}", timeout=self.timeout)
            if resp.status_code == 429:
                espera = float(resp.headers.get("Retry-After", "1"))
                time.sleep(min(espera, 30) + 0.25)
                continue
            if resp.status_code in (401, 403):
                raise DiscordError(
                    f"{resp.status_code} ao reagir: o bot precisa da permissão "
                    "'Adicionar reações' no canal."
                )
            if resp.status_code >= 500:
                time.sleep(1.5 * (tentativa + 1))
                continue
            resp.raise_for_status()
            return
        raise DiscordError("Desisti de reagir após várias tentativas.")


    # ------------------------------------------------------------------ falar

    def me(self) -> dict:
        """O próprio bot. Serve para não responder à própria mensagem por id, e
        não só pela flag `bot` — a checagem que mais importa não deve depender
        de o Discord ter marcado a flag."""
        data = self._get("/users/@me")
        return data if isinstance(data, dict) else {}

    def post_message(
        self,
        channel_id: str,
        content: str,
        responder_a: str | None = None,
    ) -> dict:
        """Posta no canal. Exige a permissão *Enviar mensagens* (2048).

        `allowed_mentions` vazio de propósito: assim o bot **não consegue**
        mencionar ninguém, nem `@everyone`, mesmo que a pergunta de alguém ou o
        resumo de uma página contenha esse texto. É mais barato desligar a
        capacidade do que filtrar o conteúdo.
        """
        corpo: dict = {"content": content, "allowed_mentions": {"parse": []}}
        if responder_a:
            # `fail_if_not_exists`: mensagem apagada não deve derrubar a resposta.
            corpo["message_reference"] = {
                "message_id": responder_a,
                "fail_if_not_exists": False,
            }

        caminho = f"/channels/{channel_id}/messages"
        for tentativa in range(4):
            resp = self.session.post(f"{API}{caminho}", json=corpo, timeout=self.timeout)
            if resp.status_code == 429:
                espera = float(resp.headers.get("Retry-After", "1"))
                try:
                    espera = float(resp.json().get("retry_after", espera))
                except Exception:
                    pass
                time.sleep(min(espera, 30) + 0.25)
                continue
            if resp.status_code in (401, 403):
                raise DiscordError(
                    f"{resp.status_code} ao postar: o bot precisa da permissão "
                    "'Enviar mensagens' no canal (inteiro de permissões 68672)."
                )
            if resp.status_code >= 500:
                time.sleep(1.5 * (tentativa + 1))
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        raise DiscordError("Desisti de postar após várias tentativas.")

    def delete_message(self, channel_id: str, message_id: str) -> None:
        """Apaga uma mensagem. A própria não exige permissão extra — é o que
        permite ao `doctor --testar-envio` conferir o envio sem deixar lixo."""
        caminho = f"/channels/{channel_id}/messages/{message_id}"
        resp = self.session.delete(f"{API}{caminho}", timeout=self.timeout)
        if resp.status_code in (204, 404):
            return
        if resp.status_code in (401, 403):
            raise DiscordError(f"{resp.status_code} ao apagar: sem permissão.")
        resp.raise_for_status()

    def iter_messages(
        self,
        channel_id: str,
        after: str | None = None,
        limit_total: int | None = None,
    ) -> Iterator[dict]:
        """Percorre as mensagens do canal em ordem cronológica crescente.

        `after` é um snowflake: só vêm mensagens posteriores a ele. O Discord
        devolve no máximo 100 por vez, da mais nova para a mais velha — por isso
        invertemos cada lote e avançamos o cursor pelo maior id visto.

        Sem `after` não há como paginar para trás aqui: você recebe só as 100
        mensagens mais recentes e o loop encerra. Passe sempre um snowflake
        (`snowflake_for`) ou o cursor salvo em state.
        """
        cursor = after
        yielded = 0

        while True:
            params: dict[str, str | int] = {"limit": 100}
            if cursor:
                params["after"] = cursor

            batch = self._get(f"/channels/{channel_id}/messages", params)
            if not isinstance(batch, list) or not batch:
                return

            batch.sort(key=lambda m: int(m["id"]))
            for message in batch:
                yield message
                yielded += 1
                if limit_total is not None and yielded >= limit_total:
                    return

            cursor = batch[-1]["id"]
            if len(batch) < 100:
                return


def partir_para_discord(texto: str, limite: int = 1900) -> list[str]:
    """Quebra o texto em pedaços que caibam numa mensagem do Discord.

    `limite` 1900 e não 2000: o Discord conta unidades UTF-16, então um emoji
    fora do BMP custa 2 lá e 1 no `len()` do Python. A folga de 100 absorve isso
    sem precisar de aritmética de surrogates.

    Quebra na maior fronteira que couber — parágrafo, linha, espaço — e nunca no
    meio de uma URL, que é o que mais estraga a leitura.
    """
    texto = texto.strip()
    if not texto:
        return []
    if len(texto) <= limite:
        return [texto]

    # O marcador "… (10/10)" entra depois do corte, então o espaço dele tem que
    # ser reservado antes — senão o último pedaço estoura justamente o limite que
    # esta função existe para respeitar.
    util = limite - 16
    pedacos: list[str] = []
    resto = texto
    while resto:
        if len(resto) <= util:
            pedacos.append(resto)
            break
        janela = resto[:util]
        corte = -1
        for separador in ("\n\n", "\n", " "):
            corte = janela.rfind(separador)
            if corte > util // 3:
                break
        if corte <= util // 3:
            corte = util  # sem fronteira boa: corta seco em vez de estourar
        pedacos.append(resto[:corte].rstrip())
        resto = resto[corte:].lstrip()

    total = len(pedacos)
    if total > 1:
        pedacos = [f"{p}\n\n… ({i}/{total})" for i, p in enumerate(pedacos, 1)]
    return pedacos
