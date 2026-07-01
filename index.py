import os
import zipfile
import json
import traceback
import logging
from datetime import datetime, timezone
from obs import ObsClient

logger = logging.getLogger(__name__)


# =========================
# OBS: Create client
# =========================
def create_obs_client(context, endpoint):
    return ObsClient(
        access_key_id=context.getSecurityAccessKey(),
        secret_access_key=context.getSecuritySecretKey(),
        security_token=context.getSecurityToken(),
        server=endpoint
    )


# =========================
# SOURCE OBS: List valid ZIPs
# =========================
def list_valid_zip_objects(obs_client, bucket):
    resp = obs_client.listObjects(bucket)

    if resp.status >= 300 or not resp.body.contents:
        raise Exception("Erro ao listar objetos")

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

    if not valid:
        raise Exception("Nenhum ZIP encontrado")

    return valid


# =========================
# SOURCE OBS: Get latest file
# =========================
def get_latest_object(objects):
    return sorted(
        objects,
        key=lambda x: x["lastModified_dt"],
        reverse=True
    )[0]


# =========================
# SOURCE OBS: Download file
# =========================
def download_file(obs_client, bucket, key, local_path):
    logger.info(f"Download: bucket={bucket}, key={key}")
    try:
        resp = obs_client.getObject(bucket, key, downloadPath=local_path)
    except Exception as sdk_err:
        raise Exception(
            f"Exceção do SDK no download — "
            f"bucket={bucket}, key={key}, "
            f"erro={sdk_err}"
        ) from sdk_err

    if resp.status >= 300:
        raise Exception(
            f"Erro no download do arquivo — "
            f"status={resp.status}, "
            f"reason={getattr(resp, 'reason', 'N/A')}, "
            f"body={getattr(resp, 'body', 'N/A')}, "
            f"bucket={bucket}, key={key}"
        )


# =========================
# SOURCE OBS: Extract
# =========================
def extract_zip(zip_path, extract_dir):
    if not os.path.exists(extract_dir):
        os.makedirs(extract_dir)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)


# =========================
# SOURCE OBS: Find CSV
# =========================
def find_csv(directory):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".csv"):
                return os.path.join(root, file)

    raise Exception("CSV não encontrado")


# =========================
# ARTIFACT: JSON metadata content
# =========================
def generate_json(target_bucket, csv_key):
    return {
        "content_type": "CSV",
        "root_dir": target_bucket,
        "all_report_keys": [csv_key],
        "focus_version": "1.0"
    }


# =========================
# ARTIFACT: CSV and JSON files
# Path: Huawei/yyyy/mm/dd/yyyymmdd.[csv|json]
# =========================
def create_artifacts(target_bucket, csv_path, created_dt):
    year = created_dt.strftime('%Y')
    month = created_dt.strftime('%m')
    day = created_dt.strftime('%d')
    date_str = created_dt.strftime('%Y%m%d')

    base = f"Huawei/{year}/{month}/{day}"

    csv_key = f"{base}/{date_str}.csv"
    json_key = f"{base}/{date_str}.json"

    json_data = generate_json(target_bucket, csv_key)

    json_path = f"/tmp/{date_str}.json"

    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=4)

    return {
        "csv_path": csv_path,
        "json_path": json_path,
        "csv_key": csv_key,
        "json_key": json_key,
        "date_str": date_str
    }


# =========================
# TARGET OBS: Upload
# =========================
def upload_to_obs(obs_client, target_bucket, artifact):

    # Check if exists
    meta = obs_client.getObjectMetadata(target_bucket, artifact["csv_key"])
    if meta.status == 200:
        return {
            "status": "exists",
            "csv_key": artifact["csv_key"]
        }

    # Upload CSV
    resp_csv = obs_client.putFile(
        target_bucket,
        artifact["csv_key"],
        artifact["csv_path"]
    )
    if resp_csv.status >= 300:
        raise Exception("Erro upload CSV")

    # Upload JSON
    resp_json = obs_client.putFile(
        target_bucket,
        artifact["json_key"],
        artifact["json_path"]
    )
    if resp_json.status >= 300:
        raise Exception("Erro upload JSON")

    return {
        "status": "uploaded",
        "csv_key": artifact["csv_key"],
        "json_key": artifact["json_key"]
    }

# =========================
# HANDLER
# =========================
def handler(event, context):

    obs_client = None

    try:
        endpoint = os.getenv('OBS_ENDPOINT')
        source_bucket = os.getenv('SOURCE_BUCKET')
        target_bucket = os.getenv('TARGET_BUCKET')

        obs_client = create_obs_client(context, endpoint)

        # 1. List
        objects = list_valid_zip_objects(obs_client, source_bucket)

        # 2. Latest
        latest = get_latest_object(objects)

        # 3. Download
        zip_path = "/tmp/source.zip"
        download_file(obs_client, source_bucket, latest["key"], zip_path)

        # 4. Extract
        extract_dir = "/tmp/extracted"
        extract_zip(zip_path, extract_dir)
        csv_path = find_csv(extract_dir)

        # 5. CSV file and JSON Metadata
        artifact = create_artifacts(
            target_bucket,
            csv_path,
            latest["lastModified_dt"]
        )

        # 6. Upload to OBS
        result = upload_to_obs(
            obs_client,
            target_bucket,
            artifact
        )

        return {
            "statusCode": 200,
            "result": result
        }

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Erro: {str(e)}")
        logger.error(f"Traceback: {tb}")
        return {
            "statusCode": 500,
            "error": str(e)
        }

    finally:
        if obs_client:
            obs_client.close()