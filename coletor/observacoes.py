"""O que foi medido de fato: boia RJ-4 (SiMCosta) e marégrafo de Niterói (IOC). Coleta diária.

    python -m coletor.observacoes      # busca as duas e grava em dados/raw/

A previsão diz o que o mar ia fazer; isto aqui é o que ele fez. É a régua da semana 3.
"""

from datetime import UTC, datetime, timedelta

import httpx

from coletor.armazenamento import Local, chave_bruto, json_gz
from coletor.rede import baixar, criar_cliente

# 24 h + 2 de sobra: a coleta diária que atrasar (ou a de ontem que falhou por
# pouco) não deixa buraco. As horas repetidas entre dias somem no parse.
JANELA = timedelta(hours=26)

BOIA_URL = "https://simcosta.furg.br/api/intrans_data"
BOIA_ID = 12  # RJ-4, na boca da baía de Guanabara (o ponto antigo de Copacabana)
# A API exige a lista. Medido em 25/11/2026 (1 leitura a cada 30 min):
#   com valor: Hsig, Hmax, Tp, direções, vento, rajada, temperatura da água, salinidade
#   vazios hoje, mas pedidos assim mesmo (o sensor pode voltar): Avg_Air_Tmp, Avg_Hmt
PARAMS_BOIA = [
    "Hsig", "Hmax", "TP", "Avg_Wv_Dir", "Avg_Wv_Dir_N",
    "Avg_Wnd_Sp", "Gust_Sp", "Avg_Wnd_Dir", "Avg_Wnd_Dir_N",
    "Avg_W_Tmp1", "Avg_W_Tmp2", "Avg_Sal", "Avg_Air_Tmp", "Avg_Hmt",
]  # fmt: skip

IOC_URL = "https://www.ioc-sealevelmonitoring.org/service.php"
IOC_ESTACAO = "dhnn"  # Niterói: nível do mar a cada minuto (~1.500 leituras em 26 h)


def _envelope(fonte: str, resposta: httpx.Response, agora: datetime) -> dict:
    return {
        "fonte": fonte,
        "coletado_em": agora.isoformat(),
        "url": str(resposta.url),
        "janela_h": JANELA.total_seconds() / 3600,
        "resposta": resposta.json(),
    }


def buscar_boia(cliente: httpx.Client, agora: datetime) -> dict:
    params = {
        "boiaID": BOIA_ID,
        "type": "json",
        "time1": int((agora - JANELA).timestamp()),  # epoch em segundos
        "time2": int(agora.timestamp()),
        "params": ",".join(PARAMS_BOIA),
    }
    return _envelope("boia_rj4", baixar(BOIA_URL, cliente, params=params), agora)


def buscar_ioc(cliente: httpx.Client, agora: datetime) -> dict:
    params = {
        "query": "data",
        "code": IOC_ESTACAO,
        "timestart": f"{agora - JANELA:%Y-%m-%dT%H:%M}",  # UTC
        "timestop": f"{agora:%Y-%m-%dT%H:%M}",
        "format": "json",
    }
    return _envelope("ioc_niteroi", baixar(IOC_URL, cliente, params=params), agora)


def main() -> None:
    armazem = Local()
    with criar_cliente() as cliente:
        for buscar in (buscar_boia, buscar_ioc):
            agora = datetime.now(UTC)
            envelope = buscar(cliente, agora)
            conteudo = json_gz(envelope)
            chave = chave_bruto(envelope["fonte"], agora)
            armazem.salvar(chave, conteudo)
            leituras = len(envelope["resposta"])
            print(f"{envelope['fonte']:12} {leituras:5} leituras {len(conteudo):>7} B  {chave}")


if __name__ == "__main__":
    main()
