"""Diagnóstico: cada fonte responde a esta máquina? Rodar no PC e no runner e comparar.

    python -m coletor.diagnostico

No GitHub Actions a tabela vai também para o Step Summary da execução.
"""

import os
import time
from datetime import UTC, datetime, timedelta

import httpx

from coletor.rede import criar_cliente

# Ponto de Copacabana em mar aberto (seção 3 do plano). Um ponto basta para o diagnóstico.
LAT, LON = -23.010, -43.160


def fontes(agora: datetime) -> dict[str, str]:
    """URL de cada fonte, com janelas pequenas para a resposta ser leve."""
    inicio_boia = int((agora - timedelta(hours=6)).timestamp())
    fim = int(agora.timestamp())
    ioc_inicio = (agora - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    ioc_fim = agora.strftime("%Y-%m-%dT%H:%M")
    ponto = f"latitude={LAT}&longitude={LON}"
    return {
        "openmeteo_marine": f"https://marine-api.open-meteo.com/v1/marine?{ponto}"
        "&hourly=wave_height&forecast_days=1",
        "openmeteo_forecast": f"https://api.open-meteo.com/v1/forecast?{ponto}"
        "&hourly=temperature_2m&forecast_days=1",
        "openmeteo_archive": f"https://archive-api.open-meteo.com/v1/archive?{ponto}"
        "&hourly=temperature_2m&start_date=2026-01-01&end_date=2026-01-01",
        "omm_metarea5": "https://wwmiws.wmo.int/index.php/metareas/affiche/5",
        "chm_avisos": "https://www.marinha.mil.br/chm/dados-do-smm-avisos-de-mau-tempo/"
        "avisos-de-mau-tempo",
        "inea_seirhi": "https://seirhi.inea.rj.gov.br/publico",
        "inea_boletins_rio": "https://www.inea.rj.gov.br/rio-de-janeiro/",
        "boia_rj4": "https://simcosta.furg.br/api/intrans_data?boiaID=12&type=json"
        f"&time1={inicio_boia}&time2={fim}&params=Hsig",
        "ioc_niteroi": "https://www.ioc-sealevelmonitoring.org/service.php?query=data"
        f"&code=dhnn&timestart={ioc_inicio}&timestop={ioc_fim}&format=json",
    }


def diagnosticar(urls: dict[str, str], cliente: httpx.Client, pausa_s: float = 1.0) -> list[dict]:
    """Uma requisição por fonte, sem novas tentativas: queremos a primeira resposta crua.

    Um 403 aqui é informação, não erro: ele entra na tabela e o diagnóstico segue.
    """
    resultados = []
    for i, (nome, url) in enumerate(urls.items()):
        if i:
            time.sleep(pausa_s)  # educado: uma fonte por vez, com pausa
        inicio = time.perf_counter()
        try:
            resposta = cliente.get(url)
        except httpx.TransportError as erro:
            resultados.append(
                {
                    "fonte": nome,
                    "status": None,
                    "erro": type(erro).__name__,
                    "ms": _ms(inicio),
                    "bytes": 0,
                    "server": "",
                    "cf_ray": "",
                }
            )
            continue
        resultados.append(
            {
                "fonte": nome,
                "status": resposta.status_code,
                "erro": "",
                "ms": _ms(inicio),
                "bytes": len(resposta.content),
                "server": resposta.headers.get("server", ""),
                "cf_ray": resposta.headers.get("cf-ray", ""),
            }
        )
    return resultados


def _ms(inicio: float) -> int:
    return round((time.perf_counter() - inicio) * 1000)


def tabela_markdown(resultados: list[dict], onde: str) -> str:
    linhas = [
        f"### Diagnóstico das fontes · {onde}",
        "",
        "| fonte | status | ms | bytes | server | cf-ray |",
        "|---|---|---:|---:|---|---|",
    ]
    for r in resultados:
        status = r["status"] if r["status"] is not None else f"erro: {r['erro']}"
        linhas.append(
            f"| {r['fonte']} | {status} | {r['ms']} | {r['bytes']} "
            f"| {r['server']} | {r['cf_ray']} |"
        )
    return "\n".join(linhas) + "\n"


def main() -> None:
    no_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    onde = "runner do GitHub" if no_actions else "máquina local"
    agora = datetime.now(UTC)
    with criar_cliente() as cliente:
        resultados = diagnosticar(fontes(agora), cliente)
    tabela = tabela_markdown(resultados, f"{onde} · {agora:%Y-%m-%d %H:%M} UTC")
    print(tabela)
    resumo = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as arquivo:
            arquivo.write(tabela)


if __name__ == "__main__":
    main()
