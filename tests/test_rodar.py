import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from botocore.exceptions import ClientError

from coletor import rodar
from coletor.armazenamento import R2, Local, ler_estado
from coletor.execucao import CHAVE_HASHES
from coletor.observacoes import JANELA, buscar_boia, buscar_ioc

AGORA = datetime(2026, 11, 27, 9, 23, 5, tzinfo=UTC)
PAGINA_OMM = '<a href="https://wwmiws.wmo.int/x/bulletin_download/WWST02_SBBR/222">WW</a>'
PAGINA_CHM = "<p>AVISO NR 813/2026</p><p>MAR GROSSO</p><p>Compartilhe:</p>"


def fontes_falsas(falhar: str | None = None):
    """Um servidor de mentira para todas as fontes; `falhar` responde 404 naquele host."""

    def tratador(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == falhar:
            return httpx.Response(404)
        if "open-meteo" in host:
            return httpx.Response(200, json=[{"latitude": -23.0, "longitude": -43.2}] * 8)
        if host == "wwmiws.wmo.int":
            texto = "WWST02 SBBR 270030\nSECURITE" if "download" in request.url.path else PAGINA_OMM
            return httpx.Response(200, text=texto)
        if host == "www.marinha.mil.br":
            return httpx.Response(200, text=PAGINA_CHM)
        if host == "simcosta.furg.br":
            return httpx.Response(200, json=[{"timestamp": "2026-11-27T09:21:40.000Z", "Hsig": 1}])
        if host == "www.ioc-sealevelmonitoring.org":
            return httpx.Response(200, json=[{"slevel": 1.2, "stime": "2026-11-27 09:20:00"}])
        raise AssertionError(f"fonte inesperada: {request.url}")

    return httpx.Client(transport=httpx.MockTransport(tratador))


def relogio_fixo(inicio=AGORA):
    """Cada chamada avança 1 s: arquivos de fontes diferentes não colidem de nome."""
    passos = iter(range(10_000))
    return lambda: inicio + timedelta(seconds=next(passos))


@pytest.mark.parametrize(
    ("hora", "bloco"), [("00:23", 0), ("09:23", 9), ("11:40", 9), ("12:05", 12), ("23:59", 21)]
)
def test_bloco_do_cron_tolera_atraso(hora, bloco):
    h, m = map(int, hora.split(":"))
    assert rodar.bloco_do_horario(AGORA.replace(hour=h, minute=m)) == bloco


def test_o_que_coletar_por_horario():
    assert rodar.o_que_coletar(AGORA.replace(hour=9)) == {"chm": True, "diarias": True}
    assert rodar.o_que_coletar(AGORA.replace(hour=21)) == {"chm": True, "diarias": False}
    assert rodar.o_que_coletar(AGORA.replace(hour=3)) == {"chm": False, "diarias": False}
    assert rodar.o_que_coletar(AGORA.replace(hour=3), todas=True) == {"chm": True, "diarias": True}


def test_execucao_completa_grava_bruto_estado_e_log(tmp_path):
    armazem = Local(tmp_path)
    resultados, chave_log = rodar.rodar(fontes_falsas(), armazem, relogio=relogio_fixo())

    fontes = {r["fonte"]: r["status"] for r in resultados}
    assert fontes == {
        "openmeteo_mar": "ok", "openmeteo_multimodelo": "ok",
        "openmeteo_tempo_mar": "ok", "openmeteo_tempo_terra": "ok",
        "avisos_omm": "ok", "avisos_chm": "ok", "boia_rj4": "ok", "ioc_niteroi": "ok",
    }  # fmt: skip
    assert rodar.codigo_de_saida(resultados) == 0
    assert set(ler_estado(armazem, CHAVE_HASHES)) == {"avisos_omm", "avisos_chm"}

    assert chave_log == "execucoes/dt=2026-11-27/092305.json"
    log = json.loads(armazem.ler(chave_log))
    assert log["contagem"] == {"ok": 8}
    assert log["disparo"] in ("local", "workflow_dispatch", "schedule")


def test_fonte_que_falha_nao_derruba_as_outras(tmp_path):
    armazem = Local(tmp_path)
    cliente = fontes_falsas(falhar="simcosta.furg.br")  # 404: falha na hora, sem novas tentativas
    resultados, chave_log = rodar.rodar(cliente, armazem, relogio=relogio_fixo())

    por_fonte = {r["fonte"]: r for r in resultados}
    assert por_fonte["boia_rj4"]["status"] == "falha"
    assert "404" in por_fonte["boia_rj4"]["erro"]
    assert por_fonte["ioc_niteroi"]["status"] == "ok"  # veio depois e gravou
    assert rodar.codigo_de_saida(resultados) == 1
    assert json.loads(armazem.ler(chave_log))["contagem"] == {"ok": 7, "falha": 1}


def test_fora_das_09h_nao_busca_diarias_nem_chm(tmp_path):
    resultados, _ = rodar.rodar(
        fontes_falsas(), Local(tmp_path), relogio=relogio_fixo(AGORA.replace(hour=15))
    )
    fontes = {r["fonte"] for r in resultados}
    assert "boia_rj4" not in fontes and "avisos_chm" not in fontes
    assert "avisos_omm" in fontes


def test_segunda_execucao_le_o_estado_da_primeira(tmp_path):
    armazem = Local(tmp_path)
    rodar.rodar(fontes_falsas(), armazem, relogio=relogio_fixo())
    resultados, _ = rodar.rodar(
        fontes_falsas(), armazem, relogio=relogio_fixo(AGORA + timedelta(hours=3))
    )
    assert {r["fonte"]: r["status"] for r in resultados}["avisos_omm"] == "sem_mudanca"


class S3Falso:
    """Só o que o R2 usa do boto3; o erro imita o ClientError de verdade."""

    def __init__(self, codigo_erro: str | None = None):
        self.objetos, self.codigo_erro = {}, codigo_erro

    def put_object(self, Bucket, Key, Body):  # noqa: N803 (nomes da API do boto3)
        self.objetos[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if self.codigo_erro or (Bucket, Key) not in self.objetos:
            erro = {"Error": {"Code": self.codigo_erro or "NoSuchKey"}}
            raise ClientError(erro, "GetObject")

        class Corpo:
            read = staticmethod(lambda: self.objetos[(Bucket, Key)])

        return {"Body": Corpo()}


def test_r2_grava_e_le_no_bucket_certo():
    s3 = S3Falso()
    r2 = R2(s3, "praia-raw")
    r2.salvar("estado/hashes.json", b"{}")
    assert ("praia-raw", "estado/hashes.json") in s3.objetos
    assert r2.ler("estado/hashes.json") == b"{}"


def test_r2_sem_estado_e_vazio_mas_sem_permissao_e_erro():
    assert R2(S3Falso(), "praia-raw").ler("estado/hashes.json") is None
    with pytest.raises(ClientError):
        R2(S3Falso("AccessDenied"), "praia-raw").ler("estado/hashes.json")


def test_observacoes_pedem_janela_de_26_horas():
    pedidos = []

    def tratador(request):
        pedidos.append(request.url)
        return httpx.Response(200, json=[])

    cliente = httpx.Client(transport=httpx.MockTransport(tratador))
    boia, ioc = buscar_boia(cliente, AGORA), buscar_ioc(cliente, AGORA)

    p = pedidos[0].params
    assert int(p["time2"]) - int(p["time1"]) == 26 * 3600
    assert "Hsig" in p["params"].split(",")
    assert pedidos[1].params["timestart"] == f"{AGORA - JANELA:%Y-%m-%dT%H:%M}"
    assert boia["fonte"] == "boia_rj4" and ioc["fonte"] == "ioc_niteroi"
    assert boia["resposta"] == []  # resposta inteira, mesmo vazia: o parse da semana 3 decide


def test_tabela_do_step_summary_mostra_a_falha():
    resultados = [
        {"fonte": "openmeteo_mar", "status": "ok", "bytes": 2048, "ms": 900, "chave": "raw/x"},
        {"fonte": "boia_rj4", "status": "falha", "erro": "HTTPStatusError('404')", "ms": 80},
    ]
    tabela = rodar.tabela_markdown(resultados, "execucoes/dt=2026-11-28/092305.json")
    assert "| openmeteo_mar | ok | 2.0 | 900 | `raw/x` |" in tabela
    assert "| boia_rj4 | falha | 0.0 | 80 | `HTTPStatusError('404')` |" in tabela
    assert tabela.rstrip().endswith("`execucoes/dt=2026-11-28/092305.json`")
