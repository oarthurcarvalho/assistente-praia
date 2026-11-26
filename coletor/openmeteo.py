"""Previsões do Open-Meteo para as 8 praias: 4 pedidos por coleta, resposta inteira.

    python -m coletor.openmeteo        # busca os 4 tipos e grava em dados/raw/

Dados: Open-Meteo.com, CC-BY 4.0.
"""

from datetime import UTC, datetime

import httpx

from coletor.armazenamento import Local, chave_bruto, json_gz
from coletor.praias import carregar_praias
from coletor.rede import baixar, criar_cliente

MARINE = "https://marine-api.open-meteo.com/v1/marine"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# Os 3 *_peak_period ficaram de fora: vinham 100% nulos (medido em 19/11/2026).
VARIAVEIS_MAR = [
    "wave_height", "wave_direction", "wave_period",
    "wind_wave_height", "wind_wave_direction", "wind_wave_period",
    "swell_wave_height", "swell_wave_direction", "swell_wave_period",
    "secondary_swell_wave_height", "secondary_swell_wave_direction",
    "secondary_swell_wave_period",
    "ocean_current_velocity", "ocean_current_direction",
    "sea_surface_temperature", "sea_level_height_msl",
]  # fmt: skip
VARIAVEIS_TEMPO = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "precipitation", "precipitation_probability", "rain", "weather_code",
    "cloud_cover", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "uv_index",
]  # fmt: skip

# tipo -> (endpoint, parâmetros fixos). Hora sempre em GMT (UTC), o padrão da API.
TIPOS = {
    # MF-WAM (best_match) + correntes, 10 dias à frente e 1 para trás.
    "mar": (MARINE, {"hourly": VARIAVEIS_MAR, "forecast_days": 10, "past_days": 1}),
    # Os três modelos lado a lado: nenhum tem histórico na API, só o que guardarmos.
    "multimodelo": (
        MARINE,
        {
            "hourly": ["wave_height", "wave_period", "wave_direction"],
            "models": ["meteofrance_wave", "ecmwf_wam025", "ncep_gfswave025"],
            "forecast_days": 16,
        },
    ),
    # Vento sobre a água (cell_selection=sea) e chuva/temperatura em terra (land).
    "tempo_mar": (
        FORECAST,
        {"hourly": VARIAVEIS_TEMPO, "forecast_days": 8, "cell_selection": "sea"},
    ),
    "tempo_terra": (
        FORECAST,
        {"hourly": VARIAVEIS_TEMPO, "forecast_days": 8, "cell_selection": "land"},
    ),
}


def montar_params(tipo: str, praias: list[dict]) -> dict[str, str]:
    """Um pedido para todas as praias: a API aceita listas e responde na mesma ordem."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo desconhecido: {tipo!r} (use um de {sorted(TIPOS)})")
    _, fixos = TIPOS[tipo]
    params = {
        "latitude": ",".join(str(p["lat"]) for p in praias),
        "longitude": ",".join(str(p["lon"]) for p in praias),
    }
    for nome, valor in fixos.items():
        params[nome] = ",".join(valor) if isinstance(valor, list) else str(valor)
    return params


def buscar_openmeteo(tipo: str, praias: list[dict], cliente: httpx.Client, agora: datetime) -> dict:
    """Envelope com a resposta inteira, sem filtrar campo: o parse fica para depois."""
    params = montar_params(tipo, praias)  # valida o tipo antes de qualquer requisição
    url, _ = TIPOS[tipo]
    resposta = baixar(url, cliente, params=params)
    return {
        "fonte": f"openmeteo_{tipo}",
        "coletado_em": agora.isoformat(),
        "url": str(resposta.url),
        "praias": [p["chave"] for p in praias],  # a ordem da lista na resposta
        "resposta": resposta.json(),
    }


def celulas(resposta: list[dict]) -> set[tuple[float, float]]:
    """Células da grade que a API usou de fato (ela devolve a lat/lon da célula)."""
    return {(local["latitude"], local["longitude"]) for local in resposta}


def buracos(resposta: list[dict], prefixo: str = "wave_height") -> int:
    """Nulos no meio das séries que começam com `prefixo`.

    Nulo no fim é horizonte (o modelo ainda não publicou aquele dia) e não conta.
    Nulo no meio, ou série toda nula, indica ponto em terra ou falha da fonte.
    """
    total = 0
    for local in resposta:
        for nome, serie in local["hourly"].items():
            if not nome.startswith(prefixo):
                continue
            com_valor = [i for i, v in enumerate(serie) if v is not None]
            if not com_valor:
                total += len(serie)
                continue
            total += sum(v is None for v in serie[: com_valor[-1] + 1])
    return total


def ultima_hora(resposta: list[dict], serie: str) -> str | None:
    """Última hora com valor na série, na primeira praia: mostra até onde o modelo vai."""
    horas, valores = resposta[0]["hourly"]["time"], resposta[0]["hourly"].get(serie, [])
    com_valor = [h for h, v in zip(horas, valores, strict=False) if v is not None]
    return com_valor[-1] if com_valor else None


def main() -> None:
    armazem = Local()  # ensaio em dados/; a coleta de verdade é o coletor.rodar
    praias = carregar_praias()
    with criar_cliente() as cliente:
        for tipo in TIPOS:
            agora = datetime.now(UTC)
            envelope = buscar_openmeteo(tipo, praias, cliente, agora)
            conteudo = json_gz(envelope)
            caminho = chave_bruto(envelope["fonte"], agora)
            armazem.salvar(caminho, conteudo)
            resposta = envelope["resposta"]
            linha = f"{tipo:12} {len(conteudo) / 1024:5.1f} KB  células {len(celulas(resposta))}"
            if tipo == "mar":
                linha += (
                    f"  buracos em wave_height {buracos(resposta)}"
                    f"  até {ultima_hora(resposta, 'wave_height')}"
                )
            print(f"{linha}  {caminho}")


if __name__ == "__main__":
    main()
