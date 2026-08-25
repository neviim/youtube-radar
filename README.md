# youtube-radar

Monitora canais do YouTube por RSS (sem chave de API), avisa no Discord quando
sai vídeo novo, e recomenda vídeos de canais que você já demonstrou gostar —
a partir do que você já salvou no Obsidian, não de um algoritmo de terceiro.

Cadastro de canal também é pelo Discord: poste o link de um canal no canal de
entrada e o bot resolve, confirma e passa a monitorar sozinho.

Plano completo, decisões e critérios de pronto de cada fase:
`~/.buzz/.scratch/PLANO_CLAUDE_youtube_radar.md`.

## O que já funciona

As dez fases do plano estão fechadas e verificadas contra infraestrutura
real — rede, Discord, Docker, vault do Obsidian, e os binários `claude`/`codex`
de verdade, não só teste com dublê. Em ordem:

0. Esqueleto, guardas de segredo, `.env.example`
1. Leitor de RSS
2. Resolução de canal (link → `channel_id`, confirmado pelo feed)
3. Ciclo de monitoração — `at-least-once`, avisa-depois-marca, recuo por falha
4. Cadastro e aviso pelo Discord
5. Docker (`dev`/`prod`, healthcheck, roda como seu usuário, não root)
6. Perfil de gosto, lido do vault (somente leitura)
7. Pool de recomendação e digest diário
8. Narração opcional por modelo (`claude`/`codex` local, ou API direta da
   Anthropic com `ANTHROPIC_API_KEY`), nunca decide
9. `doctor` — diagnóstico e os números que tornam os gatilhos do plano conferíveis

## Configurar

```
cp .env.example .env
$EDITOR .env
```

O arquivo já vem comentado, variável por variável. As que **têm** de ser
preenchidas para o bot falar no Discord: `DISCORD_TOKEN`, `YTR_CANAL_ENTRADA`,
`YTR_CANAL_AVISO`. Sem elas o radar continua funcionando só como monitor —
nenhum comando exige Discord para rodar.

## Rodar

**Direto, com Python 3.12+:**

```
pip install -r requirements.txt
python3 -m ytr doctor          # confere a configuração antes de tudo
python3 -m ytr resolver https://youtube.com/@algumcanal --salvar
python3 -m ytr ciclo --seco    # ensaio: mostra o que avisaria, não escreve nada
python3 -m ytr ciclo           # de verdade
```

**Com Docker**, via o wrapper `./ytr.sh` (ele resolve `~` do `.env`, seu UID e
o caminho do vault — chamar `docker compose` direto exige exportar essas
variáveis à mão):

```
./ytr.sh dev doctor          # container efêmero, útil pra testar
./ytr.sh prod up             # sobe o agendador em background
./ytr.sh prod logs           # acompanha
./ytr.sh prod status         # estado e saúde do container
./ytr.sh prod down           # derruba
```

Em produção o agendador (`docker/scheduler.sh`) chama `ytr ciclo` na cadência
do feed (`YTR_PISO_SEGUNDOS`, 900s por padrão) e `ytr digest` uma vez por dia,
no horário de `YTR_DIGEST_AT`.

## Comandos

| Comando | O que faz |
|---|---|
| `feed` | Lê um feed de canal, de arquivo ou da rede |
| `resolver <url>` | URL de canal → `channel_id`, confirmado pelo RSS. `--salvar` cadastra |
| `canais` | Lista, ativa ou desativa canal monitorado |
| `ciclo` | Um ciclo de monitoração: cadastro pelo Discord + verificação de vídeo novo |
| `perfil` | Perfil de gosto lido do vault (somente leitura). `--resolver` mapeia URL de vídeo → canal |
| `digest` | Recomendação diária dos dois pools (canais do vault + Shorts suprimidos de canal monitorado) + captura de feedback (👍/👎) dos digests anteriores |
| `sinais` | Os 👍/👎 capturados, por vídeo |
| `doctor` | Diagnóstico — roda mesmo com `.env` inválido |

Todo comando que escreve aceita `--seco` onde faz sentido: mostra o que faria,
sem tocar em nada.

## Se o YouTube começar a responder 404 pra tudo

Já aconteceu (medido): volume alto de requisição num período curto faz o
YouTube bloquear o IP por um tempo, inclusive para páginas nunca consultadas
antes. `ytr/limitador.py` é o freio de circuito: depois de dois ciclos
seguidos com todos os canais falhando, ele para de tentar sozinho, e volta a
tentar (uma sonda) quando o cooldown passa — sem perder nenhum canal no
caminho. `doctor` mostra o estado (`limitador ABERTO/fechado`).

## Testar

```
./testar.py                    # suíte inteira, módulo por módulo
./testar.py --um-processo      # o mais rápido
./testar.py --help             # todos os modos
```

360+ testes, sem rede nem Docker — os módulos que tocam rede ou Discord usam
dublê. O que foi verificado contra infraestrutura real está documentado nas
mensagens de commit de cada fase.

## Estrutura

```
ytr/            código do radar (12+ módulos)
tests/          suíte de testes, um arquivo por módulo
docker/         Dockerfile, entrypoint, scheduler
curadoria/      canais.yaml — a lista de canais monitorados, editável à mão
docs/           VENDORADO.md (o que foi copiado do discord-link-brain, e por quê)
testar.py       runner de testes com saída legível
ytr.sh          wrapper de Docker
```
