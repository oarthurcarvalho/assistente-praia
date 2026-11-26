"""Avisos de mau tempo da METAREA V: boletins da OMM (principal) e página do CHM (reserva).

    python -m coletor.avisos       # rodar 2 vezes: a 2ª deve dar sem_mudanca

Só grava quando o texto dos avisos muda (sha256 do texto normalizado contra o último).
"""

import gzip
import html
import re
from datetime import UTC, datetime

import httpx

from coletor.armazenamento import Local, chave_bruto, ler_estado, salvar_estado
from coletor.execucao import CHAVE_HASHES, mudou, sha256_texto
from coletor.rede import baixar, criar_cliente

PAGINA_OMM = "https://wwmiws.wmo.int/index.php/metareas/affiche/5"
PAGINA_CHM = "https://www.marinha.mil.br/chm/dados-do-smm-avisos-de-mau-tempo/avisos-de-mau-tempo"

# Código do boletim na página da OMM -> fonte no bruto. O link muda a cada emissão.
BOLETINS_OMM = {
    "WWST02_SBBR": "avisos_omm",  # só os avisos (NR 811/2026...), sai mais vezes
    "FQST02_SBBR": "boletim_omm",  # avisos + previsão por área (CHARLIE = litoral do RJ)
}

_LINK_BOLETIM = re.compile(r'href="([^"]*/bulletin_download/([A-Z0-9]+_[A-Z]+)/\d+)"')
_CABECALHO_OMM = re.compile(r"^[A-Z]{4}\d{2} [A-Z]{4} \d{6}$")  # "WWST02 SBBR 250030"


def links_boletins(pagina: str) -> dict[str, str]:
    """Código -> URL do boletim atual. Página sem nenhum link conhecido é falha, não vazio."""
    links = {codigo: url for url, codigo in _LINK_BOLETIM.findall(pagina) if codigo in BOLETINS_OMM}
    if not links:
        raise ValueError("nenhum link de boletim na página da OMM: o layout mudou?")
    return links


def normalizar_boletim(texto: str) -> str:
    """Sem o cabeçalho com a hora da transmissão e sem diferença de espaços/linhas."""
    linhas = (" ".join(linha.split()) for linha in texto.splitlines())
    return "\n".join(linha for linha in linhas if linha and not _CABECALHO_OMM.match(linha))


def texto_avisos_chm(pagina: str) -> str:
    """Só o texto dos avisos: do primeiro "AVISO NR" até o "Compartilhe:" seguinte.

    Fica de fora o resto da página (mapa SVG, menus, "atualizado em ..."), que muda
    sem que os avisos mudem.
    """
    sem_codigo = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", pagina)
    sem_codigo = re.sub(r"(?s)<!--.*?-->", " ", sem_codigo)
    texto = html.unescape(re.sub(r"<[^>]+>", "\n", sem_codigo))
    linhas = [" ".join(linha.split()) for linha in texto.splitlines()]
    texto = "\n".join(linha for linha in linhas if linha)
    inicio = texto.find("AVISO NR")
    if inicio == -1:
        return ""  # sem aviso vigente (ou layout novo: o parse da semana 3 vai acusar)
    fim = texto.find("Compartilhe:", inicio)
    return texto[inicio : fim if fim != -1 else None].strip()


def guardar_se_mudou(
    fonte: str,
    conteudo: bytes,
    texto_hash: str,
    extensao: str,
    hashes: dict[str, str],
    agora: datetime,
    salvar,
) -> dict:
    """Grava o bruto inteiro (gz) se o texto mudou; atualiza o hash só depois de gravar.

    "bytes" é o que foi gravado (como nas outras fontes): 0 quando não houve mudança.
    """
    resultado = {"fonte": fonte, "sha256": sha256_texto(texto_hash)}
    if not mudou(fonte, texto_hash, hashes):
        return {**resultado, "status": "sem_mudanca", "bytes": 0, "chave": None}
    chave = chave_bruto(fonte, agora, extensao)
    comprimido = gzip.compress(conteudo)
    salvar(chave, comprimido)
    hashes[fonte] = resultado["sha256"]
    return {**resultado, "status": "ok", "bytes": len(comprimido), "chave": chave}


def buscar_omm(cliente: httpx.Client, hashes: dict, agora: datetime, salvar) -> list[dict]:
    links = links_boletins(baixar(PAGINA_OMM, cliente).text)
    resultados = []
    for codigo, url in links.items():
        resposta = baixar(url, cliente)
        texto = normalizar_boletim(resposta.text)
        fonte = BOLETINS_OMM[codigo]
        resultados.append(
            guardar_se_mudou(fonte, resposta.content, texto, "txt.gz", hashes, agora, salvar)
        )
    return resultados


def buscar_chm(cliente: httpx.Client, hashes: dict, agora: datetime, salvar) -> list[dict]:
    resposta = baixar(PAGINA_CHM, cliente)
    texto = texto_avisos_chm(resposta.text)
    resultado = guardar_se_mudou(
        "avisos_chm", resposta.content, texto, "html.gz", hashes, agora, salvar
    )
    return [resultado]


def buscar_avisos(
    cliente: httpx.Client, hashes: dict, agora: datetime, salvar, incluir_chm: bool = True
) -> list[dict]:
    """OMM sempre; CHM quando pedido (2×/dia). Uma fonte que falha não derruba a outra."""
    etapas = [("omm", buscar_omm)] + ([("chm", buscar_chm)] if incluir_chm else [])
    resultados = []
    for nome, buscar in etapas:
        try:
            resultados.extend(buscar(cliente, hashes, agora, salvar))
        except (httpx.HTTPError, ValueError) as erro:  # 403 do CHM, layout mudou...
            resultados.append({"fonte": nome, "status": "falha", "erro": repr(erro)[:300]})
    return resultados


def main() -> None:
    armazem = Local()  # ensaio em dados/; a coleta de verdade é o coletor.rodar
    hashes = ler_estado(armazem, CHAVE_HASHES)
    with criar_cliente() as cliente:
        resultados = buscar_avisos(cliente, hashes, datetime.now(UTC), armazem.salvar)
    salvar_estado(armazem, CHAVE_HASHES, hashes)
    for r in resultados:
        detalhe = r.get("chave") or r.get("erro") or ""
        print(f"{r['fonte']:12} {r['status']:12} {r.get('bytes', 0):>9} B  {detalhe}")


if __name__ == "__main__":
    main()
