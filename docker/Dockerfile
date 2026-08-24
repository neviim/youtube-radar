# syntax=docker/dockerfile:1
#
# Duas metas (targets):
#   dev   — código vem por bind mount, roda sob demanda
#   prod  — código assado na imagem, agendador roda sozinho e reinicia sozinho
#
# O UID/GID são argumentos de build porque o container escreve em `.state` e lê o
# vault do Obsidian, que vivem no host: rodar como root deixaria arquivo root em
# diretório do host.

FROM python:3.12-slim AS base

ARG UID=1000
ARG GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/ytr \
    CLAUDE_CONFIG_DIR=/home/ytr/.claude \
    CODEX_HOME=/home/ytr/.codex

# ca-certificates: o feed do YouTube e o Discord são HTTPS. tzdata: TZ precisa
# existir dentro do container, senão o horário do digest (YTR_DIGEST_AT) sai errado.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    if ! getent group "${GID}" >/dev/null; then groupadd -g "${GID}" ytr; fi; \
    if ! getent passwd "${UID}" >/dev/null; then \
        useradd -u "${UID}" -g "${GID}" -M -d /home/ytr -s /bin/bash ytr; \
    fi; \
    mkdir -p /home/ytr/.claude /home/ytr/.codex /app/.state; \
    chown -R "${UID}:${GID}" /home/ytr /app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY docker/entrypoint.sh docker/scheduler.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/scheduler.sh

ENTRYPOINT ["entrypoint.sh"]


# ------------------------------------------------------------------- dev ---
# O compose sobrepõe /app com o diretório do host, então o COPY aqui só serve
# para a imagem funcionar solta (docker run sem compose).
FROM base AS dev

ENV YTR_MODE=dev

COPY --chown=${UID}:${GID} . /app
USER ${UID}:${GID}
CMD ["doctor"]


# ------------------------------------------------------------------ prod ---
FROM base AS prod

ENV YTR_MODE=prod

COPY --chown=${UID}:${GID} ytr/ /app/ytr/
COPY --chown=${UID}:${GID} requirements.txt README.md /app/
USER ${UID}:${GID}

# Sem argumento a imagem de produção sobe o agendador, não um ciclo avulso.
CMD ["scheduler"]

HEALTHCHECK --interval=5m --timeout=10s --start-period=1m --retries=2 \
    CMD ["entrypoint.sh", "healthcheck"]
