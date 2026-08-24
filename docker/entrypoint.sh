#!/bin/sh
# Traduz o argumento do container para o que deve rodar de fato.
#
#   (nada)               -> o CMD da imagem (dev: doctor, prod: scheduler)
#   scheduler             -> laço de agendamento (produção)
#   healthcheck           -> verifica o heartbeat do agendador
#   test | tests          -> ./testar.py
#   shell | bash | sh      -> shell interativo
#   feed | ciclo | ...     -> python -m ytr <args>
set -eu

HEARTBEAT="${YTR_HEARTBEAT:-/app/.state/heartbeat}"

case "${1:-}" in
    scheduler)
        exec scheduler.sh
        ;;

    healthcheck)
        # Saudável se o último ciclo que rodou de verdade cabe na janela de
        # tolerância. Sem heartbeat ainda (container recém-subido), aceita: o
        # --start-period do HEALTHCHECK já cobre esse intervalo.
        [ -f "$HEARTBEAT" ] || exit 0
        now=$(date +%s)
        last=$(cat "$HEARTBEAT" 2>/dev/null || echo 0)
        tolerance="${YTR_HEALTH_TOLERANCE:-3600}"
        age=$((now - last))
        if [ "$age" -gt "$tolerance" ]; then
            echo "último ciclo há ${age}s (limite ${tolerance}s)" >&2
            exit 1
        fi
        echo "último ciclo há ${age}s"
        exit 0
        ;;

    test|tests)
        exec python testar.py
        ;;

    shell|bash)
        shift
        exec /bin/bash "$@"
        ;;

    sh)
        shift
        exec /bin/sh "$@"
        ;;

    "")
        exec python -m ytr doctor
        ;;

    *)
        exec python -m ytr "$@"
        ;;
esac
