from datetime import UTC, datetime

import httpx

from coletor import diagnostico


def test_403_entra_na_tabela_sem_derrubar_o_diagnostico():
    def tratador(request):
        if request.url.host == "bloqueia.test":
            return httpx.Response(403, headers={"server": "cloudflare", "cf-ray": "abc-GRU"})
        if request.url.host == "cai.test":
            raise httpx.ConnectError("caiu")
        return httpx.Response(200, content=b"12345")

    cliente = httpx.Client(transport=httpx.MockTransport(tratador))
    urls = {
        "boa": "https://boa.test/",
        "bloqueada": "https://bloqueia.test/",
        "fora": "https://cai.test/",
    }
    resultados = diagnostico.diagnosticar(urls, cliente, pausa_s=0)

    boa, bloqueada, fora = resultados
    assert (boa["status"], boa["bytes"]) == (200, 5)
    assert (bloqueada["status"], bloqueada["server"], bloqueada["cf_ray"]) == (
        403,
        "cloudflare",
        "abc-GRU",
    )
    assert (fora["status"], fora["erro"]) == (None, "ConnectError")

    tabela = diagnostico.tabela_markdown(resultados, "teste")
    assert "| bloqueada | 403 |" in tabela
    assert "erro: ConnectError" in tabela


def test_fontes_cobrem_o_plano_e_usam_janelas_do_momento():
    urls = diagnostico.fontes(datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    assert {
        "openmeteo_marine",
        "omm_metarea5",
        "chm_avisos",
        "inea_seirhi",
        "boia_rj4",
        "ioc_niteroi",
    } <= urls.keys()
    assert "time2=1795435200" in urls["boia_rj4"]  # 23/11/2026 12:00 UTC em epoch
    assert "timestop=2026-11-23T12:00" in urls["ioc_niteroi"]
