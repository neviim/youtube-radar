#!/bin/sh
# Agendador de produção: mantém o processo vivo e chama `ytr ciclo` no piso da
# cadência que o próprio feed declara.
#
# Preferi isto a cron dentro do container pelos mesmos três motivos do projeto
# irmão: cron não herda o ambiente do container, os logs não vão para o stdout do
# PID 1, e um SIGTERM do `docker stop` não chega ao job em execução.
#
#   YTR_CICLO_INTERVALO   segundos entre execuções (padrão: YTR_PISO_SEGUNDOS,
#                         900 — não corre à frente do cache-control do feed)
#   YTR_CICLO_ARGS        argumentos extras para o ciclo (ex.: "--seco")
#   YTR_RUN_ON_START      1 = roda uma vez ao subir (padrão 1)
set -eu

INTERVAL="${YTR_CICLO_INTERVALO:-${YTR_PISO_SEGUNDOS:-900}}"
RUN_ON_START="${YTR_RUN_ON_START:-1}"
CICLO_ARGS="${YTR_CICLO_ARGS:-}"
HEARTBEAT="${YTR_HEARTBEAT:-/app/.state/heartbeat}"

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

log "modo grade: a cada ${INTERVAL}s (fuso ${TZ:-?})"

if [ "$RUN_ON_START" = "1" ]; then
    run_ciclo
fi

while true; do
    log "próximo ciclo em ${INTERVAL}s."
    nap "$INTERVAL"
    run_ciclo
done
