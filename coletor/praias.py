"""Lê o praias.toml, a tabela que todas as fontes usam (Open-Meteo, INEA)."""

import tomllib
from pathlib import Path

ARQUIVO = Path(__file__).with_name("praias.toml")


def carregar_praias(caminho: Path = ARQUIVO) -> list[dict]:
    """Uma praia por item, na ordem do arquivo, com a chave da tabela em "chave"."""
    with open(caminho, "rb") as arquivo:  # tomllib exige modo binário
        tabela = tomllib.load(arquivo)
    return [{"chave": chave, **dados} for chave, dados in tabela.items()]
