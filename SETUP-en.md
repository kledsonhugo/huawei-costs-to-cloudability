# Setup

Instructions for setting up the integration of **Huawei Cloud** cost data with the **Cloudability** tool.

> 💡 Suggested parameters for experimentation scenarios. Validate according to the architecture standards used.

# Part I: Cost data generation (Master Account)

## KMS

In the **Data Encryption Workshop** service, select the **Key Management Service** menu, click **Create Key**, and enter the configuration values.

Parameter:

- Name: `kms-cloudability`

Access the created **KMS**, and in the **Grants** menu, click **Create Grant** for each of the accounts below.

1. Huawei Account

   - Account: `<MASTER_ACCOUNT_ID>`
   - Name: `grant-huawei-cloudability`
   - Granted Operations
     - `Create Data Key`
     - `Decrypt Data Key`
     - `Query Key Information`

2. Centralized Account

   - Account: `<CENTRALIZED_ACCOUNT_ID>`
   - Name: `grant-centralized-cloudability`
   - Granted Operations
     - `Create Data Key`
     - `Decrypt Data Key`
     - `Query Key Information`

## OBS

In the **OBS** service, click **Create Bucket** and enter the configuration values.

Parameter:

- Bucket Name: `<OBS_MASTER_BUCKET>` or another available name
- Block Public Access: `Enabled settings: 4`
- Bucket Policy: `Private`
- Server-Side Encryption: `Enabled`
- Encryption Method: `SSE-KMS`
- Encryption Key Type: `Custom`
- Custom: `kms-cloudability`

Access the created **OBS** bucket, select **Bucket Policies** from the left menu, click **Create**, and enter the configuration values.

Parameter:

- Policy Name: `obs-policy-cloudability`
- Principal: `Other accounts` and insert the **Centralized Account** ID
- Resources: `Entire bucket (including the objects in it)`
- Actions: `Bucket Read-Only`


## Cost Details Export

In the **Cost Center** service, select **Cost Details Export** from the left menu.

Under **Export to OBS**, click **Create Export Task** and enter the configuration values.

Parameter:

- Task Name: `export-to-cloudability`
- Bucket Name: `<OBS_MASTER_BUCKET>`
- Bucket Directory Prefix: `huawei`
- Content Type: `Cost details (FOCUS 1.0)`

> Validate data automatically generated in the OBS bucket within up to 48 hours. If no data is generated, contact support.

# Part II: Cost data transfer (Centralized Account)

## VPC

Select the **VPC** service, click **Create VPC**, and enter the configuration values.

Parameters:

- VPC Name: `vpc-cloudability`
- VPC IPv4 CIDR Block: `10.0.0.0/16`
- Subnet Name: `subnet-cloudability`
- Subnet IPv4 CIDR Block: `10.0.1.0/24`

## VPC Endpoint

In the **VPC** service, select the **VPC Endpoints** menu, click **Buy VPC Endpoint**, and enter the configuration values.

Parameters:

- Service List: `com.myhuaweicloud.<REGION>.obs`
- VPC: `vpc-cloudability`
- Route Table: `rtb-vpc-cloudability`

## KMS

In the **Data Encryption Workshop** service, select the **Key Management Service** menu, click **Create Key**, and enter the configuration values.

Parameter:

- Name: `kms-cloudability-target`

## OBS

In the **OBS** service, click **Create Bucket** and enter the configuration values.

Parameter:

- Bucket Name: `<OBS_TARGET_BUCKET>` or another available name
- Block Public Access: `Enabled settings: 4`
- Bucket Policy: `Private`
- Server-Side Encryption: `Enabled`
- Encryption Method: `SSE-KMS`
- Encryption Key Type: `Custom`
- Custom: `kms-cloudability-target`

## Agency for FunctionGraph

In the **IAM** service, select the **Permissions > Policies/Roles** menu, click **Create Custom Policy** for each of the policies below.

1. KMS

   - Policy Name: `policy-cloudability-kms-readonly`
   - Policy Content:
     ```
     {
         "Version": "1.1",
         "Statement": [
             {
                 "Effect": "Allow",
                 "Action": [
                     "kms:cmk:get",
                     "kms:dek:create",
                     "kms:dek:decrypt"
                 ],
                 "Resource": [
                     "KMS:*:*:KeyId:<MASTER_ACCOUNT_KMS_KEY_ID>",
                     "KMS:*:*:KeyId:<CENTRALIZED_ACCOUNT_KMS_KEY_ID>"
                 ]
             }
         ]
     }
     ```

   > The `MASTER_ACCOUNT_KMS_KEY_ID` is required for the function to decrypt objects from the source bucket (SSE-KMS). Without this permission, downloads return error 403.

2. VPC

   - Policy Name: `policy-cloudability-vpc-readonly`
   - Policy Content:
     ```
     {
         "Version": "1.1",
         "Statement": [
             {
                 "Effect": "Allow",
                 "Action": [
                     "vpc:ports:get",
                     "vpc:ports:create",
                     "vpc:vpcs:get",
                     "vpc:subnets:get"
                 ]
             }
         ]
     }
     ```

3. OBS (Read access to the Master Account bucket)

   - Policy Name: `policy-cloudability-obs-master-readonly`
   - Policy Content:
     ```
     {
         "Version": "1.1",
         "Statement": [
             {
                 "Effect": "Allow",
                 "Action": [
                     "obs:object:GetObject",
                     "obs:bucket:HeadBucket",
                     "obs:bucket:ListBucket"
                 ],
                 "Resource": [
                     "OBS:*:*:object:<OBS_MASTER_BUCKET>/*",
                     "OBS:*:*:bucket:<OBS_MASTER_BUCKET>"
                 ]
             }
         ]
     }
     ```

4. OBS (Read and write access to the Centralized Account bucket)

   - Policy Name: `policy-cloudability-obs-target-readwrite`
   - Policy Content:
     ```
     {
         "Version": "1.1",
         "Statement": [
             {
                 "Effect": "Allow",
                 "Action": [
                     "obs:object:GetObject",
                     "obs:bucket:HeadBucket",
                     "obs:object:PutObject",
                     "obs:bucket:ListBucket"
                 ],
                 "Resource": [
                     "OBS:*:*:object:<OBS_TARGET_BUCKET>/*",
                     "OBS:*:*:bucket:<OBS_TARGET_BUCKET>"
                 ]
             }
         ]
     }
     ```

In the **IAM** service, select the **Agencies** menu, click **Create Agency**, and enter the configuration values.

Parameters:

- Agency Name: `agency-cloudability-fg`
- Agency Type: `Cloud Service`
- Cloud Service: `FunctionGraph`

Authorize the **Agency** for the policies below:

- `policy-cloudability-kms-readonly`
- `policy-cloudability-vpc-readonly`
- `policy-cloudability-obs-master-readonly`
- `policy-cloudability-obs-target-readwrite`

## CSMS

In the **Data Encryption Workshop** service, select the **Cloud Secret Management Service** menu, click **Create Secret**, and enter the configuration values.

Parameter:

- Secret Name: `csms-cloudability-key`
- Secret Type: `Text`
- Secret Value: `<contents of key.pem>`

> The private key contents (`key.pem`) are stored as a secret in CSMS and retrieved at runtime by the `fg-cloudability-s3` function.

## FunctionGraph

### Function creation

In the **FunctionGraph** service, click **Create Function** and enter the configuration values.

Parameter:

- Function Type: `Event Function`
- Function Name: `fg-cloudability`
- Agency: `agency-cloudability-fg`
- Runtime: `Python 3.9`
- Public Access: `Disabled`
- VPC Access: `Enabled`
- VPC: `vpc-cloudability`
- Subnet: `subnet-cloudability`

---

### Function configuration

In the **Configuration** menu, adjust the configuration values.

- Basic Settings
  - Execution Timeout (s): `30`
- Trigger
  - Trigger Type: `Time`
  - Timer Name: `timer-cloudability`
  - Rule: `Cron expression`
    - `0 6 * * * ?`
- Environment Variables:

  | Variable            | Example Value                            | Description                        |
  |---------------------|---------------------------------------------|----------------------------------|
  | `OBS_ENDPOINT`      | `https://obs.<REGION>.myhuaweicloud.com`   | OBS Endpoint                     |
  | `OBS_SOURCE_BUCKET` | `<OBS_MASTER_BUCKET>`                       | Source OBS bucket (Master Account) |
  | `OBS_TARGET_BUCKET` | `<OBS_TARGET_BUCKET>`                       | Target OBS bucket              |
  | `S3_BUCKET`         | `<S3_BUCKET>`                               | S3 bucket on AWS (referenced in metadata) |

---

### Function deployment

Upload the `index.py` code and execute the function.

## FunctionGraph S3

Function responsible for reading files from the target OBS bucket. The upload to the S3 bucket on AWS is currently disabled.

### Function creation

In the **FunctionGraph** service, click **Create Function** and enter the configuration values.

Parameter:

- Function Type: `Event Function`
- Function Name: `fg-cloudability-s3`
- Agency: `agency-cloudability-fg`
- Runtime: `Python 3.9`
- Public Access: `Disabled`
- VPC Access: `Enabled`
- VPC: `vpc-cloudability`
- Subnet: `subnet-cloudability`

---

### Function configuration

In the **Configuration** menu, adjust the configuration values.

- Basic Settings
  - Execution Timeout (s): `60`
- Trigger
  - Trigger Type: `Time`
  - Timer Name: `timer-cloudability-s3`
  - Rule: `Cron expression`
    - `0 7 * * * ?`
- Environment Variables:

  | Variable            | Example Value                                      | Description                          |
  |---------------------|-------------------------------------------------------|------------------------------------|
  | `OBS_ENDPOINT`      | `https://obs.<REGION>.myhuaweicloud.com`             | OBS Endpoint                       |
  | `OBS_BUCKET`        | `<OBS_TARGET_BUCKET>`                                 | Source OBS bucket (with CSV+JSON) |
  | `CSMS_SECRET_NAME`  | `csms-cloudability-key`                               | CSMS secret name             |
  | `CSMS_REGION`       | `<REGION>`                                            | CSMS region                     |
  | `CSMS_ENDPOINT`     | `https://kms.<REGION>.myhuaweicloud.com`             | CSMS endpoint (VPC endpoint)    |
  | `CSMS_PROJECT_ID`   | `<PROJECT_ID>`                                        | CSMS project ID                 |
  | `CERT_DIR`          | `/tmp/certificado`                                    | Local certificate directory   |
  | `CERT_FILE`         | `cert.pem`                                            | Certificate filename     |
  | `KEY_FILE`          | `key.pem`                                             | Private key filename   |
  | `CA_BUNDLE`         | `ca_bundle.crt`                                       | CA bundle filename                  |

> ⚠️ Variables `VAULT_URL`, `VAULT_AWS_PATH`, `AWS_REGION`, and `S3_BUCKET` are not currently used (S3 upload disabled).

---

### Certificates

The certificates (`cert.pem` and `ca_bundle.crt`) must be stored in the source OBS bucket, in the `certs/` directory:

```
certs/
├── cert.pem        # Client TLS certificate
└── ca_bundle.crt    # CA bundle for Vault server validation
```

The private key (`key.pem`) is retrieved via **CSMS** at runtime and is not stored in the bucket.

> ⚠️ Certificate download from OBS and key retrieval via CSMS are currently disabled (commented code).

---

### Function deployment

Upload the `s3.py` code and execute the function.

---