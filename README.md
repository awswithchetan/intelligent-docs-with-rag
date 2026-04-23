# Intelligent Docs — HR Policy Assistant

A serverless RAG (Retrieval-Augmented Generation) application that lets employees ask natural language questions about company HR policy documents. Built on AWS using Amazon Bedrock, S3 Vectors, and Cognito.

## Architecture

```
Browser (localhost)
    │
    ├── Cognito Hosted UI (login)
    │
    └── API Gateway
            ├── POST /upload-url  → Lambda (presigned S3 URL) [admin only]
            ├── GET  /docs        → Lambda (list indexed docs)
            └── POST /search      → Lambda (RAG query)

S3 (docs/*.pdf) ──► S3 Event ──► Ingest Lambda
                                      ├── pypdf (text extraction)
                                      ├── Bedrock Titan Embed v2 (embeddings)
                                      └── S3 Vectors (store)

S3 Vectors ◄──► Search Lambda ──► Bedrock Claude 3 Haiku (answer generation)
```

## Prerequisites

- AWS account with access to `ap-south-1` (Mumbai)
- Bedrock model access enabled for:
  - `amazon.titan-embed-text-v2:0`
  - `anthropic.claude-3-haiku-20240307-v1:0`
- AWS CLI configured locally

---

## Deployment Steps

### 1. S3 Bucket (document storage)

Create a standard S3 bucket (e.g. `intelligent-docs-app`) in `ap-south-1`.

Add a CORS configuration to allow browser uploads:
```json
[{
  "AllowedHeaders": ["*"],
  "AllowedMethods": ["GET", "PUT", "POST"],
  "AllowedOrigins": ["*"],
  "ExposeHeaders": []
}]
```

### 2. S3 Vector Bucket & Index

In the S3 console, go to **Vector buckets** (left sidebar) → **Create vector bucket**.

- Name: `intelligent-docs-vectors`
- Encryption: SSE-S3

Inside the bucket, create a **vector index**:
- Name: `hr-policy-index`
- Dimension: `512`
- Distance metric: `Cosine`
- Non-filterable metadata key: `text`

### 3. IAM Role for Lambda

Create an IAM role named `intelligent-docs-lambda-role` with trust policy for `lambda.amazonaws.com`.

Attach the following managed policies:
- `AWSLambdaBasicExecutionRole`

Add an inline policy with these permissions:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::intelligent-docs-app",
        "arn:aws:s3:::intelligent-docs-app/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3vectors:PutVectors",
        "s3vectors:QueryVectors",
        "s3vectors:DeleteVectors",
        "s3vectors:GetVectors",
        "s3vectors:ListVectors"
      ],
      "Resource": [
        "arn:aws:s3vectors:ap-south-1:ACCOUNT_ID:bucket/intelligent-docs-vectors",
        "arn:aws:s3vectors:ap-south-1:ACCOUNT_ID:bucket/intelligent-docs-vectors/index/hr-policy-index"
      ]
    }
  ]
}
```

Replace `ACCOUNT_ID` with your AWS account ID.

### 4. Lambda Layer (pypdf dependency)

The ingest Lambda requires `pypdf`. Package it as a Lambda Layer so all function code can be pasted inline — no zip uploads needed.

On your local machine:
```bash
mkdir -p python
pip install pypdf==4.0.1 -t python/
zip -r pypdf-layer.zip python/
```

In the Lambda console → **Layers** → **Create layer**:
- Name: `pypdf-layer`
- Upload `pypdf-layer.zip`
- Compatible runtime: Python 3.12

### 5. Lambda Functions

Create four Lambda functions, all with:
- Runtime: Python 3.12
- Region: ap-south-1
- Role: `intelligent-docs-lambda-role`

Paste the code directly in the inline editor (no zip required).

#### 5a. intelligent-docs-ingest
- Handler: `lambda_function.lambda_handler`
- Timeout: 300s | Memory: 512 MB
- Code: paste contents of `backend/ingest/lambda_function_s3vectors.py` as `lambda_function.py`
- **Add layer**: attach the `pypdf-layer` created above
- Environment variables:

| Key | Value |
|-----|-------|
| `DOCS_BUCKET` | `intelligent-docs-app` |
| `VECTOR_BUCKET` | `intelligent-docs-vectors` |
| `VECTOR_INDEX` | `hr-policy-index` |
| `EMBED_MODEL` | `amazon.titan-embed-text-v2:0` |
| `CHUNK_SIZE` | `200` |
| `CHUNK_OVERLAP` | `20` |

#### 5b. intelligent-docs-search
- Handler: `lambda_function.lambda_handler`
- Timeout: 60s | Memory: 512 MB
- Code: paste contents of `backend/search/lambda_function_s3vectors.py` as `lambda_function.py`
- Environment variables:

| Key | Value |
|-----|-------|
| `VECTOR_BUCKET` | `intelligent-docs-vectors` |
| `VECTOR_INDEX` | `hr-policy-index` |
| `EMBED_MODEL` | `amazon.titan-embed-text-v2:0` |
| `LLM_MODEL` | `anthropic.claude-3-haiku-20240307-v1:0` |
| `TOP_K` | `15` |

#### 5c. intelligent-docs-list
- Handler: `lambda_function.lambda_handler`
- Timeout: 30s | Memory: 256 MB
- Code: paste contents of `backend/docs/lambda_function.py`
- Environment variables:

| Key | Value |
|-----|-------|
| `DOCS_BUCKET` | `intelligent-docs-app` |

#### 5d. intelligent-docs-upload
- Handler: `docs_upload_url.lambda_handler`
- Timeout: 15s | Memory: 256 MB
- Code: paste contents of `backend/upload_url/docs_upload_url.py`
- Environment variables:

| Key | Value |
|-----|-------|
| `DOCS_BUCKET` | `intelligent-docs-app` |

### 6. S3 Event Trigger

On the `intelligent-docs-app` bucket, add an event notification:
- Event types: `s3:ObjectCreated:*` and `s3:ObjectRemoved:*`
- Prefix filter: `docs/`
- Suffix filter: `.pdf`
- Destination: Lambda → `intelligent-docs-ingest`

Grant S3 permission to invoke the Lambda (S3 will prompt for this in the console).

### 6. API Gateway

Create a REST API named `intelligent-docs-api`.

Create three resources and methods:

| Resource | Method | Lambda | Auth |
|----------|--------|--------|------|
| `/upload-url` | POST | intelligent-docs-upload | Cognito |
| `/search` | POST | intelligent-docs-search | Cognito |
| `/docs` | GET | intelligent-docs-list | Cognito |

For each resource, also add an `OPTIONS` method (for CORS) with:
- Integration type: Mock
- Response headers:
  - `Access-Control-Allow-Origin: '*'`
  - `Access-Control-Allow-Headers: 'Content-Type,Authorization'`
  - `Access-Control-Allow-Methods: 'GET,POST,OPTIONS'`

Enable CORS on each Lambda integration response as well.

Deploy the API to a stage named `prod`.

### 7. Cognito User Pool

Create a User Pool named `intelligent-docs-users`:
- Sign-in: email
- Password policy: min 8 chars, upper + lower + numbers
- No MFA required (for demo)

Create two groups: `admins` and `employees`.

Create an App Client named `intelligent-docs-app`:
- No client secret
- OAuth flows: Authorization code grant + Implicit grant
- OAuth scopes: `openid`, `email`, `profile`
- Callback URLs: add your localhost URL (e.g. `http://localhost:8081`)
- Logout URLs: same as callback URLs

Set up a Cognito domain (e.g. `intelligent-docs-auth`).

#### Add Cognito Authorizer to API Gateway

In API Gateway, create a Cognito authorizer:
- Type: Cognito User Pools
- User Pool: `intelligent-docs-users`
- Token source: `method.request.header.Authorization`

Attach this authorizer to the POST methods on `/upload-url`, `/search`, and GET on `/docs`.

Redeploy the API after attaching the authorizer.

### 8. Create Users

In Cognito → User Pool → Users, create:
- An admin user → add to `admins` group
- An employee user → add to `employees` group

### 9. Frontend Configuration

Edit `frontend/index.html` and update the config block at the top of the `<script>` section:

```javascript
const API_BASE = "https://YOUR_API_ID.execute-api.ap-south-1.amazonaws.com/prod";
const COGNITO = {
  domain:   "https://YOUR_DOMAIN.auth.ap-south-1.amazoncognito.com",
  clientId: "YOUR_APP_CLIENT_ID",
  redirect: window.location.origin + window.location.pathname
};
```

### 10. Run Locally

Serve `frontend/index.html` on the port you registered in Cognito (e.g. port 8081):

```bash
# Python
python3 -m http.server 8081 --directory frontend/

# Node (npx)
npx serve frontend/ -p 8081
```

Open `http://localhost:8081` in your browser.

---

## Usage

1. Sign in with an admin account → upload policy PDFs via the Upload tab
2. Wait for "✓ Indexed" status (indexing takes ~30s per document)
3. Click the Documents tab to verify indexed documents
4. Ask questions in the chat — answers are generated from the uploaded documents
5. Sign in with an employee account → upload tab is hidden, queries work normally

## Sample Documents

Ready-to-use sample policy documents are in `sample-docs/`:
- `Employee_Handbook.pdf`
- `Leave_Policy.pdf`
- `WFH_Policy.pdf`
- `IT_Security_Policy.pdf`
- `Code_of_Conduct.pdf`
- `New_Joiner_Onboarding.pdf`

## Tech Stack

| Component | Service |
|-----------|---------|
| Vector storage | Amazon S3 Vectors |
| Embeddings | Amazon Titan Embed Text v2 (512d) |
| Answer generation | Anthropic Claude 3 Haiku (via Bedrock) |
| PDF processing | pypdf |
| Auth | Amazon Cognito (hosted UI, implicit flow) |
| API | Amazon API Gateway + Lambda (Python 3.12) |
| Document storage | Amazon S3 |
