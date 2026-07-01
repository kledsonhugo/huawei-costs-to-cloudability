# Costs Details Export to Cloudability

Process to automate the extraction and sending of **Huawei Cloud** cost data to the **Cloudability** tool.

## Architecture Diagram

Diagram of the solution considering the resources and operational flow.

![Diagram](images/diagram.png)

## Solution

The solution comprises three macro functionalities:

1. **Scheduled Task** in the **Cost Details Export** of the **Billing > Cost Center** service on the **Master Account**, which daily generates cost data from all accounts in the organization into an **OBS** bucket on the **Master Account** itself.

2. **FunctionGraph** (`fg-cloudability`) in a **Centralized Account**, which extracts cost data from the **OBS** bucket on the **Master Account** and sends it to an **OBS** bucket on the **Centralized Account**.

3. **FunctionGraph** (`fg-cloudability-s3`) in a **Centralized Account**, which reads the cost files (CSV) and metadata (JSON) from the **OBS** bucket on the **Centralized Account** and sends them to an **S3** bucket on **AWS**.

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
  "root_dir": "target-bucket",
  "all_report_keys": [
    "Huawei/20260301-20260401/1714521600/1714521600.csv"
  ],
  "focus_version": "1.0"
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
* Filter by prefix `Huawei/`
* Ignores objects with size 0

---

### Identifying the most recent CSV + JSON pair

* Filters `.csv` and `.json` files
* Pairs CSV and JSON by name (same epoch)
* Selects the most recent pair (highest epoch)

---

### File download

* Download CSV to `/tmp/<epoch>.csv`
* Download JSON to `/tmp/<epoch>.json`

---

### Vault authentication

* Login via TLS certificate (`cert1.pem` + `key.pem`)
* Validation with CA bundle (`ca_bundle.crt`)
* Obtains `client_token`

---

### AWS credentials retrieval

* Request to Vault with `client_token`
* Returns temporary `AccessKey`, `SecretKey`, and `SecurityToken`

---

### Upload to AWS S3

Operations performed:

1. Checks CSV existence in S3 (`head_object`)
2. If it does not exist:

   * Upload the CSV
   * Upload the JSON

3. Cleanup of temporary files
* Uses `boto3`
* Disabled by default

---