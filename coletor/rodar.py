"""Uma execução completa do coletor: cada fonte no seu try, 1 log, saída ≠ 0 se algo falhou.

    python -m coletor.rodar             # o horário (UTC) decide o que coletar; grava no R2
    python -m coletor.rodar --todas     # tudo agora (workflow_dispatch e teste manual)
    python -m coletor.rodar --local     # grava em dados/ em vez do R2

A cada 3 h: Open-Meteo (4) e boletins da OMM. Às 09h e 21h UTC: página do CHM.
Às 09h UTC (06h no Rio): boia e marégrafo, com janela de 26 h.
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime

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
from coletor.observacoes import buscar_boia, buscar_ioc
from coletor.openmeteo import TIPOS, buscar_openmeteo
from coletor.praias import carregar_praias
from coletor.rede import criar_cliente

BLOCOS_CHM = {9, 21}
BLOCO_DIARIAS = 9


def agora_utc() -> datetime:
    return datetime.now(UTC)


def bloco_do_horario(agora: datetime) -> int:
    """A que execução do cron (00, 03, ..., 21 UTC) este horário pertence.

    O cron "23 */3" atrasa: a das 09:23 que só começar às 11:40 ainda é do bloco 9 e
    ainda coleta as diárias. Pelo relógio exato (hour == 9) ela perderia o dia.
    """
    return agora.hour // 3 * 3


def o_que_coletar(agora: datetime, todas: bool = False) -> dict[str, bool]:
    bloco = bloco_do_horario(agora)
    return {
        "chm": todas or bloco in BLOCOS_CHM,
        "diarias": todas or bloco == BLOCO_DIARIAS,
    }


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
    """Coleta o que é deste horário e grava bruto, estado e log. Devolve (resultados, log)."""
    inicio = relogio()
    plano = o_que_coletar(inicio, todas)
    hashes = ler_estado(armazem, CHAVE_HASHES)  # sem estado (R2 fora) não vale continuar
    praias = carregar_praias()

    def openmeteo(tipo):
        agora = relogio()  # coletado_em é a hora de cada pedido, não a do começo
        envelope = buscar_openmeteo(tipo, praias, cliente, agora)
        return guardar_envelope(envelope, agora, armazem.salvar)

    def observacao(buscar):
        agora = relogio()
        return guardar_envelope(buscar(cliente, agora), agora, armazem.salvar)

    resultados = []
    for tipo in TIPOS:
        resultados += tentar(f"openmeteo_{tipo}", lambda tipo=tipo: openmeteo(tipo))
    resultados += tentar(
        "avisos",
        lambda: buscar_avisos(cliente, hashes, relogio(), armazem.salvar, incluir_chm=plano["chm"]),
    )
    if plano["diarias"]:
        resultados += tentar("boia_rj4", lambda: observacao(buscar_boia))
        resultados += tentar("ioc_niteroi", lambda: observacao(buscar_ioc))

    # Estado só no fim, depois de todo bruto gravado (mesma regra do hash na quinta).
    try:
        salvar_estado(armazem, CHAVE_HASHES, hashes)
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
    parser.add_argument("--todas", action="store_true", help="coleta tudo, ignora o horário")
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
