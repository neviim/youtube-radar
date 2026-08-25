# Checklist de pendências

Lista executável do que falta, derivada de `docs/INDEX.md` → "Pendências / o que
ainda falta implementar". Cada item tem o motivo e os arquivos envolvidos — quem
for executar não precisa reabrir o plano original pra entender o contexto. Ordem
sugerida: do mais isolado/barato pro mais estrutural.

Concluído nesta rodada, fora da lista: **sinal de vídeo postado agora soma
afinidade de canal** (`ytr/gosto.py:carregar`, tratando `{"tipo": "postado"}`).

## 1. Documentado, mas sem código por trás

- [ ] **Decidir o destino de `YTR_RESUMO_LLM`.** Hoje é lido em `Config`
      (`ytr/config.py`) e não consultado por nenhum código — ligá-lo não faz
      nada. Duas saídas: implementar um resumo por vídeo via modelo (custo de
      quota rodando de 15 em 15 min, por isso o plano recomendava contra — D5),
      ou remover a variável morta do `.env.example`/`Config` até haver decisão
      de fazer de verdade.
- [ ] **Decidir o destino de `LLM_BUDGET_DIR`.** Mesma situação: lido, nunca
      usado. Ou implementa o ledger de quota compartilhado com o
      `discord-link-brain` (dois `.state/` diferentes, precisa desenhar onde o
      lock mora — D5 do plano já aponta que isso exige um terceiro lugar que
      hoje não existe), ou remove a variável até isso ser prioridade.
- [ ] **Revalidar `yt-dlp` e decidir a transcrição (degrau 3 do resumo, D4).**
      Pré-requisito: atualizar o `yt-dlp` instalado e medir de novo contra o
      YouTube atual (a versão de referência falhava com saída vazia e código
      0 — §1.5 do plano). Só depois disso vale ligar `Canal.transcricao`
      (`ytr/canal.py`) a algum código — hoje o campo existe no esquema e não é
      lido em lugar nenhum.

## 2. Pool 2 — a metade que falta

- [ ] **Implementar a supressão por baixa afinidade em canal monitorado.**
      Hoje `ytr/ciclo.py:novidades` só filtra Short; todo vídeo não-Short de
      canal monitorado é avisado, sem checar afinidade. Decisão de design
      necessária antes de codar: o `ciclo` roda de 15 em 15 min e hoje não
      carrega o perfil de gosto (`ytr.gosto`) — precisa decidir se vale o
      custo de ler o vault a cada ciclo, cachear o perfil entre ciclos, ou
      mover esse corte para o `digest` em vez do `ciclo`.
      Ponto de entrada simétrico ao que já existe para Shorts:
      `ledger.registrar_candidato_pool2` (mesma persistência), consumido por
      `ytr/pool.py:buscar_pool2`.

## 3. Threshold-gated — só monitorar, não implementar ainda

- [ ] **SQLite para o estado** — gatilho: `canais.yaml` passar de 200 canais
      *ou* leitura do perfil de gosto passar de 150ms. Os dois números já
      aparecem no `doctor` (linhas `monitorados` e `corpus`); não fazer nada
      até um deles estourar.
- [ ] **Embeddings para o perfil de gosto** — gatilho: corpus do vault encostar
      no orçamento de prompt (`YTR_CORPUS_MAX_CHARS`, também no `doctor`). Não
      fazer nada até o `doctor` mostrar isso perto do limite.

## 4. Fora do código

- [ ] **Decidir o fluxo de branch/PR.** Não existe branch `main` neste repo
      (nem local, nem no GitHub) — só `feat/v1`, que é também a branch padrão
      hoje no remoto. Se a intenção é abrir PR em algum momento, criar `main`
      e mover o fluxo de push para PR + merge antes que `feat/v1` cresça mais.
