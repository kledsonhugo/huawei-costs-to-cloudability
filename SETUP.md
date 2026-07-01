# Setup

Instruções para setup da integração de dados de custos da **Huawei Cloud** com a ferramenta **Cloudability**.

> 💡 Parâmetros sugeridos para cenários de experimentação. Valide conforme os padrões de arquitetura utilizados.

# Parte I: Geração dos dados de custos (Conta Master)

## KMS

No serviço **Data Encryption Workshop** selecione o menu **Key Management Service**, clique em **Create Key** e digite os valores de configuração.

Parâmetro:

- Name: `kms-cloudability`

Acessar a **KMS** criada, e no menu **Grants** clique em **Create Grant** para cada uma das contas abaixo.

1. Conta Huawei

   - Account: `<ID_CONTA_HUAWEI>`
   - Name: `grant-huawei-cloudability`
   - Granted Operations
     - `Create Data Key`
     - `Decrypt Data Key`
     - `Query Key Information`

2. Conta Centralizada

   - Account: `<ID_CONTA_CENTRALIZADA>`
   - Name: `grant-centralized-cloudability`
   - Granted Operations
     - `Create Data Key`
     - `Decrypt Data Key`
     - `Query Key Information`

## OBS

No serviço **OBS** clique em **Create Bucket** e digite os valores de configuração.

Parâmetro:

- Bucket Name: `<OBS_MASTER_BUCKET>` ou outro nome disponível
- Block Public Access: `Enabled seetings: 4`
- Bucket Policy: `Private`
- Server-Side Encryption: `Enabled`
- Encryption Method: `SSE-KMS`
- Encryption Key Type: `Custom`
- Custom: `kms-cloudability`

Acessar o bucket **OBS** criado, selecione no menu à esquerda a opção **Bucket Policies**, clique em **Create** e digite os valores de configuração.

Parâmetro:

- Policy Name: `obs-policy-cloudability`
- Principal: `Other accounts` e insira o ID da **Conta Centralizada**
- Resources: `Entire bucket (including the objects in it)`
- Actions: `Bucket Read-Only`


## Cost Details Export

No serviço **Cost Center**, selecione no menu à esquerda a opção **Cost Details Export**.

Em **Export to OBS** clique em **Create Export Task** e digite os valores de configuração.

Parâmetro:

- Task Name: `export-to-cloudability`
- Bucket Name: `<OBS_MASTER_BUCKET>`
- Bucket Directory Prefix: `huawei`
- Content Type: `Cost details (FOCUS 1.0)`

> Valide dados gerados automaticamente no bucket OBS em até 48hs. Caso não tenha dados contate o suporte.

# Parte II: Transferência dos dados de custos (Conta Centralizada)

## VPC

Selecione o serviço **VPC**, clique em **Create VPC** e digite os valores de configuração.

Parâmetros:

- VPC Name: `vpc-cloudability`
- VPC IPv4 CIDR Block: `10.0.0.0/16`
- Subnet Name: `subnet-cloudability`
- Subnet IPv4 CIDR Block: `10.0.1.0/24`

## VPC Endpoint

No serviço **VPC** selecione o menu **VPC Endpoints**, clique em **Buy VPC Endpoint** e digite os valores de configuração.

Parâmetros:

- Service List: `com.myhuaweicloud.<REGION>.obs`
- VPC: `vpc-cloudability`
- Route Table: `rtb-vpc-cloudability`

## KMS

No serviço **Data Encryption Workshop** selecione o menu **Key Management Service**, clique em **Create Key** e digite os valores de configuração.

Parâmetro:

- Name: `kms-cloudability-target`

## OBS

No serviço **OBS** clique em **Create Bucket** e digite os valores de configuração.

Parâmetro:

- Bucket Name: `<OBS_TARGET_BUCKET>` ou outro nome disponível
- Block Public Access: `Enabled seetings: 4`
- Bucket Policy: `Private`
- Server-Side Encryption: `Enabled`
- Encryption Method: `SSE-KMS`
- Encryption Key Type: `Custom`
- Custom: `kms-cloudability-target`

## Agency para FunctionGraph

No serviço **IAM** selecione o menu **Permissions > Policies/Roles**, clique em **Create Custom Policy** para cada uma das policies abaixo.

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
                     "KMS:*:*:KeyId:<KMS_KEY_ID_CONTA_CENTRALIZADA>"
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

3. OBS (Leitura no bucket da Conta Master)

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

4. OBS (Leitura e gravação no bucket da Conta Centralizada)

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

No serviço **IAM** selecione o menu **Agencies**, clique em **Create Agency** e digite os valores de configuração.

Parâmetros:

- Agency Name: `agency-cloudability-fg`
- Agency Type: `Cloud Service`
- Cloud Service: `FunctionGraph`

Autorize a **Agency** para as policies abaixo:

- `policy-cloudability-kms-readonly`
- `policy-cloudability-vpc-readonly`
- `policy-cloudability-obs-master-readonly`
- `policy-cloudability-obs-target-readwrite`

## FunctionGraph

### Criação da Function

No serviço **FunctionGraph** clique em **Create Function** e digite os valores de configuração.

Parâmetro:

- Function Type: `Event Function`
- Function Name: `fg-cloudability`
- Agency: `fg-cloudability`
- Runtime: `Python 3.9`
- Public Access: `Disabled`
- VPC Access: `Enabled`
- VPC: `vpc-cloudability`
- Subnet: `subnet-cloudability`

---

### Configuração da Function

No menu **Configuration** ajuste os valores de configuração.

- Basic Settings
  - Execution Timeout (s): `30`
- Trigger
  - Trigger Type: `Time`
  - Timer Name: `timer-cloudability`
  - Rule: `Cron expression`
    - `0 6 * * * ?`
- Environment Variables:

  | Variável          | Exemplo de Valor                              | Descrição            |
  |-------------------|-----------------------------------------------|----------------------|
  | `SOURCE_BUCKET`   | `<OBS_MASTER_BUCKET>`                  | Source Bucket OBS    |
  | `TARGET_BUCKET`   | `<OBS_TARGET_BUCKET>`                  | Target Bucket OBS    |
  | `OBS_ENDPOINT`    | `https://obs.<REGION>.myhuaweicloud.com`   | Endpoint OBS         |

---

### Deploy da Function

Subir código `index.py` e executar a function.

## FunctionGraph S3

Function responsável por ler os arquivos do bucket OBS de destino e enviá-los para o bucket S3 na AWS.

### Criação da Function

No serviço **FunctionGraph** clique em **Create Function** e digite os valores de configuração.

Parâmetro:

- Function Type: `Event Function`
- Function Name: `fg-cloudability-s3`
- Agency: `agency-cloudability-fg`
- Runtime: `Python 3.9`
- Public Access: `Disabled`
- VPC Access: `Enabled`
- VPC: `vpc-cloudability`
- Subnet: `subnet-cloudability`

---

### Configuração da Function

No menu **Configuration** ajuste os valores de configuração.

- Basic Settings
  - Execution Timeout (s): `60`
- Trigger
  - Trigger Type: `Time`
  - Timer Name: `timer-cloudability-s3`
  - Rule: `Cron expression`
    - `0 7 * * * ?`
- Environment Variables:

  | Variável              | Exemplo de Valor                                      | Descrição                          |
  |-----------------------|-------------------------------------------------------|------------------------------------|
  | `OBS_ENDPOINT` | `https://obs.<REGION>.myhuaweicloud.com` | Endpoint OBS |
  | `OBS_SOURCE_BUCKET` | `<OBS_TARGET_BUCKET>` | Bucket OBS de origem (com CSV+JSON) |
  | `S3_BUCKET` | `<S3_BUCKET>` | Bucket S3 de destino na AWS |
  | `VAULT_URL` | `https://<vault_domain>/v1/auth/cert/login` | URL login Vault |
  | `VAULT_AWS_PATH`      | `https://<vault_domain>/v1/aws/creds/<role>` | Path creds AWS Vault |
  | `AWS_REGION`          | `<AWS_REGION>`                                           | Região AWS                         |

---

### Certificados

Incluir os arquivos de certificado no diretório `certificado/` do código da function:

```
certificado/
├── cert1.pem        # Certificado TLS do cliente
├── key.pem          # Chave privada do cliente
└── ca_bundle.crt    # CA bundle para validação do servidor Vault
```

O caminho montado na function será `/opt/function/code/certificado/`.

---

### Deploy da Function

Subir código `index_s3.py` e o diretório `certificado/`, e executar a function.

---