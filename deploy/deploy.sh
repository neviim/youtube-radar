#!/usr/bin/env bash
# Instala (primeira vez) ou atualiza (git pull + rebuild + restart) o
# youtube-radar no servidor de produção, via SSH — sem sudo, sem senha,
# depois que deploy/bootstrap-servidor.sh rodou lá uma vez.
#
#   ./deploy/deploy.sh              atualiza para o commit mais novo da branch
#   ./deploy/deploy.sh --seco       mostra o que mudaria, sem tocar em nada
#   ./deploy/deploy.sh --status     só mostra o commit e a saúde do container remoto
#
# Se algo não commitado for encontrado na pasta remota, ou o container não
# ficar saudável depois do rebuild, o deploy é abortado / revertido sozinho
# para o commit anterior — nunca deixa o servidor pela metade.
#
# Configuração (variáveis de ambiente, todas com default sensato pra este repo):
#   YTR_SSH_HOST      (default: 192.168.15.17)
#   YTR_SSH_USER      (default: neviim)
#   YTR_SSH_KEY       (default: ~/.ssh/ytr_deploy)
#   YTR_DEPLOY_DIR    (default: /opt/youtube-radar)
#   YTR_DEPLOY_BRANCH (default: feat/v1)
set -euo pipefail

SSH_HOST="${YTR_SSH_HOST:-192.168.15.17}"
SSH_USER="${YTR_SSH_USER:-neviim}"
SSH_KEY="${YTR_SSH_KEY:-$HOME/.ssh/ytr_deploy}"
DEPLOY_DIR="${YTR_DEPLOY_DIR:-/opt/youtube-radar}"
BRANCH="${YTR_DEPLOY_BRANCH:-feat/v1}"

info() { printf '\033[2m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31merro:\033[0m %s\n' "$*" >&2; exit 1; }

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
[[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
ssh_do() { ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "$@"; }

mode="${1:---up}"
case "$mode" in
    --status)
        info "status em ${SSH_HOST}:${DEPLOY_DIR}"
        ssh_do "cd '$DEPLOY_DIR' && git log -1 --oneline && ./ytr.sh prod status"
        exit 0
        ;;
    --seco|--up) : ;;
    *) die "modo desconhecido: $mode (--up | --seco | --status)" ;;
esac

info "conectando em ${SSH_USER}@${SSH_HOST}..."
ssh_do "test -d '$DEPLOY_DIR'" \
    || die "$DEPLOY_DIR não existe em ${SSH_HOST} — rode deploy/bootstrap-servidor.sh lá primeiro (uma vez só)"

DRY_RUN=0
[[ "$mode" == --seco ]] && DRY_RUN=1

# Só $DEPLOY_DIR e $BRANCH são substituídos aqui (localmente); tudo com \$ vira
# variável de verdade só quando o script roda do outro lado do SSH.
remote_script=$(cat <<REMOTE
set -euo pipefail
cd '$DEPLOY_DIR'

exec 9>.deploy.lock
flock -n 9 || { echo "outro deploy já em andamento em $DEPLOY_DIR" >&2; exit 1; }

if [[ -n "\$(git status --porcelain)" ]]; then
    echo "erro: há mudanças não commitadas em $DEPLOY_DIR — resolva na mão antes do deploy" >&2
    exit 1
fi

git fetch origin "$BRANCH"
PREV="\$(git rev-parse HEAD)"
NEW="\$(git rev-parse origin/$BRANCH)"

if [[ "\$PREV" == "\$NEW" ]]; then
    echo "já está no commit mais novo (\${PREV:0:7}) — nada pra atualizar."
    exit 0
fi

echo "atualizando \${PREV:0:7} -> \${NEW:0:7}:"
git log --oneline "\$PREV..\$NEW"

if [[ "\$DRY_RUN" == 1 ]]; then
    echo "(--seco: parei aqui, nada foi tocado)"
    exit 0
fi

git checkout "$BRANCH"
git merge --ff-only "origin/$BRANCH"

if [[ ! -f .env ]]; then
    echo "erro: não há .env em $DEPLOY_DIR — copie de .env.example e preencha antes de subir" >&2
    exit 1
fi

./ytr.sh prod up

ok=0
for _ in \$(seq 1 12); do
    sleep 5
    health="\$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-healthcheck{{end}}' ytr-prod 2>/dev/null || echo indisponivel)"
    echo "saúde: \$health"
    if [[ "\$health" == healthy || "\$health" == sem-healthcheck ]]; then ok=1; break; fi
    if [[ "\$health" == unhealthy ]]; then break; fi
done

if [[ "\$ok" != 1 ]]; then
    echo "container não ficou saudável depois do deploy — revertendo para \${PREV:0:7}" >&2
    git checkout "$BRANCH"
    git reset --hard "\$PREV"
    ./ytr.sh prod up
    exit 1
fi

echo "deploy ok, em \$(git rev-parse --short HEAD)"
REMOTE
)

ssh_do "DRY_RUN=$DRY_RUN bash -s" <<<"$remote_script"
