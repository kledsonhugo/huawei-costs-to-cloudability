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
                     "KMS:*:*:KeyId:<CENTRALIZED_ACCOUNT_KMS_KEY_ID>"
                 ]
             }
         ]
     }
     ```

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

## FunctionGraph

### Function creation

In the **FunctionGraph** service, click **Create Function** and enter the configuration values.

Parameter:

- Function Type: `Event Function`
- Function Name: `fg-cloudability`
- Agency: `fg-cloudability`
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

  | Variable          | Example Value                              | Description            |
  |-------------------|-----------------------------------------------|----------------------|
  | `SOURCE_BUCKET`   | `<OBS_MASTER_BUCKET>`                  | Source Bucket OBS    |
  | `TARGET_BUCKET`   | `<OBS_TARGET_BUCKET>`                  | Target Bucket OBS    |
  | `OBS_ENDPOINT`    | `https://obs.<REGION>.myhuaweicloud.com`   | Endpoint OBS         |

---

### Function deployment

Upload the `index.py` code and execute the function.

## FunctionGraph S3

Function responsible for reading files from the target OBS bucket and sending them to the S3 bucket on AWS.

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

  | Variable              | Example Value                                      | Description                          |
  |-----------------------|-------------------------------------------------------|------------------------------------|
  | `OBS_ENDPOINT` | `https://obs.<REGION>.myhuaweicloud.com` | OBS Endpoint |
  | `OBS_SOURCE_BUCKET` | `<OBS_TARGET_BUCKET>` | Source OBS bucket (with CSV+JSON) |
  | `S3_BUCKET` | `<S3_BUCKET>` | Destination S3 bucket on AWS |
  | `VAULT_URL` | `https://<vault_domain>/v1/auth/cert/login` | Vault login URL |
  | `VAULT_AWS_PATH`      | `https://<vault_domain>/v1/aws/creds/<role>` | Vault AWS creds path |
  | `AWS_REGION`          | `<AWS_REGION>`                                           | AWS Region                         |

---

### Certificates

Include the certificate files in the `certificado/` directory of the function code:

```
certificado/
├── cert1.pem        # Client TLS certificate
├── key.pem          # Client private key
└── ca_bundle.crt    # CA bundle for Vault server validation
```

The mounted path in the function will be `/opt/function/code/certificado/`.

---

### Function deployment

Upload the `index_s3.py` code and the `certificado/` directory, and execute the function.

---