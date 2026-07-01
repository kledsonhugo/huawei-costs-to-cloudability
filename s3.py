"""
FunctionGraph s3: Lê os arquivos de custos (CSV) e metadados (JSON)
do bucket OBS de destino (Huawei) e os envia para um bucket S3 na AWS.

Pré-requisitos:
  - Certificado e chave privada para autenticação no Vault (montados via CERT_DIR)
  - CA bundle para validação TLS
  - Variáveis de ambiente:
      OBS_ENDPOINT       - Endpoint do OBS
      OBS_SOURCE_BUCKET  - Bucket OBS de origem
      S3_BUCKET          - Bucket S3 de destino na AWS
      VAULT_URL          - URL do endpoint do Vault para login
      VAULT_ROLE         - Role do Vault para obter credenciais AWS
      VAULT_AWS_PATH     - Path do Vault para creds AWS
      AWS_REGION         - Região AWS
"""

import os
import json
import traceback
import logging
import sys

from datetime import datetime, timezone
from obs import ObsClient, GetObjectHeader
# import boto3
# from botocore.exceptions import NoCredentialsError, ClientError
import requests
from requests import packages
from urllib3 import disable_warnings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
disable_warnings(packages.urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Caminhos dos certificados (configurável via variável de ambiente)
# ---------------------------------------------------------------------------
CERT_DIR = os.getenv("CERT_DIR", "/opt/function/code/certificado")
CERT_FILE = os.path.join(CERT_DIR, "cert1.pem")
KEY_FILE = os.path.join(CERT_DIR, "key.pem")
CA_BUNDLE = os.path.join(CERT_DIR, "ca_bundle.crt")

# =========================
# OBS: Create client
# Cria cliente OBS com credenciais temporárias.
# =========================
def create_obs_client(context, endpoint):

    logger.info("Criando cliente OBS")

    ak = context.getSecurityAccessKey()
    sk = context.getSecuritySecretKey()
    tk = context.getSecurityToken()

    if not ak or not sk or not tk:
        raise Exception("Credenciais OBS temporárias não disponíveis no contexto")

    return ObsClient(
        access_key_id=ak,
        secret_access_key=sk,
        security_token=tk,
        server=endpoint
    )

# =========================
# OBS: List objects
# Lista objetos no bucket OBS, opcionalmente filtrados por prefix.
# Retorna lista de dicts com 'key' e 'size'.
# =========================
def list_obs_objects(obs_client, bucket, prefix=None):

    logger.info(f"Listando objetos - bucket={bucket}, prefix={prefix}")

    resp = obs_client.listObjects(bucket, prefix=prefix)

    logger.info(f"Status da listagem: {resp.status}")

    if resp.status >= 300 or not resp.body.contents:
        raise Exception(
            f"Erro ao listar objetos - "
            f"status={resp.status}, bucket={bucket}, prefix={prefix}"
        )

    objects = []
    for content in resp.body.contents:
        if content.size == 0:
            continue
        objects.append({
            "key": content.key,
            "size": content.size,
            "lastModified": content.lastModified
        })

    logger.info(f"Total de objetos válidos: {len(objects)}")

    if not objects:
        raise Exception(
            f"Nenhum objeto encontrado - bucket={bucket}, prefix={prefix}"
        )

    return objects

# =========================
# OBS: Find latest CSV and JSON pair
# Dada uma lista de objetos, encontra o par CSV+JSON mais recente.
# Os arquivos seguem o padrão: Huawei/yyyy/mm/dd/yyyymmdd.csv
#                                                     /yyyymmdd.json
# Retorna dict com 'csv_key', 'json_key', 'date_str'.
# =========================
def find_latest_artifact_pair(objects):

    logger.info("Procurando par CSV+JSON mais recente")

    csv_files = [o for o in objects if o["key"].endswith(".csv")]
    json_files = {o["key"] for o in objects if o["key"].endswith(".json")}

    if not csv_files:
        raise Exception("Nenhum arquivo CSV encontrado no bucket OBS")

    # Ordena CSVs pela key (contém yyyymmdd) decrescente
    csv_files_sorted = sorted(csv_files, key=lambda x: x["key"], reverse=True)

    for csv_obj in csv_files_sorted:
        csv_key = csv_obj["key"]
        json_key = csv_key.replace(".csv", ".json")

        if json_key in json_files:
            # Extrai o date_str (yyyymmdd) da key
            # Formato: Huawei/yyyy/mm/dd/yyyymmdd.csv
            parts = csv_key.split("/")
            date_str = parts[-1].replace(".csv", "")
            logger.info(f"Par válido encontrado - date_str={date_str}")
            return {
                "csv_key": csv_key,
                "json_key": json_key,
                "date_str": date_str
            }

    raise Exception("Nenhum par CSV+JSON encontrado no bucket OBS")

# =========================
# OBS: Download file
# Faz download de um objeto do OBS para o caminho local.
# =========================
def download_from_obs(obs_client, bucket, key, local_path):

    logger.info(f"Download OBS: bucket={bucket}, key={key}")

    try:
        resp = obs_client.getObject(bucket, key, downloadPath=local_path)
    except Exception as sdk_err:
        raise Exception(
            f"Exceção do SDK no download OBS - "
            f"bucket={bucket}, key={key}"
        ) from sdk_err

    if resp.status >= 300:
        raise Exception(
            f"Erro no download OBS - "
            f"status={resp.status}, "
            f"bucket={bucket}, key={key}"
        )

    if os.path.exists(local_path):
        file_size = os.path.getsize(local_path)
        logger.info(f"Download OK: {key} ({file_size} bytes)")
    else:
        raise Exception(f"Arquivo não foi criado após download: {local_path}")

# =========================
# Vault: Authenticate with certificate
# Autentica no HashiCorp Vault usando autenticação por certificado TLS.
# Retorna o client_token do Vault.
# =========================
# def vault_login(vault_url, cert_file, key_file, ca_bundle):
#
#     logger.info("Autenticando no Vault")
#
#     try:
#         resp = requests.post(
#             vault_url,
#             cert=(cert_file, key_file),
#             verify=ca_bundle,
#             json={"name": "cert1"}
#         )
#     except Exception as req_err:
#         raise Exception(f"Erro na requisição de login ao Vault") from req_err
#
#     if resp.status_code != 200:
#         raise Exception(
#             f"Erro no login Vault - status={resp.status_code}"
#         )
#
#     try:
#         data = resp.json()
#     except Exception as json_err:
#         raise Exception(f"Erro ao fazer parse da resposta Vault") from json_err
#
#     client_token = data.get("auth", {}).get("client_token")
#
#     if not client_token:
#         raise Exception("client_token não encontrado na resposta do Vault")
#
#     logger.info("Vault login OK")
#     return client_token

# =========================
# Vault: Get AWS credentials
# Obtém credenciais AWS temporárias do Vault.
# Retorna dict com 'AccessKey', 'SecretKey', 'SecurityToken'.
# =========================
# def get_aws_credentials_from_vault(vault_aws_path, client_token, ca_bundle):
#
#     logger.info(f"Obtendo credenciais AWS do Vault: path={vault_aws_path}")
#
#     try:
#         resp = requests.post(
#             vault_aws_path,
#             headers={"X-Vault-Token": client_token},
#             verify=ca_bundle
#         )
#     except Exception as req_err:
#         raise Exception(f"Erro na requisição de creds AWS ao Vault") from req_err
#
#     if resp.status_code != 200:
#         raise Exception(
#             f"Erro ao obter creds AWS - status={resp.status_code}"
#         )
#
#     try:
#         data = resp.json()
#     except Exception as json_err:
#         raise Exception(f"Erro ao fazer parse da resposta AWS Vault") from json_err
#
#     creds = data.get("data", {})
#
#     access_key = creds.get("access_key")
#     secret_key = creds.get("secret_key")
#     security_token = creds.get("security_token")
#
#     if not access_key or not secret_key:
#         raise Exception("Credenciais AWS incompletas na resposta do Vault")
#
#     logger.info("Credenciais AWS obtidas com sucesso")
#     return {
#         "AccessKey": access_key,
#         "SecretKey": secret_key,
#         "SecurityToken": security_token
#     }

# =========================
# AWS S3: Create client
# Cria cliente boto3 S3 com credenciais do Vault.
# =========================
# def create_s3_client(aws_creds, region):
#
#     logger.info(f"Criando cliente S3 para região {region}")
#
#     try:
#         session = boto3.Session(
#             aws_access_key_id=aws_creds["AccessKey"],
#             aws_secret_access_key=aws_creds["SecretKey"],
#             aws_session_token=aws_creds["SecurityToken"],
#             region_name=region
#         )
#         client = session.client("s3")
#         logger.info("Cliente S3 criado com sucesso")
#         return client
#     except Exception as e:
#         raise

# =========================
# AWS S3: Check if object exists
# Verifica se um objeto já existe no S3.
# =========================
# def s3_object_exists(s3_client, bucket, key):
#
#     logger.info(f"Verificando existência do objeto S3: {key}")
#
#     try:
#         s3_client.head_object(Bucket=bucket, Key=key)
#         logger.info(f"Objeto encontrado: {key}")
#         return True
#     except ClientError as e:
#         error_code = e.response["Error"]["Code"]
#         if error_code == "404":
#             return False
#         raise
#     except Exception as e:
#         logger.info(f"Exceção ao verificar existência: {e}")
#         return False

# =========================
# AWS S3: Upload file
# Faz upload de um arquivo local para o S3.
# =========================
# def upload_to_s3(s3_client, bucket, key, local_path):
#
#     if not os.path.exists(local_path):
#         raise Exception(f"Arquivo não existe: {local_path}")
#
#     file_size = os.path.getsize(local_path)
#     logger.info(f"Upload S3: bucket={bucket}, key={key}, size={file_size}")
#
#     try:
#         s3_client.upload_file(local_path, bucket, key)
#         logger.info(f"Upload S3 OK: {key}")
#     except NoCredentialsError:
#         raise Exception("Credenciais AWS não encontradas para upload S3")
#     except ClientError as s3_err:
#         raise Exception(
#             f"Erro ClientError no upload S3 - bucket={bucket}, key={key}"
#         ) from s3_err
#     except Exception as e:
#         raise Exception(
#             f"Erro inesperado no upload S3 - bucket={bucket}, key={key}"
#         ) from e

# =========================
# HANDLER
# 1. Lista objetos no bucket OBS
# 2. Encontra o par CSV+JSON mais recente
# 3. Faz download dos arquivos
# 4. Autentica no Vault via certificado
# 5. Obtém credenciais AWS do Vault
# 6. Envia CSV e JSON para o bucket S3
# =========================
def handler(event, context):

    logger.info("INICIANDO FUNCTIONGRAPH S3")

    obs_client = None

    try:
        # --- Variáveis de ambiente ---
        obs_endpoint = os.getenv("OBS_ENDPOINT")
        obs_source_bucket = os.getenv("OBS_SOURCE_BUCKET")
        s3_bucket = os.getenv("S3_BUCKET")
        vault_url = os.getenv("VAULT_URL")
        vault_aws_path = os.getenv("VAULT_AWS_PATH")
        aws_region = os.getenv("AWS_REGION", "sa-east-1")

        # --- Validação ---
        missing = []
        if not obs_endpoint:
            missing.append("OBS_ENDPOINT")
        if not obs_source_bucket:
            missing.append("OBS_SOURCE_BUCKET")
        if not s3_bucket:
            missing.append("S3_BUCKET")
        if not vault_url:
            missing.append("VAULT_URL")
        if not vault_aws_path:
            missing.append("VAULT_AWS_PATH")

        if missing:
            raise Exception(f"Variáveis de ambiente obrigatórias ausentes: {missing}")

        logger.info("Variáveis de ambiente validadas com sucesso")

        # 1. Cria cliente OBS ---
        obs_client = create_obs_client(context, obs_endpoint)

        # 2. Lista objetos no bucket OBS ---
        logger.info(f"Listando objetos no bucket OBS: {obs_source_bucket}")
        objects = list_obs_objects(obs_client, obs_source_bucket, prefix="Huawei/")

        # 3. Encontra par CSV+JSON mais recente ---
        artifact = find_latest_artifact_pair(objects)
        logger.info(f"Par encontrado - CSV: {artifact['csv_key']}, JSON: {artifact['json_key']}")

        # 4. Download dos arquivos do OBS ---
        csv_local = f"/tmp/{artifact['date_str']}.csv"
        json_local = f"/tmp/{artifact['date_str']}.json"

        download_from_obs(obs_client, obs_source_bucket, artifact["csv_key"], csv_local)
        download_from_obs(obs_client, obs_source_bucket, artifact["json_key"], json_local)

        # 5. Autentica no Vault ---
        # client_token = vault_login(vault_url, CERT_FILE, KEY_FILE, CA_BUNDLE)

        # 6. Obtém credenciais AWS do Vault ---
        # aws_creds = get_aws_credentials_from_vault(vault_aws_path, client_token, CA_BUNDLE)

        # 7. Cria cliente S3 ---
        # s3_client = create_s3_client(aws_creds, aws_region)

        # 8. Verifica se CSV já existe no S3 ---
        # csv_exists = s3_object_exists(s3_client, s3_bucket, artifact["csv_key"])
        #
        # if csv_exists:
        #     result = {
        #         "status": "exists",
        #         "csv_key": artifact["csv_key"],
        #         "json_key": artifact["json_key"]
        #     }
        # else:
        #     # 9. Upload CSV e JSON para S3 ---
        #     upload_to_s3(s3_client, s3_bucket, artifact["csv_key"], csv_local)
        #     upload_to_s3(s3_client, s3_bucket, artifact["json_key"], json_local)
        #
        #     result = {
        #         "status": "uploaded",
        #         "csv_key": artifact["csv_key"],
        #         "json_key": artifact["json_key"]
        #     }
        #
        #     logger.info(
        #         f"Upload concluído - CSV: {artifact['csv_key']}, "
        #         f"JSON: {artifact['json_key']}"
        #     )

        # 10. Limpa arquivos temporários ---
        for f in [csv_local, json_local]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass

        logger.info("Todas as etapas concluídas com sucesso")

        return {
            "statusCode": 200,
            "isBase64Encoded": False,
            "body": json.dumps({
                "message": "Arquivos processados com sucesso",
                # "result": result
            }),
            "headers": {
                "Content-Type": "application/json"
            }
        }

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Erro: {str(e)}")
        logger.error(f"Traceback: {tb}")

        return {
            "statusCode": 500,
            "isBase64Encoded": False,
            "body": json.dumps({
                "error": str(e)
            }),
            "headers": {
                "Content-Type": "application/json"
            }
        }

    finally:
        if obs_client:
            obs_client.close()
