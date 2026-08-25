#!/bin/sh
# Agendador de produção: mantém o processo vivo, chama `ytr ciclo` no piso da
# cadência que o próprio feed declara, e `ytr digest` uma vez por dia.
#
# Preferi isto a cron dentro do container pelos mesmos três motivos do projeto
# irmão: cron não herda o ambiente do container, os logs não vão para o stdout do
# PID 1, e um SIGTERM do `docker stop` não chega ao job em execução.
#
#   YTR_CICLO_INTERVALO   segundos entre execuções (padrão: YTR_PISO_SEGUNDOS,
#                         900 — não corre à frente do cache-control do feed)
#   YTR_CICLO_ARGS        argumentos extras para o ciclo (ex.: "--seco")
#   YTR_RUN_ON_START      1 = roda uma vez ao subir (padrão 1)
#   YTR_DIGEST_AT         HH:MM, uma vez por dia (padrão 08:00, mesmo do .env.example)
#
# O digest não ganha o próprio laço de `sleep`: ele é checado a cada ciclo (o mesmo
# `nap`), e só roda de fato quando o relógio já passou de `YTR_DIGEST_AT` e ainda não
# rodou hoje. Rodar a cada 15 min, e não num segundo laço próprio, evita um PID a mais
# só para uma checagem de data — e `cmd_digest` já é idempotente por dia (recusa
# regenerar e reescreveria os `message_id` de quem já foi postado), então esta guarda
# aqui é otimização de não gastar rede à toa, não a garantia de verdade.
set -eu

INTERVAL="${YTR_CICLO_INTERVALO:-${YTR_PISO_SEGUNDOS:-900}}"
RUN_ON_START="${YTR_RUN_ON_START:-1}"
CICLO_ARGS="${YTR_CICLO_ARGS:-}"
DIGEST_AT="${YTR_DIGEST_AT:-08:00}"
HEARTBEAT="${YTR_HEARTBEAT:-/app/.state/heartbeat}"

ultimo_digest=""
sleep_pid=""

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

on_term() {
    log "SIGTERM recebido — encerrando."
    [ -n "$sleep_pid" ] && kill "$sleep_pid" 2>/dev/null || true
    exit 0
}
trap on_term TERM INT

# Dorme de forma interrompível: `sleep` em background + `wait` deixa o trap rodar
# na hora, em vez de o `docker stop` ter que esperar o timeout de 10s.
nap() {
    sleep "$1" &
    sleep_pid=$!
    wait "$sleep_pid" 2>/dev/null || true
    sleep_pid=""
}

run_ciclo() {
    log "ytr ciclo ${CICLO_ARGS}"
    set +e
    # shellcheck disable=SC2086  # CICLO_ARGS é lista de flags, quer split
    python -m ytr ciclo ${CICLO_ARGS}
    rc=$?
    set -e
    case "$rc" in
        0|1)
            # 0 = sem erro; 1 = o ciclo rodou e algum canal individual falhou (o
            # próprio ciclo nunca posta por um canal doente — só recua). Nos dois
            # casos o mecanismo funcionou, então o heartbeat avança.
            mkdir -p "$(dirname "$HEARTBEAT")"
            date +%s >"$HEARTBEAT"
            log "ciclo concluído (código $rc)."
            ;;
        *)
            # 2 = estado/config não deixou nem começar; 3 = outra instância detém
            # a trava. Problema nosso ou colisão — não avança o heartbeat, e
            # depois do suficiente o HEALTHCHECK acusa.
            log "ciclo FALHOU (código $rc) — heartbeat não avança."
            ;;
    esac
}

# "HH:MM" -> minutos desde a meia-noite. `10#` (prefixo de base) não é POSIX — o
# `dash` deste container rejeita com "expecting EOF", só o `bash` entende. Em vez
# disso, tira **um** zero à esquerda (a forma POSIX de evitar que "08"/"09" sejam
# lidos como octal inválido): "08" -> "8", "00" -> "0", "23" fica "23" (não começa
# com zero, nada a tirar).
_sem_zero_a_esquerda() {
    valor="${1#0}"
    printf '%s' "${valor:-0}"
}

minutos_do_dia() {
    h="$(_sem_zero_a_esquerda "${1%%:*}")"
    m="$(_sem_zero_a_esquerda "${1#*:}")"
    echo $((h * 60 + m))
}

run_digest_se_na_hora() {
    hoje="$(date +%F)"
    [ "$ultimo_digest" = "$hoje" ] && return 0
    [ "$(minutos_do_dia "$(date +%H:%M)")" -lt "$(minutos_do_dia "$DIGEST_AT")" ] && return 0

    log "ytr digest"
    set +e
    python -m ytr digest
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
        ultimo_digest="$hoje"
    else
        log "digest FALHOU (código $rc) — tenta de novo no próximo ciclo."
    fi
}

log "modo grade: ciclo a cada ${INTERVAL}s · digest às ${DIGEST_AT} (fuso ${TZ:-?})"

if [ "$RUN_ON_START" = "1" ]; then
    run_ciclo
    run_digest_se_na_hora
fi

while true; do
    log "próximo ciclo em ${INTERVAL}s."
    nap "$INTERVAL"
    run_ciclo
    run_digest_se_na_hora
done
