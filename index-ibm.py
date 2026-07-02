import os
import uuid
import zipfile
import json
import traceback
import logging
import time as _time
from datetime import datetime, timezone
from obs import ObsClient


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


# =========================
# OBS: Create client
# =========================
def create_obs_client(context, endpoint):
    logger.info(f"[OBS] Criando cliente OBS - endpoint={endpoint}")
    client = ObsClient(
        access_key_id=context.getSecurityAccessKey(),
        secret_access_key=context.getSecuritySecretKey(),
        security_token=context.getSecurityToken(),
        server=endpoint
    )
    logger.info("[OBS] Cliente OBS criado com sucesso")
    return client


# =========================
# SOURCE OBS: List valid ZIPs
# =========================
def list_valid_zip_objects(obs_client, bucket):
    logger.info(f"[LIST] Listando objetos do bucket - bucket={bucket}")
    resp = obs_client.listObjects(bucket)

    if resp.status >= 300 or not resp.body.contents:
        logger.error(f"[LIST] Falha ao listar objetos - status={resp.status}")
        raise Exception("Erro ao listar objetos")

    logger.info(f"[LIST] Status da listagem: {resp.status}")
    valid = []

    for content in resp.body.contents:
        if content.size == 0:
            continue
        if content.key.endswith(".crc"):
            continue
        if not content.key.endswith(".zip"):
            continue

        dt = datetime.strptime(
            content.lastModified,
            "%Y/%m/%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        valid.append({
            "key": content.key,
            "lastModified_dt": dt
        })

    logger.info(f"[LIST] ZIPs válidos encontrados: {len(valid)}")

    if not valid:
        raise Exception("Nenhum ZIP encontrado")

    return valid


# =========================
# SOURCE OBS: Get latest file
# =========================
def get_latest_object(objects):
    logger.info(f"[LATEST] Selecionando ZIP mais recente de {len(objects)} objetos")
    latest = sorted(
        objects,
        key=lambda x: x["lastModified_dt"],
        reverse=True
    )[0]
    logger.info(
        f"[LATEST] ZIP selecionado - key={latest['key']}, "
        f"lastModified={latest['lastModified_dt'].isoformat()}"
    )
    return latest


# =========================
# SOURCE OBS: Download file
# =========================
def download_file(obs_client, bucket, key, local_path):
    logger.info(f"[DOWNLOAD] Iniciando - bucket={bucket}, key={key}, local_path={local_path}")
    try:
        resp = obs_client.getObject(bucket, key, downloadPath=local_path)
    except Exception as sdk_err:
        logger.error(f"[DOWNLOAD] Exceção do SDK - bucket={bucket}, key={key}, erro={sdk_err}")
        raise Exception(
            f"Exceção do SDK no download - "
            f"bucket={bucket}, key={key}, "
            f"erro={sdk_err}"
        ) from sdk_err

    if resp.status >= 300:
        logger.error(
            f"[DOWNLOAD] Falha - status={resp.status}, "
            f"reason={getattr(resp, 'reason', 'N/A')}, "
            f"bucket={bucket}, key={key}"
        )
        raise Exception(
            f"Erro no download do arquivo - "
            f"status={resp.status}, "
            f"reason={getattr(resp, 'reason', 'N/A')}, "
            f"body={getattr(resp, 'body', 'N/A')}, "
            f"bucket={bucket}, key={key}"
        )

    file_size = os.path.getsize(local_path) if os.path.exists(local_path) else 0
    logger.info(f"[DOWNLOAD] Concluído - key={key}, size={file_size} bytes")


# =========================
# SOURCE OBS: Extract
# =========================
def extract_zip(zip_path, extract_dir):
    logger.info(f"[EXTRACT] Extraindo ZIP - zip_path={zip_path}, extract_dir={extract_dir}")
    if not os.path.exists(extract_dir):
        os.makedirs(extract_dir)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        members = zip_ref.namelist()
        zip_ref.extractall(extract_dir)

    logger.info(f"[EXTRACT] Extração concluída - {len(members)} arquivos extraídos")


# =========================
# SOURCE OBS: Find CSV
# =========================
def find_csv(directory):
    logger.info(f"[FIND_CSV] Procurando CSV em - directory={directory}")
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".csv"):
                csv_path = os.path.join(root, file)
                logger.info(f"[FIND_CSV] CSV encontrado - path={csv_path}")
                return csv_path

    logger.error(f"[FIND_CSV] Nenhum CSV encontrado em - directory={directory}")
    raise Exception("CSV não encontrado")


# =========================
# ARTIFACT: JSON metadata content
# =========================
def generate_json(s3_bucket, csv_key, report_period):
    logger.info(f"[JSON] Gerando metadados JSON - s3_bucket={s3_bucket}, csv_key={csv_key}")
    return {
        "content_type": "CSV",
        "root_dir": s3_bucket,
        "all_report_keys": [csv_key],
        "focus_version": "1.0",
        "report_period": report_period
    }


# =========================
# ARTIFACT: CSV and JSON files
# Path: <Vendor>/<Report_Period>/<Epoch>/<prefix>-Manifest.json
#   Vendor         = Huawei
#   Report_Period  = YYYYMM01-YYYY(M+1)01
#   Epoch          = epoch time do upload
#   prefix         = Huawei
# =========================
def create_artifacts(s3_bucket, target_bucket, csv_path, created_dt):
    logger.info(f"[ARTIFACT] Criando artefatos - s3_bucket={s3_bucket}, target_bucket={target_bucket}, csv_path={csv_path}")

    year = int(created_dt.strftime('%Y'))
    month = int(created_dt.strftime('%m'))

    # Report Period: YYYYMM01-YYYY(M+1)01
    start_period = f"{year}{month:02d}01"
    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year = year + 1
    end_period = f"{next_year}{next_month:02d}01"
    report_period = f"{start_period}-{end_period}"

    # Epoch Time Folder
    epoch_folder = str(int(_time.time()))

    base = f"Huawei/{report_period}/{epoch_folder}"

    csv_key = f"{base}/{epoch_folder}.csv"
    json_key = f"{base}/{epoch_folder}-Manifest.json"

    json_data = generate_json(s3_bucket, csv_key, report_period)

    json_path = f"/tmp/{epoch_folder}-Manifest.json"

    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=4)

    logger.info(
        f"[ARTIFACT] Artefatos criados - report_period={report_period}, "
        f"epoch_folder={epoch_folder}, csv_key={csv_key}, json_key={json_key}"
    )

    return {
        "csv_path": csv_path,
        "json_path": json_path,
        "csv_key": csv_key,
        "json_key": json_key,
        "report_period": report_period,
        "epoch_folder": epoch_folder
    }


# =========================
# TARGET OBS: Upload
# =========================
def upload_to_obs(obs_client, target_bucket, artifact):

    logger.info(f"[UPLOAD] Iniciando upload - target_bucket={target_bucket}")

    # Check if exists
    logger.info(f"[UPLOAD] Verificando se CSV já existe - key={artifact['csv_key']}")
    meta = obs_client.getObjectMetadata(target_bucket, artifact["csv_key"])
    if meta.status == 200:
        logger.info(f"[UPLOAD] CSV já existe no bucket - key={artifact['csv_key']}")
        return {
            "status": "exists",
            "csv_key": artifact["csv_key"]
        }

    # Upload CSV
    logger.info(f"[UPLOAD] Enviando CSV - key={artifact['csv_key']}")
    resp_csv = obs_client.putFile(
        target_bucket,
        artifact["csv_key"],
        artifact["csv_path"]
    )
    if resp_csv.status >= 300:
        logger.error(f"[UPLOAD] Falha no upload CSV - status={resp_csv.status}, key={artifact['csv_key']}")
        raise Exception("Erro upload CSV")
    logger.info(f"[UPLOAD] CSV enviado com sucesso - key={artifact['csv_key']}")

    # Upload JSON
    logger.info(f"[UPLOAD] Enviando JSON - key={artifact['json_key']}")
    resp_json = obs_client.putFile(
        target_bucket,
        artifact["json_key"],
        artifact["json_path"]
    )
    if resp_json.status >= 300:
        logger.error(f"[UPLOAD] Falha no upload JSON - status={resp_json.status}, key={artifact['json_key']}")
        raise Exception("Erro upload JSON")
    logger.info(f"[UPLOAD] JSON enviado com sucesso - key={artifact['json_key']}")

    logger.info("[UPLOAD] Upload concluído com sucesso")

    return {
        "status": "uploaded",
        "csv_key": artifact["csv_key"],
        "json_key": artifact["json_key"]
    }

# =========================
# HANDLER
# =========================
def handler(event, context):

    global _exec_id
    _exec_id = uuid.uuid4().hex[:12]

    logger.info("===== INICIANDO HANDLER =====")

    obs_client = None

    try:
        endpoint = os.getenv('OBS_ENDPOINT')
        source_bucket = os.getenv('SOURCE_BUCKET')
        target_bucket = os.getenv('TARGET_BUCKET')
        s3_bucket = os.getenv('S3_BUCKET')

        logger.info(
            f"[ENV] Variáveis - endpoint={endpoint}, "
            f"source_bucket={source_bucket}, target_bucket={target_bucket}, s3_bucket={s3_bucket}"
        )

        # 1. OBS Client
        logger.info("[STEP 1/7] Criando cliente OBS")
        obs_client = create_obs_client(context, endpoint)

        # 2. List
        logger.info(f"[STEP 2/7] Listando ZIPs válidos - bucket={source_bucket}")
        objects = list_valid_zip_objects(obs_client, source_bucket)

        # 3. Latest
        logger.info("[STEP 3/7] Selecionando ZIP mais recente")
        latest = get_latest_object(objects)

        # 4. Download
        logger.info("[STEP 4/7] Baixando ZIP")
        zip_path = "/tmp/source.zip"
        download_file(obs_client, source_bucket, latest["key"], zip_path)

        # 5. Extract
        logger.info("[STEP 5/7] Extraindo ZIP e localizando CSV")
        extract_dir = "/tmp/extracted"
        extract_zip(zip_path, extract_dir)
        csv_path = find_csv(extract_dir)

        # 6. CSV file and JSON Metadata
        logger.info("[STEP 6/7] Criando artefatos (CSV e JSON)")
        artifact = create_artifacts(
            s3_bucket,
            target_bucket,
            csv_path,
            latest["lastModified_dt"]
        )

        # 7. Upload to OBS
        logger.info("[STEP 7/7] Enviando artefatos para OBS")
        result = upload_to_obs(
            obs_client,
            target_bucket,
            artifact
        )

        logger.info(f"===== HANDLER CONCLUÍDO COM SUCESSO - result={result} =====")

        return {
            "statusCode": 200,
            "result": result
        }

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[HANDLER] Erro: {str(e)}")
        logger.error(f"[HANDLER] Traceback: {tb}")
        return {
            "statusCode": 500,
            "error": str(e)
        }

    finally:
        if obs_client:
            obs_client.close()
            logger.info("[HANDLER] Cliente OBS fechado")
        logger.info("===== FIM DO HANDLER =====")