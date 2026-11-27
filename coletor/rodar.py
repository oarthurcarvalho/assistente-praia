"""Uma execução completa do coletor: cada fonte no seu try, 1 log, saída ≠ 0 se algo falhou.

    python -m coletor.rodar             # coleta o que está vencido; grava no R2
    python -m coletor.rodar --todas     # tudo agora (workflow_dispatch e teste manual)
    python -m coletor.rodar --local     # grava em dados/ em vez do R2

Toda execução: Open-Meteo (4) e boletins da OMM. Página do CHM: vence às 09h e 21h UTC.
Boia e marégrafo: vencem às 09h UTC (06h no Rio). Fonte vencida que falha continua
vencida, e a execução seguinte tenta de novo.
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx
from dotenv import load_dotenv

from coletor.armazenamento import (
    Local,
    chave_bruto,
    json_gz,
    ler_estado,
    r2_do_ambiente,
    salvar_estado,
)
from coletor.avisos import buscar_avisos
from coletor.execucao import CHAVE_HASHES, registrar_execucao
from coletor.observacoes import JANELA, buscar_boia, buscar_ioc
from coletor.openmeteo import TIPOS, buscar_openmeteo
from coletor.praias import carregar_praias
from coletor.rede import criar_cliente

# Fonte que não vai em toda execução -> horários (UTC) em que ela vence.
PRAZOS = {"avisos_chm": (9, 21), "boia_rj4": (9,), "ioc_niteroi": (9,)}
CHAVE_ULTIMAS = "estado/ultimas_coletas.json"  # fonte -> início da última coleta que deu certo
SOBRA = timedelta(hours=2)
JANELA_MAXIMA = timedelta(days=7)


def agora_utc() -> datetime:
    return datetime.now(UTC)


def ultimo_prazo(agora: datetime, horas: tuple[int, ...]) -> datetime:
    """O horário marcado mais recente que já passou: hoje ou ontem."""
    marcas = [
        (agora - timedelta(days=dias)).replace(hour=h, minute=0, second=0, microsecond=0)
        for dias in (0, 1)
        for h in horas
    ]
    return max(m for m in marcas if m <= agora)


def vencidas(agora: datetime, ultimas: dict[str, str], todas: bool = False) -> set[str]:
    """Fontes cujo prazo passou desde a última coleta que deu certo.

    Não depende de qual execução do cron é esta: o GitHub atrasa horas e às vezes
    descarta execuções. A primeira que rodar depois das 09h UTC coleta a boia, seja
    a das 09:23, a das 12:23 ou uma atrasada da madrugada.
    """
    return {
        fonte
        for fonte, horas in PRAZOS.items()
        if todas
        or fonte not in ultimas
        or datetime.fromisoformat(ultimas[fonte]) < ultimo_prazo(agora, horas)
    }


def janela(agora: datetime, ultima: str | None) -> timedelta:
    """Desde a última coleta boa (com 2 h de sobra), nunca menos que 26 h nem mais que 7 dias.

    Se a boia falhar 3 dias seguidos, a coleta que voltar busca os 3 dias: sem buraco.
    """
    if ultima is None:
        return JANELA
    desde = agora - datetime.fromisoformat(ultima) + SOBRA
    return min(max(desde, JANELA), JANELA_MAXIMA)


def guardar_envelope(envelope: dict, agora: datetime, salvar) -> dict:
    """Envelope inteiro em .json.gz, na chave da hora em que foi coletado."""
    conteudo = json_gz(envelope)
    chave = chave_bruto(envelope["fonte"], agora)
    salvar(chave, conteudo)
    resposta = envelope["resposta"]
    registros = len(resposta) if isinstance(resposta, list) else None
    return {
        "fonte": envelope["fonte"],
        "status": "ok",
        "bytes": len(conteudo),
        "registros": registros,  # 8 praias, 49 leituras da boia...: 0 vai chamar atenção
        "chave": chave,
    }


def tentar(fonte: str, etapa) -> list[dict]:
    """Roda uma fonte isolada: qualquer erro vira uma linha "falha" no log e segue.

    Pegar Exception aqui é de propósito: um bug numa fonte não pode impedir as outras de
    gravar. A falha não some: vai para o log e faz a execução terminar com código 1.
    """
    inicio = time.perf_counter()
    try:
        resultado = etapa()
        resultados = resultado if isinstance(resultado, list) else [resultado]
    except Exception as erro:
        resultados = [{"fonte": fonte, "status": "falha", "erro": repr(erro)[:300]}]
    ms = round((time.perf_counter() - inicio) * 1000)
    return [{**r, "ms": ms} for r in resultados]


def rodar(cliente: httpx.Client, armazem, todas: bool = False, relogio=agora_utc):
    """Coleta o que está vencido e grava bruto, estado e log. Devolve (resultados, log)."""
    inicio = relogio()
    hashes = ler_estado(armazem, CHAVE_HASHES)  # sem estado (R2 fora) não vale continuar
    ultimas = ler_estado(armazem, CHAVE_ULTIMAS)
    pendentes = vencidas(inicio, ultimas, todas)
    praias = carregar_praias()

    def openmeteo(tipo):
        agora = relogio()  # coletado_em é a hora de cada pedido, não a do começo
        envelope = buscar_openmeteo(tipo, praias, cliente, agora)
        return guardar_envelope(envelope, agora, armazem.salvar)

    def observacao(fonte, buscar):
        agora = relogio()
        envelope = buscar(cliente, agora, janela(agora, ultimas.get(fonte)))
        return guardar_envelope(envelope, agora, armazem.salvar)

    resultados = []
    for tipo in TIPOS:
        resultados += tentar(f"openmeteo_{tipo}", lambda tipo=tipo: openmeteo(tipo))
    resultados += tentar(
        "avisos",
        lambda: buscar_avisos(
            cliente, hashes, relogio(), armazem.salvar, incluir_chm="avisos_chm" in pendentes
        ),
    )
    for fonte, buscar in (("boia_rj4", buscar_boia), ("ioc_niteroi", buscar_ioc)):
        if fonte in pendentes:
            resultados += tentar(fonte, lambda f=fonte, b=buscar: observacao(f, b))

    # Só a fonte que deu certo sai da fila; a que falhou fica vencida para a próxima.
    for r in resultados:
        if r["fonte"] in PRAZOS and r["status"] in ("ok", "sem_mudanca"):
            ultimas[r["fonte"]] = inicio.isoformat()

    # Estado só no fim, depois de todo bruto gravado (mesma regra do hash na quinta).
    try:
        salvar_estado(armazem, CHAVE_HASHES, hashes)
        salvar_estado(armazem, CHAVE_ULTIMAS, ultimas)
    except Exception as erro:
        resultados.append({"fonte": "estado", "status": "falha", "erro": repr(erro)[:300]})
    chave_log = registrar_execucao(resultados, inicio, relogio(), armazem.salvar)
    return resultados, chave_log


def codigo_de_saida(resultados: list[dict]) -> int:
    """≠ 0 se alguma fonte falhou: é o que deixa a execução vermelha no Actions."""
    return 1 if any(r["status"] == "falha" for r in resultados) else 0


def tabela_markdown(resultados: list[dict], chave_log: str) -> str:
    """Resumo para o Step Summary do Actions: dá para ver a execução sem abrir o log."""
    linhas = ["| fonte | status | KB | ms | detalhe |", "|---|---|--:|--:|---|"]
    for r in resultados:
        detalhe = r.get("erro") or r.get("chave") or ""
        kb = f"{r.get('bytes', 0) / 1024:.1f}"
        linhas.append(f"| {r['fonte']} | {r['status']} | {kb} | {r.get('ms', 0)} | `{detalhe}` |")
    return "\n".join(linhas) + f"\n\nlog: `{chave_log}`\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coletor.rodar")
    parser.add_argument("--todas", action="store_true", help="coleta tudo, vencido ou não")
    parser.add_argument("--local", action="store_true", help="grava em dados/ e não no R2")
    args = parser.parse_args(argv)

    load_dotenv()  # no PC lê o .env; no Actions não há .env e valem os Secrets
    armazem = Local() if args.local else r2_do_ambiente()
    with criar_cliente() as cliente:
        resultados, chave_log = rodar(cliente, armazem, todas=args.todas)

    for r in resultados:
        detalhe = r.get("chave") or r.get("erro") or ""
        tamanho = f"{r.get('bytes', 0):>9} B {r.get('ms', 0):>6} ms"
        print(f"{r['fonte']:20} {r['status']:12} {tamanho}  {detalhe}")
    print(f"log: {chave_log}")

    resumo = os.environ.get("GITHUB_STEP_SUMMARY")  # só existe dentro do Actions
    if resumo:
        with open(resumo, "a", encoding="utf-8") as arquivo:
            arquivo.write(tabela_markdown(resultados, chave_log))
    return codigo_de_saida(resultados)


if __name__ == "__main__":
    sys.exit(main())
