"""Linha de comando do radar: `python3 -m ytr <comando>`.

Este módulo é a camada que **prova** que o resto funciona. Sem ele, todo critério de
pronto do plano escrito como `python3 -m ytr …` é uma frase que ninguém pode rodar — e
foi exatamente o que faltou numa rodada anterior: doze módulos que importavam sem erro
e nenhuma forma de exercitá-los de fora.

Três posturas, e as três são deliberadas:

1. **Erro de configuração é uma frase, não um traceback.** `ConfigError`, `CanalError`,
   `EstadoNaoGravavel` e `TravaOcupada` são capturados aqui, no topo, e viram uma linha
   em `stderr` com código de saída próprio. Ninguém depura `.env` lendo pilha.
2. **`main()` recebe `argv` e devolve `int`.** Não chama `sys.exit` por dentro. É o que
   deixa o teste rodar um comando inteiro em processo, sem `subprocess` — inclusive o
   caso de `.state` não-gravável, que precisa provar que o ciclo recusa **antes** de
   qualquer POST.
3. **Todo comando valida as marcas.** `validar_marcas()` roda em `_cfg()`, não dentro de
   um comando específico: o guarda que impede o radar de reusar um emoji do
   `discord-link-brain` não pode depender de qual subcomando alguém digitou.

Códigos de saída, e cada um significa uma coisa:

    0  deu certo
    1  não achei / uso errado (URL que não é canal, apelido inexistente)
    2  configuração ou estado impedem continuar (inclui `.state` não-gravável)
    3  outro processo detém a trava
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import cadastro as mod_cadastro
from . import canal as mod_canal
from . import config as mod_config
from . import feed as mod_feed
from . import gosto as mod_gosto
from . import ledger as mod_ledger
from . import trava as mod_trava
from .canal import Canais, CanalError, classificar, handle_por_oembed, resolver
from .ciclo import PostagemBloqueada, PublicadorDiscord, rodar
from .config import Config, ConfigError
from .discord_client import DiscordClient
from .rede import Cliente
from .state import Estado, EstadoNaoGravavel, Saude, preflight
from .trava import TravaOcupada

# O `canais.yaml` não mora no `.state`: ele é a curadoria dele, versionável e editável à
# mão, enquanto `.state/` é derivado e descartável. Misturar os dois faria um `rm -rf
# .state` para limpar cursor apagar também a lista de canais.
CANAIS_PADRAO = "canais.yaml"

# Orçamento de leitura do perfil, em milissegundos. Vem do plano: acima disto, a leitura
# do vault deixa de caber dentro de um ciclo e a decisão de reler a cada vez muda.
ORCAMENTO_LEITURA_MS = 150.0

# Teto de canais monitorados. Não é limite técnico — é o ponto em que a banda por dia
# deixa de ser desprezível e o número tem de voltar para a mesa.
TETO_MONITORADOS = 200


def _erro(mensagem: str) -> None:
    print(f"erro: {mensagem}", file=sys.stderr)


def _caminho_canais() -> Path:
    return Path(os.environ.get("YTR_CANAIS", "").strip() or CANAIS_PADRAO)


def _cfg() -> Config:
    """Config do ambiente, já com o guarda de marcas aplicado.

    `validar_marcas()` aqui e não em cada comando: ele impede que o radar ponha numa
    mensagem um emoji que o `discord-link-brain` usa como "já resolvido" — os dois
    rodam com o mesmo bot, e o campo `me` das reações não distingue processo. Um guarda
    que só vale em alguns subcomandos não é guarda.
    """
    mod_config.load_dotenv()
    cfg = Config.from_env()
    cfg.validar_marcas()
    return cfg


def _discord_cliente(cfg: Config) -> DiscordClient | None:
    """`None` sem `DISCORD_TOKEN` — o radar continua funcionando só como monitor,
    exatamente como antes da Fase 4."""
    return DiscordClient(f"Bot {cfg.discord_token}") if cfg.discord_token else None


# ------------------------------------------------------------------------ feed


def cmd_feed(args) -> int:
    """Lê um feed — de arquivo ou da rede — e lista as entradas.

    `--arquivo` existe para o caminho offline ser o **padrão de teste**, não uma
    concessão: o fixture é o XML real de 26 KB, e exercitar o parser não deve depender
    de o YouTube estar de pé.
    """
    if args.arquivo:
        bruto = Path(args.arquivo).read_text(encoding="utf-8")
        origem = args.arquivo
        bytes_gastos = 0
    else:
        if not args.channel_id:
            _erro("dê `--arquivo <xml>` ou um `channel_id` para buscar na rede.")
            return 1
        channel_id = mod_canal.ChannelId(args.channel_id)
        cliente = Cliente()
        resposta = cliente.get(channel_id.url_feed)
        if not resposta.ok:
            _erro(f"o RSS de {channel_id} respondeu HTTP {resposta.status}.")
            return 1
        bruto, origem, bytes_gastos = resposta.texto, channel_id.url_feed, resposta.bytes_no_fio

    feed = mod_feed.parse(bruto)

    if args.json:
        print(json.dumps(
            {"channel_id": feed.channel_id, "titulo": feed.titulo,
             "videos": [vars(v) for v in feed.videos]},
            ensure_ascii=False, indent=2,
        ))
        return 0

    largura = max((len(v.video_id) for v in feed.videos), default=11)
    for numero, video in enumerate(feed.videos, 1):
        marca = "SHORT" if video.is_short else "     "
        print(f"{numero:>3} {marca} {video.video_id:<{largura}}  {video.titulo}")

    shorts = sum(1 for v in feed.videos if v.is_short)
    print(
        f"\n{len(feed.videos)} entradas · {shorts} Shorts · canal {feed.channel_id or '?'} "
        f"({feed.titulo or 'sem título'}) · {bytes_gastos} bytes no fio · {origem}"
    )
    return 0


# -------------------------------------------------------------------- resolver


def cmd_resolver(args) -> int:
    """URL → `channel_id`, **confirmado pelo RSS**, e opcionalmente persistido.

    Imprime só o id na saída padrão. O resto vai para `stderr`, para o comando servir
    em `$(…)` sem que alguém tenha de filtrar linha de contexto.
    """
    alvo = classificar(args.url)
    if alvo.tipo != "canal":
        _erro(
            f"{args.url!r} não é link de canal (classifiquei como {alvo.tipo!r}). "
            "Link de vídeo registra sinal de gosto; só link de canal cadastra."
        )
        return 1

    cliente = Cliente()
    achado = resolver(alvo, cliente, usar_yt_dlp=args.yt_dlp)
    print(achado.channel_id)
    print(
        f"  {achado.nome or 'sem título'} · fonte {achado.fonte} · "
        f"{len(achado.videos_atuais)} vídeos no feed · {achado.bytes_gastos} bytes",
        file=sys.stderr,
    )

    if not args.salvar:
        return 0

    cfg = _cfg()
    with mod_trava.travar(cfg.state_dir):
        canais = Canais(_caminho_canais())
        if achado.channel_id in canais:
            print(f"  já estava cadastrado — não dupliquei.", file=sys.stderr)
            return 0
        canais.adicionar(
            achado.channel_id,
            handle=achado.handle or alvo.handle,
            nome=achado.nome,
            url_original=args.url,
        )
        canais.salvar()

        # Semeia os 15 ids que a confirmação já trouxe. Sem isto, o primeiro ciclo do
        # canal recém-cadastrado avisaria o catálogo inteiro — e a mesma resposta HTTP
        # que provou que o canal existe é a que evita isso, de graça.
        estado = Estado(cfg.state_dir)
        atual = estado.carregar(str(achado.channel_id))
        for video_id in achado.videos_atuais:
            atual.lembrar(video_id, cfg.lembrar_ids)
        atual.semeado = True
        estado.salvar(atual)

    print(
        f"  cadastrado, com {len(achado.videos_atuais)} ids semeados "
        f"(aviso a partir do próximo vídeo).",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------- canais


def cmd_canais(args) -> int:
    cfg = _cfg()
    canais = Canais(_caminho_canais())

    if args.acao in ("desativar", "ativar"):
        if not args.alvo:
            _erro(f"`canais {args.acao}` precisa de um id, handle ou nome.")
            return 1
        # A trava é o que impede este comando de colidir com o ciclo escrevendo o mesmo
        # YAML. O desenho de arquivo não resolve aqui: são dois comandos, um arquivo.
        with mod_trava.travar(cfg.state_dir):
            canais = Canais(_caminho_canais())
            achado = canais.por_apelido(args.alvo)
            if achado is None:
                _erro(f"não achei canal por {args.alvo!r} em {_caminho_canais()}.")
                return 1
            achado.ativo = args.acao == "ativar"
            canais.salvar()
        print(f"{achado.handle or achado.channel_id}: ativo={achado.ativo}")
        return 0

    todos = canais.todos()
    if not todos:
        print(f"nenhum canal em {_caminho_canais()} — cadastre com `resolver <url> --salvar`.")
        return 0

    estado = Estado(cfg.state_dir)
    largura = max(len(c.handle or c.channel_id) for c in todos)
    for canal in todos:
        atual = estado.carregar(canal.channel_id)
        marca = " " if canal.ativo else "×"
        print(
            f"{marca} {(canal.handle or canal.channel_id):<{largura}}  {canal.channel_id}  "
            f"{len(atual.avisados):>3} vistos  "
            f"{'semeado' if atual.semeado else 'a semear'}"
            + (f"  {atual.falhas} falha(s)" if atual.falhas else "")
        )
    print(f"\n{len(canais.ativos())} ativo(s) de {len(todos)} · {_caminho_canais()}")
    return 0


# ----------------------------------------------------------------------- ciclo


def cmd_ciclo(args) -> int:
    """Um ciclo de monitoração.

    A ordem aqui é a garantia: **preflight antes da trava, e trava antes do ciclo.**
    O preflight prova que `.state` aceita escrita *antes* de qualquer POST — sem ele,
    um `.state` read-only deixa o sistema postar com sucesso e falhar ao marcar, a cada
    15 minutos, para sempre.

    O cadastro (Fase 4, `ytr.cadastro`) roda **antes** do "nenhum canal ativo": é o
    próprio cadastro que pode cadastrar o primeiro canal deste ciclo.
    """
    cfg = _cfg()
    canais = Canais(_caminho_canais())

    preflight(cfg.state_dir)

    with mod_trava.travar(cfg.state_dir):
        estado = Estado(cfg.state_dir)
        cliente = Cliente()
        discord = _discord_cliente(cfg)

        relatorio_cadastro = mod_cadastro.processar(
            cfg, canais, estado, cliente, discord, seco=args.seco
        )
        for linha in relatorio_cadastro.linhas:
            print(linha)
        for erro in relatorio_cadastro.erros:
            print(f"⛔ {erro}", file=sys.stderr)

        if not canais.ativos():
            print(f"nenhum canal ativo em {_caminho_canais()} — nada a fazer.")
            return 0

        publicador = (
            PublicadorDiscord(discord, cfg.canal_aviso)
            if discord is not None and cfg.canal_aviso
            else None
        )
        relatorio = rodar(cfg, canais, estado, cliente, publicador=publicador, seco=args.seco)

    for linha in relatorio.linhas:
        print(linha)
    for erro in relatorio.erros:
        print(f"⛔ {erro}", file=sys.stderr)
    print(relatorio.resumo())
    return 1 if (relatorio.erros or relatorio_cadastro.erros) else 0


# ---------------------------------------------------------------------- perfil


def _mapa_canal(cfg) -> tuple[dict, Path]:
    """Cache `url_do_video → handle`, resolvido por oEmbed uma vez e guardado.

    Existe porque o vault guarda a URL do **vídeo** e a afinidade é por **canal**, e
    resolver 72 URLs custa ~27 s de rede — que não cabem dentro de um ciclo de 15
    minutos, nem devem ser pagos a cada `perfil`.
    """
    caminho = Path(cfg.state_dir) / "canais_por_url.json"
    if not caminho.is_file():
        return {}, caminho
    try:
        return json.loads(caminho.read_text(encoding="utf-8")), caminho
    except (json.JSONDecodeError, OSError):
        return {}, caminho


def cmd_perfil(args) -> int:
    cfg = _cfg()
    cfg.exigir_vault()
    mapa, caminho_mapa = _mapa_canal(cfg)

    if args.resolver:
        cliente = Cliente()
        parcial = mod_gosto.carregar(cfg, sinais=None, mapa_canal=mapa)
        pendentes = [u for u in sorted(parcial.urls_conhecidas) if u not in mapa]
        print(f"resolvendo {len(pendentes)} URL(s) por oEmbed…", file=sys.stderr)
        for url in pendentes:
            handle = handle_por_oembed(url, cliente)
            if handle:
                mapa[url] = handle
        caminho_mapa.parent.mkdir(parents=True, exist_ok=True)
        caminho_mapa.write_text(
            json.dumps(mapa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"  {len(mapa)} resolvida(s) · {cliente.bytes_gastos} bytes · {caminho_mapa}",
            file=sys.stderr,
        )

    perfil = mod_gosto.carregar(cfg, sinais=mod_ledger.sinais(cfg.state_dir), mapa_canal=mapa)

    ranking = perfil.afinidade_canal.most_common()
    if ranking:
        largura = max(len(h) for h, _ in ranking)
        for posicao, (handle, quantos) in enumerate(ranking, 1):
            print(f"{posicao:>3}. @{handle:<{largura}}  {quantos}")
    else:
        print(
            "nenhuma afinidade de canal: o vault guarda URL de vídeo, e o mapa "
            f"`{caminho_mapa.name}` está vazio. Rode `perfil --resolver` uma vez."
        )

    tags = perfil.afinidade_tag.most_common(12)
    if tags:
        print("\ntags:", " · ".join(f"{t} {n}" for t, n in tags))

    # Os dois números que decidem quando este desenho deixa de servir, impressos
    # **contados** contra o limiar — em vez de repetidos de uma extrapolação do plano.
    folga_corpus = "ok" if perfil.corpus_chars <= cfg.corpus_max_chars else "ESTOUROU"
    folga_leitura = "ok" if perfil.leitura_ms <= ORCAMENTO_LEITURA_MS else "ESTOUROU"
    print(
        f"\n{len(perfil.notas)} notas · {len(ranking)} canais · "
        f"corpus {perfil.corpus_chars} de {cfg.corpus_max_chars} chars ({folga_corpus}) · "
        f"leitura {perfil.leitura_ms:.1f} ms de {ORCAMENTO_LEITURA_MS:.0f} ({folga_leitura})"
    )
    return 0


# ---------------------------------------------------------------------- sinais


def cmd_sinais(args) -> int:
    cfg = _cfg()
    registros = mod_ledger.sinais(cfg.state_dir)
    if not registros:
        print(f"nenhum sinal em {mod_ledger.caminho_sinais(cfg.state_dir)}.")
        return 0
    for sinal in registros[-args.ultimos:]:
        print(
            f"{sinal.get('em', '?')}  {sinal.get('reacao', '?')}  "
            f"{sinal.get('video_id', '?'):<11}  {sinal.get('handle') or sinal.get('channel_id') or '?'}"
        )
    positivos = sum(1 for s in registros if s.get("reacao") == "👍")
    negativos = sum(1 for s in registros if s.get("reacao") == "👎")
    print(f"\n{len(registros)} sinal(is) · {positivos} 👍 · {negativos} 👎")
    return 0


# ---------------------------------------------------------------------- doctor


def cmd_doctor(args) -> int:
    """Diagnóstico. **Roda mesmo com `.env` inválido** — é quando ele mais importa.

    Um `doctor` que morre pela mesma razão que o sistema morreu não diagnostica nada, e
    é o modo de falha mais fácil de escrever sem perceber: qualquer `Config.from_env()`
    no topo faz o comando abortar exatamente no caso que ele existe para explicar.

    Cada linha imprime **o número medido ao lado do seu limiar**, não um rótulo verde.
    """
    problemas: list[str] = []

    try:
        mod_config.load_dotenv()
    except OSError as erro:
        problemas.append(f"não consegui ler o `.env`: {erro}")

    cfg = None
    try:
        cfg = Config.from_env()
    except ConfigError as erro:
        problemas.append(str(erro))

    print(f"config          {'lida' if cfg is not None else 'INVÁLIDA — veja abaixo'}")

    # As marcas ganham **linha própria e impressa**. A primeira versão disto guardava o
    # erro em `problemas` e só imprimia a lista no ramo de `cfg is None` — então um
    # `YTR_EMOJI_VIDEO=✅` fazia o doctor contar "5 problemas" e nomear quatro,
    # engolindo em silêncio justamente a colisão que faz um link parar de ser arquivado
    # em silêncio. Diagnóstico que esconde um achado é pior que diagnóstico nenhum.
    if cfg is not None:
        try:
            cfg.validar_marcas()
            print(f"marcas          ok · {' '.join(m for m in cfg.marcas if m)}")
        except ConfigError as erro:
            print(f"marcas          COLIDEM — {erro}")
            problemas.append(str(erro))

    if cfg is None:
        # Sem config não há limiar para comparar, mas o diagnóstico já tem valor: ele
        # nomeia a variável que impediu de subir. Sair 2 aqui é o contrato.
        for problema in problemas:
            print(f"  ⛔ {problema}", file=sys.stderr)
        return 2

    for nome, exigir in (
        ("DISCORD_TOKEN", cfg.exigir_discord),
        ("YTR_CANAL_AVISO", cfg.exigir_canal_aviso),
        ("YTR_CANAL_ENTRADA", cfg.exigir_canal_entrada),
        ("OBSIDIAN_VAULT", cfg.exigir_vault),
    ):
        try:
            exigir()
            print(f"{nome:<15} ok")
        except ConfigError as erro:
            print(f"{nome:<15} FALTA — {erro}")
            problemas.append(str(erro))

    canais = None
    try:
        canais = Canais(_caminho_canais())
    except CanalError as erro:
        problemas.append(str(erro))
        print(f"canais.yaml     INVÁLIDO — {erro}")

    if canais is not None:
        ativos = len(canais.ativos())
        print(f"monitorados     {ativos} de {TETO_MONITORADOS} (teto)")

    estado = Estado(cfg.state_dir)
    todos = estado.todos()
    bytes_ciclo = sum(e.bytes_ultimo_ciclo for e in todos)
    # 96 ciclos/dia é o que um piso de 900 s produz. Multiplicar o último ciclo medido
    # por 96 é extrapolação, e está dita como tal em vez de impressa como "banda/dia".
    print(
        f"banda           {bytes_ciclo} bytes no último ciclo · "
        f"~{bytes_ciclo * 96 / 1_000_000:.1f} MB/dia se todo ciclo custar isto "
        f"(extrapolação, não medição)"
    )

    piores = sorted((e for e in todos if e.falhas), key=lambda e: -e.falhas)[:5]
    print(f"falhas          {len(piores)} canal(is) com falha consecutiva")
    for pior in piores:
        print(f"  · {pior.channel_id}  {pior.falhas} falha(s)  {pior.ultimo_erro[:70]}")

    saude = Saude.carregar(cfg.state_dir)
    print(f"heartbeat       {saude.heartbeat or 'nunca'} (tolerância {cfg.health_tolerance}s)")
    print(f"ciclos ruins    {saude.ciclos_com_falha_total} seguidos com falha total")
    print(f"llm             backend {cfg.llm_backend} · teto {cfg.llm_max_dia}/dia")

    if saude.postagem_bloqueada:
        print(f"postagem        BLOQUEADA — {saude.motivo}")
        problemas.append(saude.motivo)
    else:
        print(f"postagem        liberada · POST_ENABLED={int(cfg.post_enabled)}")

    try:
        preflight(cfg.state_dir)
        print(f"{'.state':<15} gravável ({cfg.state_dir})")
    except EstadoNaoGravavel as erro:
        print(f"{'.state':<15} NÃO GRAVÁVEL — {erro}")
        problemas.append(str(erro))

    if problemas:
        print(f"\n{len(problemas)} problema(s). O radar não vai avisar direito assim.")
        return 2
    print("\nnenhum problema.")
    return 0


# ------------------------------------------------------------------------ main


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m ytr",
        description="youtube-radar — monitora canais do YouTube e recomenda a partir do que já é seu.",
        epilog=(
            "exemplos:\n"
            "  python3 -m ytr feed --arquivo tests/fixtures/feed.xml\n"
            "  python3 -m ytr resolver https://youtube.com/@algumcanal\n"
            "  python3 -m ytr resolver https://youtube.com/@algumcanal --salvar\n"
            "  python3 -m ytr canais\n"
            "  python3 -m ytr canais desativar @algumcanal\n"
            "  python3 -m ytr ciclo --seco\n"
            "  python3 -m ytr perfil --resolver\n"
            "  python3 -m ytr sinais\n"
            "  python3 -m ytr doctor\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = p.add_subparsers(dest="comando")

    f = subs.add_parser("feed", help="lê um feed de canal, de arquivo ou da rede")
    f.add_argument("channel_id", nargs="?", help="UC… para buscar na rede")
    f.add_argument("--arquivo", help="XML local em vez de rede (o caminho de teste)")
    f.add_argument("--json", action="store_true", help="saída estruturada")
    f.set_defaults(func=cmd_feed)

    r = subs.add_parser("resolver", help="URL de canal → channel_id, confirmado pelo RSS")
    r.add_argument("url")
    r.add_argument("--salvar", action="store_true", help="persiste em canais.yaml e semeia")
    r.add_argument("--yt-dlp", action="store_true", dest="yt_dlp",
                   help="tenta o yt-dlp se a página não trouxer o id")
    r.set_defaults(func=cmd_resolver)

    c = subs.add_parser("canais", help="lista, desativa ou reativa canal monitorado")
    c.add_argument("acao", nargs="?", default="listar",
                   choices=("listar", "desativar", "ativar"))
    c.add_argument("alvo", nargs="?", help="id, handle (com ou sem @) ou nome")
    c.set_defaults(func=cmd_canais)

    ci = subs.add_parser("ciclo", help="um ciclo de monitoração")
    ci.add_argument("--seco", action="store_true", help="não posta: imprime o que avisaria")
    ci.set_defaults(func=cmd_ciclo)

    pe = subs.add_parser("perfil", help="perfil de gosto lido do vault (somente leitura)")
    pe.add_argument("--resolver", action="store_true",
                    help="resolve as URLs do vault por oEmbed e guarda o mapa")
    pe.set_defaults(func=cmd_perfil)

    si = subs.add_parser("sinais", help="os 👍/👎 capturados, por vídeo")
    si.add_argument("--ultimos", type=int, default=20, metavar="N")
    si.set_defaults(func=cmd_sinais)

    do = subs.add_parser("doctor", help="diagnóstico — roda mesmo com .env inválido")
    do.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)

    # Sem subcomando, ajuda e **código 2**. Não 0: um lançador que roda `python3 -m ytr`
    # sem argumento por engano não pode receber "deu tudo certo" de volta.
    if not getattr(args, "comando", None):
        parser.print_help()
        return 2

    try:
        return args.func(args)
    except ConfigError as erro:
        _erro(str(erro))
        return 2
    except EstadoNaoGravavel as erro:
        _erro(str(erro))
        return 2
    except PostagemBloqueada as erro:
        _erro(str(erro))
        return 2
    except TravaOcupada as erro:
        _erro(str(erro))
        return 3
    except CanalError as erro:
        _erro(str(erro))
        return 1
    except mod_feed.FeedInvalido as erro:
        _erro(str(erro))
        return 1
    except FileNotFoundError as erro:
        _erro(f"não achei o arquivo: {erro}")
        return 1
    except BrokenPipeError:
        # `python3 -m ytr feed … | head` fecha o cano no meio. Sem este ramo o Python
        # imprime "Exception ignored" no fim, que parece defeito e não é.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
