import gzip
from datetime import UTC, datetime

import httpx
import pytest

from coletor import avisos
from coletor.armazenamento import ler_estado, salvar_estado
from coletor.execucao import mudou, sha256_texto

AGORA = datetime(2026, 11, 26, 9, 23, 5, tzinfo=UTC)

PAGINA_OMM = """
<a href="https://wwmiws.wmo.int/index.php/metareas/display/bulletin_download/FQST02_SBBR/111">FQ</a>
<a href="https://wwmiws.wmo.int/index.php/metareas/display/bulletin_download/WWST02_SBBR/222">WW</a>
<a href="https://wwmiws.wmo.int/index.php/metareas/display/bulletin_download/XXXX99_ZZZZ/333">?</a>
"""
BOLETIM = (
    "WWST02 SBBR 250030\n\nSECURITE\n\nWARNING NR 811/2026\nGALE WARNING\nVALID UNTIL 272100Z.\n"
)
PAGINA_CHM = """<html><head><style>.x{}</style><script>var t=1;</script></head><body>
<p>atualizado em 24/11/2026 - 14:49</p><svg><text>mapa</text></svg>
<!-- INICIO AREA DO AVISO NR 812/2026 -->
<h3>ÁREA ALFA</h3><p>AVISO NR 813/2026</p><p>AVISO DE MAR GROSSO</p>
<p>VÁLIDO ATÉ 271800Z.</p><p>Compartilhe:</p><footer>menu</footer></body></html>"""


def test_links_so_dos_boletins_conhecidos():
    links = avisos.links_boletins(PAGINA_OMM)
    assert set(links) == {"FQST02_SBBR", "WWST02_SBBR"}
    assert links["WWST02_SBBR"].endswith("/WWST02_SBBR/222")


def test_pagina_sem_links_e_falha_e_nao_lista_vazia():
    with pytest.raises(ValueError, match="layout"):
        avisos.links_boletins("<html>manutenção</html>")


def test_boletim_retransmitido_com_outra_hora_nao_e_mudanca():
    outra_transmissao = BOLETIM.replace("250030", "250630").replace("\n\n", "\n\n\n")
    assert avisos.normalizar_boletim(BOLETIM) == avisos.normalizar_boletim(outra_transmissao)
    assert avisos.normalizar_boletim(BOLETIM).startswith("SECURITE\nWARNING NR 811/2026")


def test_chm_hash_ignora_o_resto_da_pagina():
    texto = avisos.texto_avisos_chm(PAGINA_CHM)
    assert texto == "AVISO NR 813/2026\nAVISO DE MAR GROSSO\nVÁLIDO ATÉ 271800Z."
    outra_hora = PAGINA_CHM.replace("14:49", "15:10").replace("mapa", "mapa novo")
    assert avisos.texto_avisos_chm(outra_hora) == texto


def test_chm_sem_aviso_vigente_vira_texto_vazio():
    assert avisos.texto_avisos_chm("<p>Não há avisos em vigor.</p><p>Compartilhe:</p>") == ""


def test_mudou_compara_com_o_ultimo_hash_da_fonte():
    hashes = {"avisos_omm": sha256_texto("A")}
    assert not mudou("avisos_omm", "A", hashes)
    assert mudou("avisos_omm", "B", hashes)
    assert mudou("avisos_chm", "A", hashes)  # fonte nova: sempre muda


def test_primeira_grava_segunda_registra_sem_mudanca(tmp_path):
    hashes = {}
    gravados = {}

    def salvar(chave, conteudo):
        gravados[chave] = conteudo

    args = ("avisos_omm", BOLETIM.encode(), "texto", "txt.gz", hashes, AGORA)
    primeira = avisos.guardar_se_mudou(*args, salvar=salvar)
    segunda = avisos.guardar_se_mudou(*args, salvar=salvar)

    assert primeira["status"] == "ok"
    assert primeira["chave"] == "raw/avisos_omm/dt=2026-11-26/092305.txt.gz"
    assert gzip.decompress(gravados[primeira["chave"]]) == BOLETIM.encode()
    assert segunda["status"] == "sem_mudanca" and segunda["chave"] is None
    assert len(gravados) == 1


def test_falha_ao_gravar_nao_atualiza_o_hash():
    hashes = {}

    def salvar_quebrado(chave, conteudo):
        raise OSError("disco cheio")

    with pytest.raises(OSError):
        avisos.guardar_se_mudou("avisos_omm", b"x", "x", "txt.gz", hashes, AGORA, salvar_quebrado)
    assert hashes == {}  # a próxima execução tenta gravar de novo


def test_403_no_chm_nao_derruba_a_omm(monkeypatch):
    monkeypatch.setattr(  # sem gravar nada: o teste é só sobre o isolamento das fontes
        avisos,
        "guardar_se_mudou",
        lambda fonte, conteudo, texto, ext, hashes, agora: {"fonte": fonte, "status": "ok"},
    )

    def tratador(request):
        if request.url.host == "www.marinha.mil.br":
            return httpx.Response(403)
        if "bulletin_download" in request.url.path:
            return httpx.Response(200, text=BOLETIM)
        return httpx.Response(200, text=PAGINA_OMM)

    cliente = httpx.Client(transport=httpx.MockTransport(tratador))
    resultados = avisos.buscar_avisos(cliente, {}, AGORA)

    por_fonte = {r["fonte"]: r["status"] for r in resultados}
    assert por_fonte == {"boletim_omm": "ok", "avisos_omm": "ok", "chm": "falha"}
    assert "403" in resultados[-1]["erro"]


def test_estado_vai_e_volta(tmp_path):
    assert ler_estado("estado/hashes.json", raiz=tmp_path) == {}
    salvar_estado("estado/hashes.json", {"avisos_omm": "abc"}, raiz=tmp_path)
    assert ler_estado("estado/hashes.json", raiz=tmp_path) == {"avisos_omm": "abc"}
