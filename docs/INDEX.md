# youtube-radar — documentação

Índice de entrada do projeto: o que ele faz, como instalar, como usar, a linha do
tempo de como foi construído e o que ainda falta. Para as decisões de design por
trás de cada escolha, veja o plano original
(`~/.buzz/.scratch/PLANO_CLAUDE_youtube_radar.md`) e `docs/VENDORADO.md` (código
copiado do `discord-link-brain`, e por quê).

## O que este projeto faz

`youtube-radar` monitora canais do YouTube por RSS — sem chave de API — e avisa
no Discord quando sai vídeo novo. Cadastro de canal também é pelo Discord: poste
o link de um canal no canal de entrada e o bot resolve o `channel_id`, confirma
pelo próprio feed e passa a monitorar sozinho, sem precisar editar arquivo.

Além de monitorar, ele recomenda: lê (somente leitura) os vídeos que você já
salvou no seu vault do Obsidian, monta um perfil de gosto por afinidade de canal,
de tag e por sobreposição léxica com o que você escreveu sobre cada link, e todo
dia manda um digest com 3-5 sugestões de canais que você já demonstrou gostar —
nunca de um algoritmo de terceiro, nunca vídeo sem checar que o link ainda existe
(liveness por oEmbed). Um modelo (`claude`/`codex` local, ou a API da Anthropic)
pode narrar esse ranking em prosa, mas nunca escolhe o que entra — a decisão é
sempre de regras determinísticas, testáveis e auditáveis pelo `doctor`.

Rodando de 15 em 15 minutos e uma vez por dia, respectivamente, o ciclo de
monitoração e o digest cabem em ~5-10 MB/dia de rede e não precisam de GPU, banco
de dados ou serviço de terceiro além do próprio Discord e do YouTube.

## Instalação

**Requisito:** Python 3.12+. Docker é opcional (recomendado para produção).

```bash
git clone https://github.com/neviim/youtube-radar.git
cd youtube-radar
pip install -r requirements.txt
cp .env.example .env
$EDITOR .env
```

O `.env.example` já vem comentado, variável por variável. As três que **têm** de
ser preenchidas para o bot falar no Discord:

```
DISCORD_TOKEN=...
YTR_CANAL_ENTRADA=...   # onde postar link de canal para cadastrar
YTR_CANAL_AVISO=...     # onde o radar avisa vídeo novo e manda o digest
```

Sem elas o radar continua funcionando só como monitor de linha de comando —
nenhum comando exige Discord para rodar. Se você quiser que o digest ganhe
narração em prosa (opcional, nunca decide o ranking), veja a seção `# modelo` do
`.env.example`: `YTR_LLM_BACKEND=claude-cli|codex-cli|anthropic`.

Confira a configuração antes de qualquer coisa:

```bash
python3 -m ytr doctor
```

Ele roda mesmo com `.env` inválido — é quando mais importa — e nomeia cada
variável que falta.

## Uso

### Cadastrar o primeiro canal

Pelo terminal (equivalente a postar o link no canal de entrada do Discord):

```bash
python3 -m ytr resolver https://youtube.com/@algumcanal --salvar
python3 -m ytr canais              # confirma que entrou em curadoria/canais.yaml
```

### Rodar um ciclo de monitoração

```bash
python3 -m ytr ciclo --seco        # ensaio: mostra o que avisaria, sem escrever nada
python3 -m ytr ciclo               # de verdade — lê cadastro pelo Discord e avisa vídeo novo
```

### Ver a recomendação diária

```bash
python3 -m ytr digest --seco       # avalia o pool sem postar nem gastar a cota de LLM do dia
python3 -m ytr digest              # de verdade — posta no canal de aviso
python3 -m ytr sinais              # os 👍/👎 já capturados, por vídeo
```

### Rodar com Docker (produção)

O wrapper `./ytr.sh` resolve `~` do `.env`, seu UID e o caminho do vault — chamar
`docker compose` direto exige exportar essas variáveis à mão.

```bash
./ytr.sh dev doctor           # container efêmero, útil pra testar
./ytr.sh prod up              # sobe o agendador em background
./ytr.sh prod logs            # acompanha
./ytr.sh prod status          # estado e saúde do container
./ytr.sh prod down            # derruba
```

Em produção, `docker/scheduler.sh` chama `ytr ciclo` na cadência do feed
(`YTR_PISO_SEGUNDOS`, 900s por padrão) e `ytr digest` uma vez por dia, no
horário de `YTR_DIGEST_AT`.

### Todos os comandos

| Comando | O que faz |
|---|---|
| `feed` | Lê um feed de canal, de arquivo ou da rede |
| `resolver <url>` | URL de canal → `channel_id`, confirmado pelo RSS. `--salvar` cadastra |
| `canais` | Lista, ativa ou desativa canal monitorado |
| `ciclo` | Um ciclo de monitoração: cadastro pelo Discord + verificação de vídeo novo |
| `perfil` | Perfil de gosto lido do vault (somente leitura). `--resolver` mapeia URL de vídeo → canal |
| `digest` | Recomendação diária dos dois pools + captura de feedback (👍/👎) dos digests anteriores |
| `sinais` | Os 👍/👎 capturados, por vídeo |
| `doctor` | Diagnóstico — roda mesmo com `.env` inválido |

Todo comando que escreve aceita `--seco` onde faz sentido: mostra o que faria,
sem tocar em nada.

## Índice cronológico do desenvolvimento

Cada linha é um commit real, na ordem em que aconteceu. `git log --oneline` é a
fonte de verdade; esta tabela é um resumo legível dela.

| Quando | Commit | O que aconteceu |
|---|---|---|
| 2026-08-24 06:07 | `7264777` | Núcleo do projeto: os 12 módulos de `ytr/` |
| 2026-08-24 06:30 | `80fab1e` | Fases 0 e 1 — CLI, runner e 299 testes |
| 2026-08-24 06:34 | `2c259ca` | Fases 2 e 3 — resolução de canal e ciclo de monitoração, contra canais reais |
| 2026-08-24 13:35 | `426641d` | Fase 5 — Docker (`dev`/`prod`, healthcheck, roda sem root) |
| 2026-08-24 14:00 | `82ea898` | Fase 4 — Discord de ponta a ponta: cadastro e aviso |
| 2026-08-24 18:16 | `12b4b00` | `curadoria/canais.yaml` passa a ser versionado no git |
| 2026-08-24 18:37 | `140dfa6` | Fase 7 — pool de recomendação e digest diário |
| 2026-08-24 19:16 | `9b0e925` | Defeito corrigido: segunda chamada de digest no mesmo dia perdia `message_id` |
| 2026-08-24 19:43 | `a5cc35b` | Curadoria de conteúdo do digest (corta marketing, prioriza conteúdo sério) |
| 2026-08-24 20:34 | `0498c76` | Fase 8 — narração opcional por modelo, guardada contra escolher item |
| 2026-08-25 00:00 | `70821df` | Fase 9 — `doctor` fechado, com os gatilhos do plano como número |
| 2026-08-25 00:15 | `7188ce9` | Dois defeitos achados rodando o agendamento de digest dentro do container |
| 2026-08-25 00:16 | `3808bfb` | README real, substituindo o placeholder da Fase 0 |
| 2026-08-25 00:34 | `719d026` | Freio de circuito contra bloqueio do YouTube por volume de requisição |
| 2026-08-25 00:35 | `98a5ae3` | README menciona o freio de circuito |
| 2026-08-25 07:43 | `fe7c2c2` | Pool 2 — Short suprimido do aviso individual vira candidato do digest |
| 2026-08-25 17:40 | `0c75076` | `LLM_BACKEND=anthropic` implementado de verdade (API direta, com chave) |
| 2026-08-25 17:52 | `9d8bbe9` | `cadastrado_por` sai do `canais.yaml` versionado, vira ledger derivado |
| 2026-08-25 19:49 | `b40d54e` | `docs/INDEX.md`: descrição, instalação, uso, linha do tempo e pendências |

As dez fases do plano original (0–9) fecham em `70821df`. Tudo depois disso é
manutenção e os itens que o próprio README marcava como "em aberto" — todos
resolvidos até `9d8bbe9`.

## Pendências / o que ainda falta implementar

Nada aqui bloqueia o uso normal do radar — são gaps conhecidos, cada um com o
motivo de ainda não ter sido feito. Versão executável, em checklist:
[`docs/CHECKLIST.md`](CHECKLIST.md).

### Documentado, mas sem código por trás

- **Transcrição de vídeo (degrau 3 do resumo, D4 do plano).** `Canal.transcricao`
  existe no esquema (`ytr/canal.py`) e pode ser ligado por canal, mas nenhum
  código lê esse campo — o resumo do aviso hoje só usa os degraus 1 e 2
  (`media:description` do RSS, ou título+canal quando não há descrição;
  `ytr/texto.py:resumo`). Motivo original: a versão de `yt-dlp` disponível na
  máquina de referência não conseguia baixar legenda (§1.5 do plano) — precisa
  ser revalidado antes de implementar.
- **Ledger de quota compartilhado entre projetos (`LLM_BUDGET_DIR`).** Lida em
  `Config`, documentada no `.env.example` como "opcional dos dois lados", mas
  sem nenhuma leitura/escrita real. A contenção de quota com o
  `discord-link-brain` hoje é só por agendamento (`YTR_DIGEST_AT` longe do
  `DLB_SYNC_AT`), como o próprio plano já assume em D5.

### Pool 2 — só metade do que o plano descreve

Hoje o Pool 2 (`ytr/pool.py:buscar_pool2`) só recupera **Shorts** suprimidos de
canal monitorado. A outra metade do D7 — vídeo de canal monitorado com **baixa
afinidade** — não existe: todo vídeo novo não-Short de canal monitorado ainda é
avisado individualmente, sem nenhum filtro de afinidade. Implementar essa parte
exige decidir o que "baixa afinidade" significa no momento do aviso (o ciclo não
tem, hoje, acesso ao perfil de gosto — só o `digest` tem).

### Decisão deliberada, com gatilho numérico (não é bug — o gatilho é o que falta)

- **SQLite para o estado.** Recusado enquanto `canais.yaml` tiver menos de 200
  canais e a leitura do perfil de gosto ficar sob 150ms — os dois números
  aparecem no `doctor` (linhas `monitorados` e `corpus`). Quando baterem o
  limiar, aí sim vale implementar.
- **Embeddings para o perfil de gosto.** Recusado enquanto o corpus do vault
  couber no orçamento de prompt (`YTR_CORPUS_MAX_CHARS`, também mostrado pelo
  `doctor`). Hoje ele cabe com folga.

### Limitação de ambiente, não de código

- **`yt-dlp` como fallback de resolução de canal (`--yt-dlp` em `resolver`)**
  está implementado (`ytr/canal.py:_yt_dlp_channel_id`) e testado (a saída vazia
  é recusada, não aceita como sucesso), mas desligado por padrão: a versão de
  `yt-dlp` medida contra o YouTube atual devolvia saída vazia com código 0 —
  precisa de uma versão mais nova instalada antes de valer a pena religar.
