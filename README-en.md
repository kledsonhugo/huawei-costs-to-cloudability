# Costs Details Export to Cloudability

Process to automate the extraction and sending of **Huawei Cloud** cost data to the **Cloudability** tool.

## Architecture Diagram

Diagram of the solution considering the resources and operational flow.

![Diagram](images/diagram.png)

## Solution

The solution comprises three macro functionalities:

1. **Scheduled Task** in the **Cost Details Export** of the **Billing > Cost Center** service on the **Master Account**, which daily generates cost data from all accounts in the organization into an **OBS** bucket on the **Master Account** itself.

2. **FunctionGraph** (`fg-cloudability`) in a **Centralized Account**, which extracts cost data from the **OBS** bucket on the **Master Account** and sends it to an **OBS** bucket on the **Centralized Account**.

3. **FunctionGraph** (`fg-cloudability-s3`) in a **Centralized Account**, which reads the cost files (CSV) and metadata (JSON) from the **OBS** bucket on the **Centralized Account**. The upload to the **S3** bucket on **AWS** is currently disabled.

## FunctionGraph `fg-cloudability`

### Cost data retrieval

Reading files from the source bucket.

* Method: `listObjects`
* Applied filters:

  * Ignores files with size 0
  * Ignores `.crc` files
  * Considers only `.zip` files

---

### Most recent file selection

* Sorted by `lastModified`
* Automatic selection of the most recent file

---

### ZIP processing

* Download to `/tmp/source.zip`
* Extraction to `/tmp/extracted`

---

### CSV location

* Recursive search in the extracted directory
* Returns the first `.csv` file found

---

### Metadata generation

Creation of a JSON file for integration with external tools.

Example:

```json
{
  "content_type": "CSV",
  "root_dir": "s3-bucket",
  "all_report_keys": [
    "Huawei/20260301-20260401/1714521600/1714521600.csv"
  ],
  "focus_version": "1.0",
  "report_period": "20260301-20260401"
}
```

---

### Upload to OBS

Operations performed:

1. Checks file existence (`getObjectMetadata`)
2. If it does not exist:

   * Upload the CSV
   * Upload the JSON

---

## FunctionGraph `fg-cloudability-s3`

### Listing objects in OBS

Reading files from the target OBS bucket.

* Method: `listObjects`
* Filter by prefix `Huawei/<report_period>/` (current period `YYYYMM01-YYYY(M+1)01`)
* Ignores objects with size 0

---

### Identifying the most recent CSV + Manifest pair

* Filters `.csv` and `-Manifest.json` files
* Pairs CSV and Manifest by name (same epoch)
* Selects the most recent pair (highest epoch)

---

### File download

* Download CSV to `/tmp/<epoch>.csv`
* Download Manifest to `/tmp/<epoch>-Manifest.json`

---

### Upload to AWS S3 (disabled)

> ⚠️ The AWS S3 integration via HashiCorp Vault is currently disabled (commented code). The script currently only reads and downloads files from the OBS bucket.

Planned flow:

* Vault authentication via TLS certificate (`cert.pem` + `key.pem`)
* Retrieval of temporary AWS credentials (`AccessKey`, `SecretKey`, `SecurityToken`)
* Upload of CSV and Manifest to the S3 bucket
* Uses `boto3`

---

### Certificate and private key management (disabled)

> ⚠️ Certificate download from OBS and private key retrieval via CSMS are currently disabled (commented code).

Planned flow:

* Download of `cert.pem` and `ca_bundle.crt` from the OBS bucket (`certs/` directory)
* Retrieval of `key.pem` content via **CSMS** (Cloud Secret Management Service)
* Local file writing for Vault authentication

---