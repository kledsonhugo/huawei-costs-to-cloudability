import os
import uuid
import json
import traceback
import logging
from datetime import datetime, timezone
from obs import ObsClient
# import boto3
# from botocore.exceptions import NoCredentialsError, ClientError
# import requests
from requests import packages
from urllib3 import disable_warnings
import socket
import ssl
from urllib.parse import urlparse
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.http.http_config import HttpConfig
from huaweicloudsdkcsms.v1.csms_client import CsmsClient
from huaweicloudsdkcsms.v1.region.csms_region import CsmsRegion
from huaweicloudsdkcsms.v1.model.show_secret_version_request import ShowSecretVersionRequest


# ---------------------------------------------------------------------------
# Logging com identificador único de execução (correlation ID)
# ---------------------------------------------------------------------------
_exec_id = None

class ExecIdFilter(logging.Filter):

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
# Diagnóstico de rede: OBS endpoint
# Testa resolução DNS e conectividade TCP (porta 443) com o endpoint do OBS.
# ---------------------------------------------------------------------------
def diagnose_obs_network(obs_endpoint):
    host = urlparse(obs_endpoint).hostname
    logger.info(f"Resolvendo host OBS: {host}")

    try:
        addrs = socket.getaddrinfo(host, 443)
        for addr in addrs:
            logger.info(f"DNS result: {addr[4][0]}")
    except Exception as e:
        logger.error(f"Falha DNS para {host}: {e}")
        raise

    logger.info(f"Testando TCP connect em {host}:443")
    try:
        s = socket.create_connection((host, 443), timeout=5)
        s.close()
        logger.info("TCP connect OK")
    except Exception as e:
        logger.error(f"Falha TCP connect em {host}:443: {e}")
        raise


# ---------------------------------------------------------------------------
# Diagnóstico de rede: OBS bucket
# Testa DNS, TCP connect e TLS handshake para o host do endpoint e do bucket
# (formato <bucket>.<endpoint_host>).
# ---------------------------------------------------------------------------
def diagnose_obs_bucket_network(obs_endpoint, bucket):
    base_host = urlparse(obs_endpoint).hostname
    bucket_host = f"{bucket}.{base_host}"

    for host in [base_host, bucket_host]:
        logger.info(f"===== Testando host: {host} =====")

        try:
            addrs = socket.getaddrinfo(host, 443)
            for addr in addrs:
                logger.info(f"DNS {host}: {addr[4][0]}")
        except Exception as e:
            logger.error(f"Falha DNS para {host}: {e}")
            raise

        try:
            logger.info(f"Testando TCP connect em {host}:443")
            raw_sock = socket.create_connection((host, 443), timeout=5)
            logger.info(f"TCP connect OK para {host}")

            logger.info(f"Testando TLS handshake em {host}:443")
            context_ssl = ssl.create_default_context()
            tls_sock = context_ssl.wrap_socket(raw_sock, server_hostname=host)
            logger.info(f"TLS handshake OK para {host} - version={tls_sock.version()}")

            tls_sock.close()

        except Exception as e:
            logger.error(f"Falha TCP/TLS para {host}: {e}")
            raise


# ---------------------------------------------------------------------------
# Diagnóstico de rede: CSMS
# Testa DNS, TCP connect e TLS handshake com o endpoint do CSMS da Huawei Cloud.
# Se 'endpoint' for informado (VPC endpoint privado), usa-o em vez do DNS público.
# ---------------------------------------------------------------------------
def diagnose_csms_network(region_id, endpoint=None):
    if endpoint:
        csms_host = urlparse(endpoint).hostname
        logger.info(f"===== Diagnóstico CSMS (VPC endpoint) - host: {csms_host} =====")
    else:
        csms_host = f"csms.{region_id}.myhuaweicloud.com"
        logger.info(f"===== Diagnóstico CSMS - host: {csms_host} =====")

    try:
        addrs = socket.getaddrinfo(csms_host, 443)
        for addr in addrs:
            logger.info(f"DNS {csms_host}: {addr[4][0]}")
    except Exception as e:
        logger.error(f"Falha DNS para {csms_host}: {e}")
        raise

    try:
        logger.info(f"Testando TCP connect em {csms_host}:443")
        raw_sock = socket.create_connection((csms_host, 443), timeout=5)
        logger.info(f"TCP connect OK para {csms_host}")

        logger.info(f"Testando TLS handshake em {csms_host}:443")
        context_ssl = ssl.create_default_context()
        tls_sock = context_ssl.wrap_socket(raw_sock, server_hostname=csms_host)
        logger.info(f"TLS handshake OK para {csms_host} - version={tls_sock.version()}")

        tls_sock.close()

    except Exception as e:
        logger.error(f"Falha TCP/TLS para {csms_host}: {e}")
        raise


# =========================
# OBS: Criar cliente
# Cria um cliente OBS com credenciais temporárias do contexto da FunctionGraph.
# Configura path_style, timeout e max_retry_count para resiliência.
# =========================
def create_obs_client(context, endpoint):
    logger.info(f"Criando cliente OBS - endpoint={endpoint}")

    ak = context.getSecurityAccessKey()
    sk = context.getSecuritySecretKey()
    tk = context.getSecurityToken()

    if not ak or not sk or not tk:
        raise Exception("Credenciais OBS temporárias não disponíveis no contexto")

    client = ObsClient(
        access_key_id=ak,
        secret_access_key=sk,
        security_token=tk,
        server=endpoint,
        path_style=True,
        timeout=10,
        max_retry_count=2
    )

    logger.info("Cliente OBS criado com sucesso")
    return client


# ---------------------------------------------------------------------------
# OBS: Download de certificados do bucket
# Baixa cert.pem e ca_bundle.crt do diretório "certs/" no bucket OBS
# para o diretório local especificado.
# ---------------------------------------------------------------------------
def download_certs_from_obs(obs_client, bucket, cert_dir, cert_file, ca_bundle, obs_prefix="certs/"):
    logger.info(f"Iniciando download de certificados - bucket={bucket}, prefix={obs_prefix}")

    if not os.path.exists(cert_dir):
        os.makedirs(cert_dir)
        logger.info(f"Diretório criado - path={cert_dir}")

    cert_files = [
        (f"{obs_prefix}{cert_file}",  os.path.join(cert_dir, cert_file)),
        (f"{obs_prefix}{ca_bundle}", os.path.join(cert_dir, ca_bundle)),
    ]

    for obs_key, local_path in cert_files:
        logger.info(f"Baixando - key={obs_key}, local_path={local_path}")

        try:
            resp = obs_client.getObject(bucket, obs_key, downloadPath=local_path)
        except Exception as sdk_err:
            raise Exception(f"Exceção no download do certificado - key={obs_key}") from sdk_err

        if resp.status >= 300:
            raise Exception(f"Erro no download do certificado - status={resp.status}, key={obs_key}")

        if os.path.exists(local_path):
            file_size = os.path.getsize(local_path)
            logger.info(f"Download concluído - key={obs_key}, size={file_size} bytes")
        else:
            raise Exception(f"Certificado não foi criado: {local_path}")

    logger.info("Todos os certificados baixados com sucesso")


# ---------------------------------------------------------------------------
# KEY: Buscar conteúdo do key.pem no CSMS
# O conteúdo da chave privada (key.pem) é armazenado como secret no CSMS.
# Usa o SDK oficial da Huawei Cloud com credenciais temporárias do contexto.
# ---------------------------------------------------------------------------
def fetch_key_from_csms(context, secret_name, region_id="sa-east-1", endpoint=None, project_id=None):
    logger.info(f"Buscando secret no CSMS - secret={secret_name}, region={region_id}, endpoint={endpoint}, project_id={'set' if project_id else 'None'}")

    ak = context.getSecurityAccessKey()
    sk = context.getSecuritySecretKey()
    tk = context.getSecurityToken()

    if not ak or not sk or not tk:
        raise Exception("Credenciais temporárias não disponíveis no contexto para CSMS")

    credentials = BasicCredentials(ak=ak, sk=sk).with_security_token(tk)
    if project_id:
        credentials = credentials.with_project_id(project_id)
        logger.info(f"project_id setado explicitamente (sem chamada ao IAM)")

    try:
        csms_region = CsmsRegion.value_of(region_id)
        if endpoint:
            csms_region.endpoints = [endpoint]
            logger.info(f"Endpoint CSMS sobrescrito (VPC): {endpoint}")

        http_config = HttpConfig.get_default_config()
        http_config.timeout = 10
        http_config.retry_total = 0
        logger.info(f"HTTP config aplicado - timeout=10s, retry=0")

        csms_client = CsmsClient.new_builder() \
            .with_http_config(http_config) \
            .with_credentials(credentials) \
            .with_region(csms_region) \
            .build()
    except Exception as e:
        raise Exception(f"Erro ao criar cliente CSMS") from e

    try:
        request = ShowSecretVersionRequest(
            secret_name=secret_name,
            version_id="latest"
        )
        response = csms_client.show_secret_version(request)
    except Exception as e:
        raise Exception(f"Erro ao buscar secret no CSMS - tipo={type(e).__name__}, msg={str(e)}") from e

    if not response or not response.version:
        raise Exception(f"Secret CSMS vazio ou não encontrado - secret={secret_name}")

    key_content = response.version.secret_string

    if not key_content:
        raise Exception(f"Secret_string vazio no CSMS - secret={secret_name}")

    logger.info(f"Secret obtido com sucesso - secret={secret_name}, size={len(key_content)} chars")
    return key_content


# ---------------------------------------------------------------------------
# KEY: Escrever key.pem local
# Escreve o conteúdo da chave privada obtido do CSMS em um arquivo local.
# ---------------------------------------------------------------------------
def write_key_file(key_content, key_path):
    logger.info(f"Escrevendo key.pem - target={key_path}")

    key_dir = os.path.dirname(key_path)
    if not os.path.exists(key_dir):
        os.makedirs(key_dir)
        logger.info(f"Diretório criado - path={key_dir}")

    try:
        with open(key_path, "w") as f:
            f.write(key_content)
    except Exception as write_err:
        raise Exception(f"Erro ao escrever key.pem") from write_err

    file_size = os.path.getsize(key_path)
    logger.info(f"Arquivo key.pem criado com sucesso - path={key_path}, size={file_size} bytes")


# =========================
# OBS: Listar objetos
# Lista objetos de custos do bucket OBS filtrando pelo período de relatório atual
# (formato Huawei/YYYYMM01-YYYY(M+1)01/).
# Ignora objetos de tamanho zero.
# Retorna lista de dicts com 'key', 'size' e 'lastModified'.
# =========================
def list_obs_objects(obs_client, bucket):
    now = datetime.now(timezone.utc)
    start_period = f"{now.year}{now.month:02d}01"
    next_month = now.month + 1
    next_year = now.year
    if next_month > 12:
        next_month = 1
        next_year = now.year + 1
    end_period = f"{next_year}{next_month:02d}01"
    current_report_period = f"{start_period}-{end_period}"
    prefix = f"Huawei/{current_report_period}/"

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
# OBS: Encontrar par CSV+Manifest mais recente
# Dada uma lista de objetos, encontra o par CSV+Manifest mais recente.
# Os arquivos seguem o padrão:
#   Huawei/<report_period>/<epoch>/<epoch>.csv
#   Huawei/<report_period>/<epoch>/<epoch>-Manifest.json
#     report_period = YYYYMM01-YYYY(M+1)01
#     epoch         = epoch time do upload
# Retorna dict com 'csv_key', 'json_key' e 'epoch'.
# =========================
def find_latest_artifact_pair(objects):
    """Retorna o par CSV+Manifest mais recente encontrado na lista de objetos."""

    logger.info(f"Procurando par CSV+JSON mais recente de {len(objects)} objetos")

    csv_files = [o for o in objects if o["key"].endswith(".csv")]
    manifest_files = {o["key"] for o in objects if o["key"].endswith("-Manifest.json")}

    logger.info(f"CSVs encontrados: {len(csv_files)}, Manifests encontrados: {len(manifest_files)}")

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
            logger.info(f"Par válido encontrado - epoch={epoch}")
            return {
                "csv_key": csv_key,
                "json_key": manifest_key,
                "epoch": epoch
            }

    raise Exception("Nenhum par CSV+Manifest encontrado no bucket OBS")


# =========================
# OBS: Download de arquivo
# Baixa um objeto do bucket OBS para o caminho local especificado.
# =========================
def download_from_obs(obs_client, bucket, key, local_path):

    logger.info(f"Iniciando download - bucket={bucket}, key={key}, local_path={local_path}")

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
        logger.info(f"Download concluído - key={key}, size={file_size} bytes")
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
# =========================
def handler(event, context):

    global _exec_id
    _exec_id = uuid.uuid4().hex[:12]

    logger.info("===== INICIANDO HANDLER =====")
    
    obs_client = None

    try:

        # --- Variáveis de ambiente ---
        
        obs_endpoint = os.getenv("OBS_ENDPOINT", "https://obs.sa-brazil-1.myhuaweicloud.com")
        obs_source_bucket = os.getenv("OBS_BUCKET")
        cert_dir = os.getenv("CERT_DIR", "/tmp/certificado")
        cert_file = os.getenv("CERT_FILE", "cert.pem")
        key_file = os.getenv("KEY_FILE", "key.pem")
        ca_bundle = os.getenv("CA_BUNDLE", "ca_bundle.crt")
        csms_secret_name = os.getenv("CSMS_SECRET_NAME")
        csms_region = os.getenv("CSMS_REGION", "sa-brazil-1")
        csms_endpoint = os.getenv("CSMS_ENDPOINT", "https://kms.sa-brazil-1.myhuaweicloud.com")
        csms_project_id = os.getenv("CSMS_PROJECT_ID")
        # aws_region = os.getenv("AWS_REGION", "sa-east-1")
        # vault_url = os.getenv("VAULT_URL")
        # vault_aws_path = os.getenv("VAULT_AWS_PATH")
        # s3_bucket = os.getenv("S3_BUCKET")

        missing = []
        if not obs_endpoint:
            missing.append("OBS_ENDPOINT")
        if not obs_source_bucket:
            missing.append("OBS_BUCKET")
        if not csms_region:
            missing.append("CSMS_REGION")
        if not csms_endpoint:
            missing.append("CSMS_ENDPOINT")
        if not csms_project_id:
            missing.append("CSMS_PROJECT_ID")
        if not csms_secret_name:
            missing.append("CSMS_SECRET_NAME")
        if not cert_dir:
            missing.append("CERT_DIR")
        if not cert_file:
            missing.append("CERT_FILE")
        if not key_file:
            missing.append("KEY_FILE")
        if not ca_bundle:
            missing.append("CA_BUNDLE")
        # if not aws_region:
        #     missing.append("AWS_REGION")
        # if not s3_bucket:
        #     missing.append("S3_BUCKET")
        # if not vault_url:
        #     missing.append("VAULT_URL")
        # if not vault_aws_path:
        #     missing.append("VAULT_AWS_PATH")
        if missing:
            raise Exception(f"Variáveis de ambiente obrigatórias ausentes: {missing}")


        # --- Sanity Check ---

        # logger.info("[DEBUG 1/4] Valida conectividade com OBS endpoint")
        # diagnose_obs_network(obs_endpoint)

        # logger.info("[DEBUG 2/4] Valida conectividade com OBS bucket")
        # diagnose_obs_bucket_network(obs_endpoint, obs_source_bucket)

        # logger.info("[DEBUG 3/4] Valida conectividade com o endpoint do CSMS")
        # diagnose_csms_network(csms_region, endpoint=csms_endpoint)


        # --- Início do processo ---

        # 1. Cria cliente OBS ---
        logger.info("[STEP 1/11] Criando cliente OBS")
        obs_client = create_obs_client(context, obs_endpoint)

        # 2. Download dos certificados do bucket OBS ---
        logger.info("[STEP 2/11] Baixando certificados do bucket OBS")
        # download_certs_from_obs(obs_client, obs_source_bucket, cert_dir, cert_file, ca_bundle)

        # 3. Escreve key.pem a partir do secret no CSMS ---
        logger.info("[STEP 3/11] Escrevendo key.pem a partir do CSMS")
        # key_local_path = os.path.join(cert_dir, key_file)
        # key_content = fetch_key_from_csms(context, csms_secret_name, csms_region, endpoint=csms_endpoint, project_id=csms_project_id)
        # write_key_file(key_content, key_local_path)

        # 4. Lista objetos de custos no bucket OBS ---
        logger.info("[STEP 4/11] Lista objetos no bucket OBS")
        objects = list_obs_objects(obs_client, obs_source_bucket)

        # 5. Encontra par CSV+JSON mais recente ---
        logger.info("[STEP 5/11] Selecionando par CSV + Manifest mais recente")
        artifact = find_latest_artifact_pair(objects)
        logger.info(
            f"csv: {artifact['csv_key']}, "
            f"manifest: {artifact['json_key']}, "
            f"epoch: {artifact['epoch']}"
        )

        # 6. Download dos arquivos do OBS ---
        logger.info("[STEP 6/11] Baixando arquivos do OBS")
        csv_local = f"/tmp/{artifact['epoch']}.csv"
        json_local = f"/tmp/{artifact['epoch']}-Manifest.json"
        download_from_obs(obs_client, obs_source_bucket, artifact["csv_key"], csv_local)
        download_from_obs(obs_client, obs_source_bucket, artifact["json_key"], json_local)

        # 7. Autentica no Vault ---
        logger.info("[STEP 7/11] Autenticando no Vault")
        # client_token = vault_login(vault_url, CERT_FILE, KEY_FILE, CA_BUNDLE)

        # 8. Obtém credenciais AWS do Vault ---
        logger.info("[STEP 8/11] Obtendo credenciais AWS do Vault")
        # aws_creds = get_aws_credentials_from_vault(vault_aws_path, client_token, CA_BUNDLE)

        # 9. Cria cliente S3 ---
        logger.info("[STEP 9/11] Criando cliente S3")
        # s3_client = create_s3_client(aws_creds, aws_region)

        # 10. Verifica se CSV já existe no S3 ---
        logger.info("[STEP 10/11] Verificando se CSV já existe no S3")
        # csv_exists = s3_object_exists(s3_client, s3_bucket, artifact["csv_key"])
        #
        # if csv_exists:
        #     result = {
        #         "status": "exists",
        #         "csv_key": artifact["csv_key"],
        #         "json_key": artifact["json_key"]
        #     }
        # else:
        #     # 11. Upload CSV e JSON para S3 ---
        logger.info("[STEP 11/11] Enviando arquivos para o S3")
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


        logger.info("===== HANDLER CONCLUÍDO COM SUCESSO =====")

        return {
            "statusCode": 200,
            "csv": artifact["csv_key"],
            "manifest": artifact["json_key"]
        }

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Erro: {str(e)}")
        logger.error(f"Traceback: {tb}")

        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e)
            })
        }


    finally:

        if obs_client:
            obs_client.close()
            
        logger.info("===== FIM DO HANDLER =====")
