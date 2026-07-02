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
import uuid
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
# Logging com identificador único de execução (correlation ID)
# ---------------------------------------------------------------------------
_exec_id = None


class ExecIdFilter(logging.Filter):
    """Injeta o exec_id (correlation ID) em cada registro de log."""

    def filter(self, record):
        record.exec_id = _exec_id or "-"
        return True


_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s | %(levelname)-5s | exec_id=%(exec_id)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    )
)
_log_handler.addFilter(ExecIdFilter())

logging.basicConfig(level=logging.INFO, handlers=[_log_handler])
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

    logger.info(f"[OBS] Criando cliente OBS - endpoint={endpoint}")

    ak = context.getSecurityAccessKey()
    sk = context.getSecuritySecretKey()
    tk = context.getSecurityToken()

    if not ak or not sk or not tk:
        logger.error("[OBS] Credenciais OBS temporárias não disponíveis no contexto")
        raise Exception("Credenciais OBS temporárias não disponíveis no contexto")

    client = ObsClient(
        access_key_id=ak,
        secret_access_key=sk,
        security_token=tk,
        server=endpoint
    )
    logger.info("[OBS] Cliente OBS criado com sucesso")
    return client

# =========================
# OBS: List objects
# Lista objetos no bucket OBS, opcionalmente filtrados por prefix.
# Retorna lista de dicts com 'key' e 'size'.
# =========================
def list_obs_objects(obs_client, bucket, prefix=None):

    logger.info(f"[LIST] Listando objetos - bucket={bucket}, prefix={prefix}")

    resp = obs_client.listObjects(bucket, prefix=prefix)

    logger.info(f"[LIST] Status da listagem: {resp.status}")

    if resp.status >= 300 or not resp.body.contents:
        logger.error(f"[LIST] Falha ao listar objetos - status={resp.status}, bucket={bucket}, prefix={prefix}")
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

    logger.info(f"[LIST] Total de objetos válidos: {len(objects)}")

    if not objects:
        raise Exception(
            f"Nenhum objeto encontrado - bucket={bucket}, prefix={prefix}"
        )

    return objects

# =========================
# OBS: Find latest CSV and JSON pair
# Dada uma lista de objetos, encontra o par CSV+JSON mais recente.
# Os arquivos seguem o padrão:
#   Huawei/<report_period>/<epoch>/<epoch>.csv
#   Huawei/<report_period>/<epoch>/<epoch>-Manifest.json
#     report_period = YYYYMM01-YYYY(M+1)01
#     epoch         = epoch time do upload
# Retorna dict com 'csv_key', 'json_key', 'epoch'.
# =========================
def find_latest_artifact_pair(objects):

    logger.info(f"[FIND_PAIR] Procurando par CSV+JSON mais recente de {len(objects)} objetos")

    csv_files = [o for o in objects if o["key"].endswith(".csv")]
    manifest_files = {o["key"] for o in objects if o["key"].endswith("-Manifest.json")}

    logger.info(f"[FIND_PAIR] CSVs encontrados: {len(csv_files)}, Manifests encontrados: {len(manifest_files)}")

    if not csv_files:
        raise Exception("Nenhum arquivo CSV encontrado no bucket OBS")

    # Ordena CSVs pela key decrescente (contém epoch no path)
    csv_files_sorted = sorted(csv_files, key=lambda x: x["key"], reverse=True)

    for csv_obj in csv_files_sorted:
        csv_key = csv_obj["key"]
        # CSV: .../<epoch>.csv  ->  JSON: .../<epoch>-Manifest.json
        manifest_key = csv_key.replace(".csv", "-Manifest.json")

        if manifest_key in manifest_files:
            # Extrai o epoch do nome do arquivo
            # Formato: Huawei/<report_period>/<epoch>/<epoch>.csv
            parts = csv_key.split("/")
            epoch = parts[-1].replace(".csv", "")
            logger.info(f"[FIND_PAIR] Par válido encontrado - epoch={epoch}")
            return {
                "csv_key": csv_key,
                "json_key": manifest_key,
                "epoch": epoch
            }

    logger.error("[FIND_PAIR] Nenhum par CSV+Manifest encontrado no bucket OBS")
    raise Exception("Nenhum par CSV+Manifest encontrado no bucket OBS")

# =========================
# OBS: Download file
# Faz download de um objeto do OBS para o caminho local.
# =========================
def download_from_obs(obs_client, bucket, key, local_path):

    logger.info(f"[DOWNLOAD] Iniciando - bucket={bucket}, key={key}, local_path={local_path}")

    try:
        resp = obs_client.getObject(bucket, key, downloadPath=local_path)
    except Exception as sdk_err:
        logger.error(f"[DOWNLOAD] Exceção do SDK - bucket={bucket}, key={key}, erro={sdk_err}")
        raise Exception(
            f"Exceção do SDK no download OBS - "
            f"bucket={bucket}, key={key}"
        ) from sdk_err

    if resp.status >= 300:
        logger.error(f"[DOWNLOAD] Falha - status={resp.status}, bucket={bucket}, key={key}")
        raise Exception(
            f"Erro no download OBS - "
            f"status={resp.status}, "
            f"bucket={bucket}, key={key}"
        )

    if os.path.exists(local_path):
        file_size = os.path.getsize(local_path)
        logger.info(f"[DOWNLOAD] Concluído - key={key}, size={file_size} bytes")
    else:
        logger.error(f"[DOWNLOAD] Arquivo não criado após download - local_path={local_path}")
        raise Exception(f"Arquivo não foi criado após download: {local_path}")

# =========================
# Vault: Authenticate with certificate
# Autentica no HashiCorp Vault usando autenticação por certificado TLS.
# Retorna o client_token do Vault.
# =========================
# def vault_login(vault_url, cert_file, key_file, ca_bundle):
#
#     logger.info(f"[VAULT] Autenticando no Vault - url={vault_url}, cert={cert_file}")
#
#     try:
#         resp = requests.post(
#             vault_url,
#             cert=(cert_file, key_file),
#             verify=ca_bundle,
#             json={"name": "cert1"}
#         )
#     except Exception as req_err:
#         logger.error(f"[VAULT] Exceção na requisição de login - url={vault_url}, erro={req_err}")
#         raise Exception(f"Erro na requisição de login ao Vault") from req_err
#
#     logger.info(f"[VAULT] Resposta do login - status_code={resp.status_code}")
#
#     if resp.status_code != 200:
#         logger.error(f"[VAULT] Falha no login - status_code={resp.status_code}, url={vault_url}")
#         raise Exception(
#             f"Erro no login Vault - status={resp.status_code}"
#         )
#
#     try:
#         data = resp.json()
#     except Exception as json_err:
#         logger.error(f"[VAULT] Erro ao fazer parse da resposta - erro={json_err}")
#         raise Exception(f"Erro ao fazer parse da resposta Vault") from json_err
#
#     client_token = data.get("auth", {}).get("client_token")
#
#     if not client_token:
#         logger.error("[VAULT] client_token não encontrado na resposta do Vault")
#         raise Exception("client_token não encontrado na resposta do Vault")
#
#     logger.info("[VAULT] Login no Vault concluído com sucesso")
#     return client_token

# =========================
# Vault: Get AWS credentials
# Obtém credenciais AWS temporárias do Vault.
# Retorna dict com 'AccessKey', 'SecretKey', 'SecurityToken'.
# =========================
# def get_aws_credentials_from_vault(vault_aws_path, client_token, ca_bundle):
#
#     logger.info(f"[VAULT] Obtendo credenciais AWS do Vault - path={vault_aws_path}")
#
#     try:
#         resp = requests.post(
#             vault_aws_path,
#             headers={"X-Vault-Token": client_token},
#             verify=ca_bundle
#         )
#     except Exception as req_err:
#         logger.error(f"[VAULT] Exceção na requisição de creds AWS - path={vault_aws_path}, erro={req_err}")
#         raise Exception(f"Erro na requisição de creds AWS ao Vault") from req_err
#
#     logger.info(f"[VAULT] Resposta creds AWS - status_code={resp.status_code}")
#
#     if resp.status_code != 200:
#         logger.error(f"[VAULT] Falha ao obter creds AWS - status_code={resp.status_code}, path={vault_aws_path}")
#         raise Exception(
#             f"Erro ao obter creds AWS - status={resp.status_code}"
#         )
#
#     try:
#         data = resp.json()
#     except Exception as json_err:
#         logger.error(f"[VAULT] Erro ao fazer parse da resposta AWS - erro={json_err}")
#         raise Exception(f"Erro ao fazer parse da resposta AWS Vault") from json_err
#
#     creds = data.get("data", {})
#
#     access_key = creds.get("access_key")
#     secret_key = creds.get("secret_key")
#     security_token = creds.get("security_token")
#
#     if not access_key or not secret_key:
#         logger.error(f"[VAULT] Credenciais AWS incompletas - access_key={'presente' if access_key else 'ausente'}, secret_key={'presente' if secret_key else 'ausente'}")
#         raise Exception("Credenciais AWS incompletas na resposta do Vault")
#
#     logger.info("[VAULT] Credenciais AWS obtidas com sucesso")
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
#     logger.info(f"[S3] Criando cliente S3 - region={region}")
#
#     try:
#         session = boto3.Session(
#             aws_access_key_id=aws_creds["AccessKey"],
#             aws_secret_access_key=aws_creds["SecretKey"],
#             aws_session_token=aws_creds["SecurityToken"],
#             region_name=region
#         )
#         client = session.client("s3")
#         logger.info(f"[S3] Cliente S3 criado com sucesso - region={region}")
#         return client
#     except Exception as e:
#         logger.error(f"[S3] Erro ao criar cliente S3 - region={region}, erro={e}")
#         raise

# =========================
# AWS S3: Check if object exists
# Verifica se um objeto já existe no S3.
# =========================
# def s3_object_exists(s3_client, bucket, key):
#
#     logger.info(f"[S3_EXISTS] Verificando existência do objeto - bucket={bucket}, key={key}")
#
#     try:
#         s3_client.head_object(Bucket=bucket, Key=key)
#         logger.info(f"[S3_EXISTS] Objeto encontrado - bucket={bucket}, key={key}")
#         return True
#     except ClientError as e:
#         error_code = e.response["Error"]["Code"]
#         if error_code == "404":
#             logger.info(f"[S3_EXISTS] Objeto não encontrado (404) - bucket={bucket}, key={key}")
#             return False
#         logger.error(f"[S3_EXISTS] ClientError - bucket={bucket}, key={key}, error_code={error_code}")
#         raise
#     except Exception as e:
#         logger.error(f"[S3_EXISTS] Exceção ao verificar existência - bucket={bucket}, key={key}, erro={e}")
#         return False

# =========================
# AWS S3: Upload file
# Faz upload de um arquivo local para o S3.
# =========================
# def upload_to_s3(s3_client, bucket, key, local_path):
#
#     if not os.path.exists(local_path):
#         logger.error(f"[S3_UPLOAD] Arquivo local não existe - local_path={local_path}")
#         raise Exception(f"Arquivo não existe: {local_path}")
#
#     file_size = os.path.getsize(local_path)
#     logger.info(f"[S3_UPLOAD] Iniciando upload - bucket={bucket}, key={key}, size={file_size} bytes")
#
#     try:
#         s3_client.upload_file(local_path, bucket, key)
#         logger.info(f"[S3_UPLOAD] Upload concluído com sucesso - bucket={bucket}, key={key}")
#     except NoCredentialsError:
#         logger.error(f"[S3_UPLOAD] Credenciais AWS não encontradas - bucket={bucket}, key={key}")
#         raise Exception("Credenciais AWS não encontradas para upload S3")
#     except ClientError as s3_err:
#         logger.error(f"[S3_UPLOAD] ClientError - bucket={bucket}, key={key}, erro={s3_err}")
#         raise Exception(
#             f"Erro ClientError no upload S3 - bucket={bucket}, key={key}"
#         ) from s3_err
#     except Exception as e:
#         logger.error(f"[S3_UPLOAD] Erro inesperado - bucket={bucket}, key={key}, erro={e}")
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

    global _exec_id
    _exec_id = uuid.uuid4().hex[:12]

    logger.info("===== INICIANDO HANDLER =====")

    obs_client = None

    try:
        # --- Variáveis de ambiente ---
        obs_endpoint = os.getenv("OBS_ENDPOINT")
        obs_source_bucket = os.getenv("OBS_SOURCE_BUCKET")
        s3_bucket = os.getenv("S3_BUCKET")
        vault_url = os.getenv("VAULT_URL")
        vault_aws_path = os.getenv("VAULT_AWS_PATH")
        aws_region = os.getenv("AWS_REGION", "sa-east-1")

        logger.info(
            f"[ENV] Variáveis - obs_endpoint={obs_endpoint}, "
            f"obs_source_bucket={obs_source_bucket}, s3_bucket={s3_bucket}, "
            f"vault_url={vault_url}, vault_aws_path={vault_aws_path}, "
            f"aws_region={aws_region}"
        )

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
            logger.error(f"[ENV] Variáveis de ambiente obrigatórias ausentes: {missing}")
            raise Exception(f"Variáveis de ambiente obrigatórias ausentes: {missing}")

        logger.info("[ENV] Variáveis de ambiente validadas com sucesso")

        # 1. Cria cliente OBS ---
        logger.info("[STEP 1/10] Criando cliente OBS")
        obs_client = create_obs_client(context, obs_endpoint)

        # 2. Lista objetos no bucket OBS ---
        now = datetime.now(timezone.utc)
        start_period = f"{now.year}{now.month:02d}01"
        next_month = now.month + 1
        next_year = now.year
        if next_month > 12:
            next_month = 1
            next_year = now.year + 1
        end_period = f"{next_year}{next_month:02d}01"
        current_report_period = f"{start_period}-{end_period}"
        list_prefix = f"Huawei/{current_report_period}/"

        logger.info(
            f"[STEP 2/10] Listando objetos no bucket OBS - "
            f"bucket={obs_source_bucket}, prefix={list_prefix}"
        )
        objects = list_obs_objects(obs_client, obs_source_bucket, prefix=list_prefix)

        # 3. Encontra par CSV+JSON mais recente ---
        logger.info("[STEP 3/10] Selecionando par CSV+Manifest mais recente")
        artifact = find_latest_artifact_pair(objects)
        logger.info(
            f"[STEP 3/10] Par selecionado - CSV: {artifact['csv_key']}, "
            f"Manifest: {artifact['json_key']}, epoch={artifact['epoch']}"
        )

        # 4. Download dos arquivos do OBS ---
        logger.info("[STEP 4/10] Baixando arquivos do OBS")
        csv_local = f"/tmp/{artifact['epoch']}.csv"
        json_local = f"/tmp/{artifact['epoch']}-Manifest.json"

        download_from_obs(obs_client, obs_source_bucket, artifact["csv_key"], csv_local)
        download_from_obs(obs_client, obs_source_bucket, artifact["json_key"], json_local)

        # 5. Autentica no Vault ---
        logger.info("[STEP 5/10] Autenticando no Vault")
        # client_token = vault_login(vault_url, CERT_FILE, KEY_FILE, CA_BUNDLE)

        # 6. Obtém credenciais AWS do Vault ---
        logger.info("[STEP 6/10] Obtendo credenciais AWS do Vault")
        # aws_creds = get_aws_credentials_from_vault(vault_aws_path, client_token, CA_BUNDLE)

        # 7. Cria cliente S3 ---
        logger.info("[STEP 7/10] Criando cliente S3")
        # s3_client = create_s3_client(aws_creds, aws_region)

        # 8. Verifica se CSV já existe no S3 ---
        logger.info("[STEP 8/10] Verificando se CSV já existe no S3")
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
        logger.info("[STEP 9/10] Enviando arquivos para o S3")
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
        logger.info("[STEP 10/10] Limpando arquivos temporários")
        for f in [csv_local, json_local]:
            try:
                if os.path.exists(f):
                    os.remove(f)
                    logger.info(f"[CLEANUP] Arquivo removido - path={f}")
            except OSError as clean_err:
                logger.warning(f"[CLEANUP] Falha ao remover - path={f}, erro={clean_err}")

        logger.info("===== HANDLER CONCLUÍDO COM SUCESSO =====")

        return {
            "statusCode": 200,
            "isBase64Encoded": False,
            "body": json.dumps({
                "message": "Arquivos processados com sucesso",
                "csv_key": artifact["csv_key"],
                "manifest_key": artifact["json_key"]
            }),
            "headers": {
                "Content-Type": "application/json"
            }
        }

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[HANDLER] Erro: {str(e)}")
        logger.error(f"[HANDLER] Traceback: {tb}")

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
            logger.info("[HANDLER] Cliente OBS fechado")
        logger.info("===== FIM DO HANDLER =====")
