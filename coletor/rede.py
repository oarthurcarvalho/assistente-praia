"""Acesso HTTP do coletor: um cliente educado e downloads com novas tentativas."""

import time

import httpx

USER_AGENT = "assistente-praia/0.1 (+https://github.com/oarthurcarvalho/assistente-praia)"
TIMEOUT_S = 20
TENTATIVAS = 3
ESPERAS_S = (1, 4)  # pausa antes da 2ª e da 3ª tentativa


def criar_cliente() -> httpx.Client:
    """Um cliente só para a execução inteira: reaproveita conexões e leva o User-Agent."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
        follow_redirects=True,  # o httpx, ao contrário do requests, não segue sozinho
    )


def _passageiro(status: int) -> bool:
    """429 (pediu para esperar) e 5xx (servidor com problema) costumam passar."""
    return status == 429 or status >= 500


def baixar(url: str, cliente: httpx.Client, params: dict | None = None) -> httpx.Response:
    """GET com até 3 tentativas em 429, 5xx e erro de rede; outro 4xx falha na hora.

    Na última tentativa a exceção sobe: quem chama decide o que fazer com a falha.
    """
    for tentativa in range(1, TENTATIVAS + 1):
        ultima = tentativa == TENTATIVAS
        try:
            resposta = cliente.get(url, params=params)
        except httpx.TransportError:  # timeout, DNS, conexão recusada ou cortada
            if ultima:
                raise
        else:
            if ultima or not _passageiro(resposta.status_code):
                resposta.raise_for_status()  # 4xx/5xx viram HTTPStatusError
                return resposta
        time.sleep(ESPERAS_S[tentativa - 1])
    raise AssertionError("inalcançável: a última tentativa sempre devolve ou levanta")
