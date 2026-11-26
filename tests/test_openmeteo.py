import gzip
import json
from datetime import UTC, datetime

import httpx
import pytest

from coletor import openmeteo
from coletor.armazenamento import Local, chave_bruto, json_gz
from coletor.praias import carregar_praias

AGORA = datetime(2026, 11, 28, 3, 17, 4, tzinfo=UTC)
PRAIAS = [
    {"chave": "copacabana", "lat": -23.010, "lon": -43.160},
    {"chave": "ipanema", "lat": -22.995, "lon": -43.205},
]


def test_praias_toml_tem_as_8_praias_completas():
    praias = carregar_praias()
    assert [p["chave"] for p in praias][:2] == ["copacabana", "arpoador"]
    assert len(praias) == 8
    for p in praias:
        assert -23.2 < p["lat"] < -22.9 and -43.6 < p["lon"] < -43.1
        assert 0 <= p["orientacao"] < 360
        assert p["inea"].startswith("02RJ01")


def test_params_juntam_as_praias_na_ordem():
    params = openmeteo.montar_params("mar", PRAIAS)
    assert params["latitude"] == "-23.01,-22.995"
    assert params["longitude"] == "-43.16,-43.205"
    assert params["hourly"].startswith("wave_height,")
    assert "peak_period" not in params["hourly"]
    assert params["past_days"] == "1"


@pytest.mark.parametrize(("tipo", "celula"), [("tempo_mar", "sea"), ("tempo_terra", "land")])
def test_tempo_escolhe_celula_de_mar_ou_de_terra(tipo, celula):
    assert openmeteo.montar_params(tipo, PRAIAS)["cell_selection"] == celula


def test_multimodelo_pede_os_tres_modelos():
    params = openmeteo.montar_params("multimodelo", PRAIAS)
    assert params["models"] == "meteofrance_wave,ecmwf_wam025,ncep_gfswave025"


def test_tipo_desconhecido_falha_com_mensagem_clara():
    with pytest.raises(ValueError, match="tipo desconhecido"):
        openmeteo.montar_params("ondas", PRAIAS)


def test_envelope_guarda_resposta_inteira_e_hora_da_coleta():
    resposta_api = [{"latitude": -23.04, "longitude": -43.12, "hourly": {"wave_height": [1.2]}}]
    pedidos = []

    def tratador(request):
        pedidos.append(request)
        return httpx.Response(200, json=resposta_api)

    cliente = httpx.Client(transport=httpx.MockTransport(tratador))
    envelope = openmeteo.buscar_openmeteo("mar", PRAIAS, cliente, AGORA)

    assert pedidos[0].url.host == "marine-api.open-meteo.com"
    assert envelope["fonte"] == "openmeteo_mar"
    assert envelope["coletado_em"] == "2026-11-28T03:17:04+00:00"
    assert envelope["praias"] == ["copacabana", "ipanema"]
    assert envelope["resposta"] == resposta_api
    assert "latitude=-23.01" in envelope["url"].replace("%2C", ",")


def test_celulas_contam_pontos_distintos_da_grade():
    resposta = [
        {"latitude": -23.04, "longitude": -43.21},
        {"latitude": -23.04, "longitude": -43.21},
        {"latitude": -23.04, "longitude": -43.29},
    ]
    assert len(openmeteo.celulas(resposta)) == 2


def test_nulo_no_fim_e_horizonte_nulo_no_meio_e_buraco():
    horas = ["00:00", "01:00", "02:00", "03:00"]
    resposta = [
        {"hourly": {"time": horas, "wave_height": [1.0, 1.1, None, None]}},  # horizonte
        {"hourly": {"time": horas, "wave_height": [1.0, None, 1.2, None]}},  # 1 buraco
        {"hourly": {"time": horas, "wave_height": [None] * 4}},  # em terra: 4
        {"hourly": {"time": horas, "wave_height_ecmwf_wam025": [None, 2.0]}},  # multimodelo
    ]
    assert openmeteo.buracos(resposta) == 1 + 4 + 1
    assert openmeteo.ultima_hora(resposta, "wave_height") == "01:00"


def test_chave_e_gravacao_do_bruto(tmp_path):
    chave = chave_bruto("openmeteo_mar", AGORA)
    assert chave == "raw/openmeteo_mar/dt=2026-11-28/031704.json.gz"

    Local(tmp_path).salvar(chave, json_gz({"a": "ação"}))
    with gzip.open(tmp_path / chave, "rt", encoding="utf-8") as arquivo:
        assert json.load(arquivo) == {"a": "ação"}
