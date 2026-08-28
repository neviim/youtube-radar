# Instalação em produção

Passo a passo para instalar o youtube-radar do zero num servidor de produção,
usando os scripts em `deploy/`. Depois da primeira instalação, toda
atualização volta a ser um único comando (seção final).

## Antes de começar

- Um servidor Linux acessível por SSH, com uma conta de usuário comum
  (não-root) que tenha `sudo` disponível — aqui chamado de `neviim`, IP de
  exemplo `192.168.15.17`. Troque pelos seus.
- `docker` + `docker compose` (plugin v2) instalados no servidor. Se não
  estiverem: `curl -fsSL https://get.docker.com | sudo sh`, rodado uma vez lá.

**Sobre o projeto irmão (`discord-link-brain`):** os dois compartilham o
mesmo vault do Obsidian no mesmo host, mas a instalação de um **não depende**
da do outro. Pode instalar este antes, depois, ou nunca instalar o outro — o
`bootstrap-servidor.sh` de cada projeto usa pasta própria
(`/opt/youtube-radar` vs `/opt/discord-link-brain`) e a única coisa que os
dois mexem em comum, o grupo `docker` do usuário, é idempotente: o primeiro
script que rodar faz a mudança, o segundo só confere que já está feita e
segue. Ver `docs/INSTALACAO_PRODUCAO.md` do `discord-link-brain` para o
passo a passo dele.

## 1 · Chave SSH dedicada (uma vez, no seu computador)

Evita digitar a senha do servidor em todo deploy futuro. Se você já tem uma
chave assim (por exemplo, porque já instalou o `discord-link-brain` antes),
**pule para o passo 2** — é a mesma chave para os dois projetos, mesmo
usuário e mesmo host.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ytr_deploy -C "ytr-deploy@$(hostname)" -N ""
ssh-copy-id -i ~/.ssh/ytr_deploy.pub neviim@192.168.15.17   # pede a senha, uma última vez
```

Alias opcional em `~/.ssh/config`, pra digitar menos:

```
Host ytr-prod
    HostName 192.168.15.17
    User neviim
    IdentityFile ~/.ssh/ytr_deploy
    IdentitiesOnly yes
```

## 2 · Preparo do servidor (uma vez, pede `sudo`)

```bash
scp deploy/bootstrap-servidor.sh neviim@192.168.15.17:/tmp/ytr-bootstrap.sh
ssh -t neviim@192.168.15.17 'bash /tmp/ytr-bootstrap.sh'
```

O que ele faz, na ordem, e por quê:

1. Confere `docker` e `docker compose` — para de propósito se não achar,
   com a instrução de instalar antes.
2. Coloca `neviim` no grupo `docker`, **se ainda não estiver** — daí pra
   frente, `docker`/`docker compose` funcionam sem `sudo`.
3. Cria `/opt/youtube-radar`, dono `neviim:docker`, `setgid` (`chmod 2775`)
   — é a única parte que pede `sudo` depois da instalação em si; tudo que
   nascer dentro da pasta (o clone, os builds) já sai com o dono certo.
4. Clona o repositório ali, na branch configurada (`feat/v1` por padrão).
5. Copia `.env.example` → `.env`, **sem preencher nada** — segredo não entra
   em script.

Se o script tiver adicionado o grupo `docker` agora (mensagem
`IMPORTANTE: relogue...`), a próxima conexão SSH já resolve isso sozinha —
não precisa fazer nada além de abrir uma sessão nova.

## 3 · Preencher o `.env` no servidor

```bash
ssh -t neviim@192.168.15.17 'nano /opt/youtube-radar/.env'
```

Mínimo pro bot falar no Discord: `DISCORD_TOKEN`, `YTR_CANAL_ENTRADA`,
`YTR_CANAL_AVISO`. Confira também `OBSIDIAN_VAULT` — já vem
`~/Documents/Obsidian/vault` por padrão, só precisa bater com o caminho real
usado pelo `discord-link-brain` (ou, sem ele, com um vault vazio: veja o
próximo passo).

## 4 · Vault do Obsidian

O radar só lê `50_LINKS/` dentro do vault (`ytr/config.py:exigir_vault`) —
sem essa subpasta, `doctor` e `ciclo` recusam rodar. Duas situações:

- **Já instalou o `discord-link-brain` neste servidor:** o
  `bootstrap-servidor.sh` dele já criou o esqueleto inteiro do vault,
  incluindo `50_LINKS/`. Nada a fazer aqui.
- **Ainda não:** crie ao menos a subpasta, pra não travar o `doctor`:
  ```bash
  ssh neviim@192.168.15.17 'mkdir -p ~/Documents/Obsidian/vault/50_LINKS'
  ```
  O radar funciona normalmente assim — só o perfil de gosto fica sem sinal
  (nenhuma nota real pra ler) até o vault ganhar conteúdo de verdade.

## 5 · Primeiro deploy

```bash
./deploy/deploy.sh
```

Sobe o container mesmo sem commit novo pra puxar (o clone do bootstrap já
está no HEAD) — o script confere se `ytr-prod` está rodando, não só se há
atualização de código, e sobe se não estiver.

## 6 · Verificar

```bash
./deploy/deploy.sh --status
```

Mostra o commit atual e a saúde do container (`docker inspect` por baixo).
Alternativa, direto no servidor: `ssh neviim@192.168.15.17 'cd /opt/youtube-radar && ./ytr.sh prod status'`.

## Depois disso: atualizar

```bash
./deploy/deploy.sh --seco   # opcional: mostra o que mudaria, sem tocar em nada
./deploy/deploy.sh          # git pull + rebuild + restart; reverte sozinho se não ficar saudável
```

Sem sudo, sem senha, sem autorização repetida — tudo isso já foi resolvido
de uma vez no passo 2.

## Se algo der errado

- **`$DEPLOY_DIR não existe`** — o passo 2 não rodou (ou rodou em outro
  caminho). Confira `YTR_DEPLOY_DIR` se você mudou o padrão.
- **`erro: não há .env`** — volte ao passo 3.
- **Container fica `unhealthy` e reverte sozinho** — `ssh neviim@192.168.15.17 'cd /opt/youtube-radar && ./ytr.sh prod logs'`
  pra ver o motivo antes de tentar de novo.
- **`outro deploy já em andamento`** — um `deploy.sh` anterior ainda está
  rodando (ou travou); o lock é `/opt/youtube-radar/.deploy.lock`.
