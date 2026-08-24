"""Cliente HTTP do radar. Um lugar só, para a banda ser contável e o gzip obrigatório.

Duas coisas que este módulo garante e que não podem ficar espalhadas:

1. **`Accept-Encoding: gzip` em toda requisição.** Medido: o feed cai de 26.136 para
   4.932 bytes, e a página de canal de ~1,6 MB para ~150 KB. Esquecer o header numa
   chamada é uma regressão de 5× que nada detectaria.
2. **A banda é contada no lugar onde é gasta.** O ciclo imprime bytes reais, e o
   `doctor` mostra a média medida — em vez de repetir uma extrapolação como se fosse
   medição.

O `User-Agent` não leva perfil de ninguém. Isso não é zelo: um `User-Agent` com
`github.com/<pessoa>` sai pela rede em toda requisição, e apagar o repo depois não
desfaz — já viajou.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

UA_BOT = "youtube-radar/0.1 (+https://www.youtube.com/feeds)"

# A página de canal só devolve o `externalId` para um agente que pareça navegador.
# É o único lugar onde fingimos ser um: o RSS e o oEmbed respondem ao UA do bot.
UA_NAVEGADOR = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class RedeError(RuntimeError):
    pass


@dataclass
class Resposta:
    url: str
    status: int
    texto: str
    cabecalhos: dict = field(default_factory=dict)
    bytes_no_fio: int = 0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Cliente:
    """Sessão com contador de banda. Um por processo."""

    def __init__(self, timeout: float = 20.0):
        self.session = requests.Session()
        self.timeout = timeout
        self.bytes_gastos = 0
        self.requisicoes = 0

    def get(self, url: str, navegador: bool = False) -> Resposta:
        cabecalhos = {
            "User-Agent": UA_NAVEGADOR if navegador else UA_BOT,
            "Accept-Encoding": "gzip, deflate",
        }
        try:
            bruta = self.session.get(url, headers=cabecalhos, timeout=self.timeout)
        except requests.RequestException as erro:
            raise RedeError(f"{type(erro).__name__} em {url}") from erro

        # `len(bruta.content)` é o corpo **descomprimido**; o que a conta de banda quer
        # é o que passou no fio. O `Content-Length` da resposta comprimida é o número
        # certo quando ele vem, e o descomprimido é a reserva honesta quando não vem.
        declarado = bruta.headers.get("Content-Length")
        no_fio = int(declarado) if (declarado or "").isdigit() else len(bruta.content)
        self.bytes_gastos += no_fio
        self.requisicoes += 1

        return Resposta(
            url=url,
            status=bruta.status_code,
            texto=bruta.text,
            cabecalhos=dict(bruta.headers),
            bytes_no_fio=no_fio,
        )
