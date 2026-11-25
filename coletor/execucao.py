"""O que acontece em cada execução: detectar mudança (hoje) e registrar o log (sex 27)."""

import hashlib

CHAVE_HASHES = "estado/hashes.json"


def sha256_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def mudou(fonte: str, texto: str, hashes: dict[str, str]) -> bool:
    """O texto já normalizado é diferente do último guardado para esta fonte?

    Não altera `hashes`: quem chama só atualiza depois de gravar o bruto. Se a gravação
    falhar, o hash antigo fica e a próxima execução tenta de novo.
    """
    return hashes.get(fonte) != sha256_texto(texto)
