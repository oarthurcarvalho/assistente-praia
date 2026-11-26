"""O que acontece em cada execução: detectar mudança e registrar o log."""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime

CHAVE_HASHES = "estado/hashes.json"


def sha256_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def mudou(fonte: str, texto: str, hashes: dict[str, str]) -> bool:
    """O texto já normalizado é diferente do último guardado para esta fonte?

    Não altera `hashes`: quem chama só atualiza depois de gravar o bruto. Se a gravação
    falhar, o hash antigo fica e a próxima execução tenta de novo.
    """
    return hashes.get(fonte) != sha256_texto(texto)


def chave_execucao(inicio: datetime) -> str:
    return f"execucoes/dt={inicio:%Y-%m-%d}/{inicio:%H%M%S}.json"


def registrar_execucao(resultados: list[dict], inicio: datetime, fim: datetime, salvar) -> str:
    """Grava 1 log por execução: é dele que sai a cobertura da semana 2.

    Do ambiente só entram o tipo de disparo e o id da execução, nunca chaves.
    """
    log = {
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "duracao_s": round((fim - inicio).total_seconds(), 1),
        "disparo": os.environ.get("GITHUB_EVENT_NAME", "local"),  # schedule, workflow_dispatch
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "contagem": dict(Counter(r["status"] for r in resultados)),
        "fontes": resultados,
    }
    chave = chave_execucao(inicio)
    salvar(chave, json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    return chave
