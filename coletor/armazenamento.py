"""Onde o bruto mora: no R2 (produção) ou em disco (testes e ensaio local), mesma chave.

Layout (seção 6.2 do plano):
    raw/<fonte>/dt=AAAA-MM-DD/HHMMSS.<extensao>    bruto imutável, sempre .gz
    execucoes/dt=AAAA-MM-DD/HHMMSS.json             1 log por execução
    estado/hashes.json                              último hash de cada página
"""

import gzip
import json
import os
from datetime import datetime
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

RAIZ_LOCAL = Path("dados")


def chave_bruto(fonte: str, coletado_em: datetime, extensao: str = "json.gz") -> str:
    """Partição por dia em UTC (dt=) e nome pela hora da coleta, nunca a hora do cron."""
    return f"raw/{fonte}/dt={coletado_em:%Y-%m-%d}/{coletado_em:%H%M%S}.{extensao}"


def json_gz(dados: dict) -> bytes:
    return gzip.compress(json.dumps(dados, ensure_ascii=False).encode("utf-8"))


class Local:
    """Pasta no disco (dados/, fora do git). O runner apaga tudo no fim: só para ensaio."""

    def __init__(self, raiz: Path = RAIZ_LOCAL):
        self.raiz = Path(raiz)

    def salvar(self, chave: str, conteudo: bytes) -> None:
        caminho = self.raiz / chave
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)

    def ler(self, chave: str) -> bytes | None:
        caminho = self.raiz / chave
        return caminho.read_bytes() if caminho.exists() else None


class R2:
    """Bucket no Cloudflare R2, pela API do S3. O token só enxerga o praia-raw."""

    def __init__(self, cliente, bucket: str):
        self.cliente = cliente
        self.bucket = bucket

    def salvar(self, chave: str, conteudo: bytes) -> None:
        self.cliente.put_object(Bucket=self.bucket, Key=chave, Body=conteudo)

    def ler(self, chave: str) -> bytes | None:
        try:
            objeto = self.cliente.get_object(Bucket=self.bucket, Key=chave)
        except ClientError as erro:
            if erro.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return None  # 1ª execução: ainda não há estado
            raise  # sem permissão, bucket errado...: isso é falha, não "vazio"
        return objeto["Body"].read()


def r2_do_ambiente() -> R2:
    """Chaves vindas do ambiente: Secrets/Variables no Actions, .env no PC."""
    cliente = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",  # o R2 não tem região; o boto3 exige uma
    )
    return R2(cliente, os.environ["R2_BUCKET"])


# Estado entre execuções. Lido no começo e gravado no fim de cada execução; o
# concurrency do workflow garante que duas execuções não disputem o mesmo arquivo.
def ler_estado(armazem, chave: str) -> dict:
    conteudo = armazem.ler(chave)
    return json.loads(conteudo) if conteudo is not None else {}


def salvar_estado(armazem, chave: str, dados: dict) -> None:
    texto = json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True)
    armazem.salvar(chave, texto.encode("utf-8"))
