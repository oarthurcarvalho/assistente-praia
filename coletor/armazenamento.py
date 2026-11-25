"""Onde o bruto mora. Por enquanto em disco; o R2 entra na sex 27 com a mesma chave.

Layout (seção 6.2 do plano):  raw/<fonte>/dt=AAAA-MM-DD/HHMMSS.<extensao>
"""

import gzip
import json
from datetime import datetime
from pathlib import Path

RAIZ_LOCAL = Path("dados")


def chave_bruto(fonte: str, coletado_em: datetime, extensao: str = "json.gz") -> str:
    """Partição por dia em UTC (dt=) e nome pela hora da coleta, nunca a hora do cron."""
    return f"raw/{fonte}/dt={coletado_em:%Y-%m-%d}/{coletado_em:%H%M%S}.{extensao}"


def json_gz(dados: dict) -> bytes:
    return gzip.compress(json.dumps(dados, ensure_ascii=False).encode("utf-8"))


def salvar_local(chave: str, conteudo: bytes, raiz: Path = RAIZ_LOCAL) -> Path:
    caminho = raiz / chave
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(conteudo)
    return caminho


# Estado entre execuções (ex.: estado/hashes.json). O runner apaga o disco no fim,
# então na sex 27 ele passa a morar no R2; aqui a interface já é a mesma.
def ler_estado(chave: str, raiz: Path = RAIZ_LOCAL) -> dict:
    caminho = raiz / chave
    if not caminho.exists():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8"))


def salvar_estado(chave: str, dados: dict, raiz: Path = RAIZ_LOCAL) -> Path:
    texto = json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True)
    return salvar_local(chave, texto.encode("utf-8"), raiz)
