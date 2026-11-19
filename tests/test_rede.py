import httpx
import pytest

from coletor import rede


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    """As novas tentativas não esperam de verdade nos testes."""
    monkeypatch.setattr("coletor.rede.time.sleep", lambda s: None)


def cliente_com(respostas):
    """Cliente falso que devolve (ou levanta) cada item de `respostas`, em ordem."""
    chamadas = []

    def tratador(request):
        chamadas.append(request)
        item = respostas[len(chamadas) - 1]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, text="ok")

    return httpx.Client(transport=httpx.MockTransport(tratador)), chamadas


def test_200_devolve_na_primeira():
    cliente, chamadas = cliente_com([200])
    assert rede.baixar("https://x.test/", cliente).text == "ok"
    assert len(chamadas) == 1


def test_404_falha_sem_nova_tentativa():
    cliente, chamadas = cliente_com([404, 200])
    with pytest.raises(httpx.HTTPStatusError):
        rede.baixar("https://x.test/", cliente)
    assert len(chamadas) == 1


def test_503_duas_vezes_e_depois_200():
    cliente, chamadas = cliente_com([503, 503, 200])
    assert rede.baixar("https://x.test/", cliente).status_code == 200
    assert len(chamadas) == 3


def test_429_tres_vezes_levanta():
    cliente, chamadas = cliente_com([429, 429, 429])
    with pytest.raises(httpx.HTTPStatusError):
        rede.baixar("https://x.test/", cliente)
    assert len(chamadas) == 3


def test_erro_de_rede_e_depois_200():
    cliente, chamadas = cliente_com([httpx.ConnectError("caiu"), 200])
    assert rede.baixar("https://x.test/", cliente).status_code == 200
    assert len(chamadas) == 2


def test_erro_de_rede_tres_vezes_levanta():
    cliente, chamadas = cliente_com([httpx.ReadTimeout("lento")] * 3)
    with pytest.raises(httpx.ReadTimeout):
        rede.baixar("https://x.test/", cliente)
    assert len(chamadas) == 3


def test_cliente_leva_user_agent_com_link_do_repositorio():
    with rede.criar_cliente() as cliente:
        assert "github.com/oarthurcarvalho/assistente-praia" in cliente.headers["user-agent"]
        assert cliente.timeout.read == rede.TIMEOUT_S
