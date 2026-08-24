# Código vendorado

Este projeto copia, em vez de depender como pacote, um punhado de arquivos do
`discord-link-brain` — decisão de D9 do plano (`~/.buzz/.scratch/PLANO_CLAUDE_youtube_radar.md`):
o segundo consumidor de uma semana de idade não paga o preço de um terceiro repo,
versionamento e passo de release. A regra tem uma linha:

**Conserta em cima (no projeto de origem), depois re-vendora aqui.** Nunca o
contrário — um patch só neste repo diverge sem aviso, e a próxima re-vendoragem
apaga o conserto.

Não há teste de drift entre os dois repos: ele não rodaria sem o outro repo
presente, e teste que não roda é comentário. O que existe em vez disso é um
teste de **contrato** por arquivo vendorado (`tests/test_env_io.py`,
`tests/test_discord_client.py`) — afirma o que este projeto depende de verdade,
não a suíte inteira de origem, para uma mudança de comportamento na re-vendoragem
quebrar aqui em vez de aparecer como defeito em produção.

Promove a pacote de verdade quando aparecer o **terceiro** consumidor, ou quando
o mesmo bug tiver de ser consertado duas vezes.

## O que está vendorado

| Arquivo aqui | Origem | Commit | Adaptação |
|---|---|---|---|
| `ytr/env_io.py` | `discord-link-brain:dlb/env_io.py` | `4674e72c1892e944237937bf86865e57d445aa18` | verbatim |
| `ytr/discord_client.py` | `discord-link-brain:dlb/discord_client.py` | `4674e72c1892e944237937bf86865e57d445aa18` | só `USER_AGENT` |

## O que foi deliberadamente reescrito, não vendorado

- **Idioma de estado** (`ytr/state.py`): escrita atômica e um-escritor-por-arquivo
  seguem o mesmo raciocínio do `dlb/state.py`, mas o estado em si é outro —
  adaptado, não copiado.
- **Busca lexical** (`ytr/lexico.py`): a Fase 6 do plano original propunha reusar
  `dlb/busca.py`, mas D9 nunca listou esse arquivo entre os vendorados (achado do
  Codex na revisão). Reescrito do zero em vez de vendorado com o débito não
  documentado.
- **`testar.py` e este padrão de saída de teste**: o documento de origem
  (`discord-link-brain:docs/PADRAO_SAIDA_DE_TESTES.md`) diz que existe para ser
  reusado em outro projeto — o **método** foi portado, a invocação e a
  interpretação são deste projeto.

## O que foi recusado, e por quê

`dlb/vault.py`, `dlb/painel.py`, `migrar.py`, `sintese.py`, `ressurgir.py`,
`temas.py`, `acervo.py` e `frontmatter.py` — todos específicos de escrever no
vault, que não é o trabalho deste sistema (D9). O radar é somente-leitura contra
o vault, de propósito.
