#!/usr/bin/env bash
# Wrapper de container do youtube-radar.
#
# A razão de existir: o docker compose não expande `~`, não sabe seu UID e não
# sabe onde mora o vault. Este script resolve isso a partir do .env e chama o
# compose com o ambiente já pronto.
#
#   ./ytr.sh dev doctor              checa config, vault e Discord (vault :ro)
#   ./ytr.sh dev ciclo --seco        ensaio, não escreve nada
#   ./ytr.sh dev perfil              perfil de gosto lido do vault
#   ./ytr.sh dev test                suíte de testes
#   ./ytr.sh dev shell                bash dentro do container
#   ./ytr.sh prod up                  sobe o agendador em background
#   ./ytr.sh prod logs                acompanha o log
#   ./ytr.sh prod run ciclo --seco    uma execução avulsa, fora do agendador
#   ./ytr.sh prod status              estado e saúde do container
#   ./ytr.sh prod down                derruba
#
# `./ytr.sh dev --rw <cmd>` monta o vault para escrita (o radar nunca escreve
# nele por conta própria — só use se for depurar algo que precise).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

die() { printf '\033[31merro:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[2m%s\033[0m\n' "$*" >&2; }

[[ -f .env ]] || die "não há .env. Rode: cp .env.example .env && \$EDITOR .env"

# Lê uma chave do .env sem executá-lo (o valor pode conter espaço, # e aspas).
env_get() {
    local key="$1" line
    line="$(grep -E "^[[:space:]]*${key}=" .env | tail -1 || true)"
    [[ -z "$line" ]] && return 0
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

expand_home() {
    local p="$1"
    case "$p" in
        "~") printf '%s' "$HOME" ;;
        "~/"*) printf '%s' "$HOME/${p#\~/}" ;;
        *) printf '%s' "$p" ;;
    esac
}

# ------------------------------------------------------------------- vault ---
VAULT_RAW="$(env_get OBSIDIAN_VAULT)"
[[ -n "$VAULT_RAW" ]] || die "OBSIDIAN_VAULT não está no .env"
YTR_HOST_VAULT="$(expand_home "$VAULT_RAW")"
[[ -d "$YTR_HOST_VAULT" ]] || die "vault não encontrado: $YTR_HOST_VAULT"
export YTR_HOST_VAULT

TIMEZONE="$(env_get TZ)"; export TIMEZONE="${TIMEZONE:-UTC}"

YTR_UID="$(id -u)"; YTR_GID="$(id -g)"; export YTR_UID YTR_GID

# Bind mount de arquivo único: se `canais.yaml` não existir no host, o docker cria
# um diretório no lugar dele — daí o touch, antes de qualquer coisa.
touch canais.yaml
mkdir -p .state

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"; shift || true

case "$mode" in
# --------------------------------------------------------------------- dev ---
dev)
    vault_mode=ro
    if [[ "${1:-}" == "--rw" ]]; then vault_mode=rw; shift; fi
    export YTR_DEV_VAULT="${YTR_DEV_VAULT:-$YTR_HOST_VAULT}"
    export YTR_DEV_VAULT_MODE="$vault_mode"

    info "dev · vault ${YTR_DEV_VAULT} (${vault_mode}) · tz ${TIMEZONE}"
    case "${1:-doctor}" in
        build) shift; exec docker compose --profile dev build "$@" ;;
        down|ps) exec docker compose --profile dev "$@" ;;
        *) exec docker compose --profile dev run --rm --build dev "$@" ;;
    esac
    ;;

# -------------------------------------------------------------------- prod ---
prod)
    # O compose valida os dois serviços mesmo com --profile; isto cala o `:?`
    # do serviço dev sem montar nada além do que prod já monta.
    export YTR_DEV_VAULT="$YTR_HOST_VAULT" YTR_DEV_VAULT_MODE=ro
    sub="${1:-status}"; shift || true
    info "prod · vault ${YTR_HOST_VAULT} (ro) · tz ${TIMEZONE}"
    case "$sub" in
        up)      exec docker compose --profile prod up -d --build "$@" ;;
        down)    exec docker compose --profile prod down "$@" ;;
        restart) exec docker compose --profile prod restart "$@" ;;
        build)   exec docker compose --profile prod build "$@" ;;
        logs)    exec docker compose --profile prod logs -f --tail 200 "$@" ;;
        status|ps)
            docker compose --profile prod ps
            docker inspect --format \
                'saúde: {{if .State.Health}}{{.State.Health.Status}}{{else}}sem healthcheck{{end}} · desde {{.State.StartedAt}}' \
                ytr-prod 2>/dev/null || true
            ;;
        run)     exec docker compose --profile prod run --rm prod "$@" ;;
        exec)    exec docker compose --profile prod exec prod "$@" ;;
        *) die "subcomando desconhecido: $sub (up|down|restart|build|logs|status|run|exec)" ;;
    esac
    ;;

build)
    export YTR_DEV_VAULT="$YTR_HOST_VAULT" YTR_DEV_VAULT_MODE=ro
    exec docker compose --profile dev --profile prod build "$@"
    ;;

help|-h|--help) usage ;;
*) die "modo desconhecido: $mode (use: dev | prod | build | help)" ;;
esac
