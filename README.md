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

## Prerequisites

- AWS account
- Bedrock model access: as of late 2025, all serverless models are **automatically enabled** — no manual activation needed
- Python 3 and `pip` installed locally (for building the Lambda layer)
- AWS CLI configured locally

> All steps below use `ap-south-1` (Mumbai) as an example. You can use any AWS region that supports Bedrock, S3 Vectors, and Cognito — just replace `ap-south-1` consistently throughout.

---

## Deployment Steps

### 1. S3 Bucket (document storage)

Create a standard S3 bucket (e.g. `intelligent-docs-app`) in your chosen region.

Add a CORS configuration to allow browser uploads via presigned URLs (the bucket itself remains private):
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
- Name: `intelligent-docs-hr-policy-index`
- Dimension: `512` (must match the embedding model output size — Titan Embed v2 produces 512-dimensional vectors)
- Distance metric: `Cosine` (measures similarity by angle between vectors, best for text embeddings)
- Non-filterable metadata key: `text` (stores the original chunk text alongside the vector for retrieval)

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
        "arn:aws:s3:::<docs-bucket-name>",
        "arn:aws:s3:::<docs-bucket-name>/*"
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
        "aws-marketplace:Subscribe",
        "aws-marketplace:Unsubscribe",
        "aws-marketplace:ViewSubscriptions"
      ],
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
        "arn:aws:s3vectors:<region>:<account-id>:bucket/<vector-bucket-name>",
        "arn:aws:s3vectors:<region>:<account-id>:bucket/<vector-bucket-name>/index/<vector-index-name>"
      ]
    }
  ]
}
```

Replace `<docs-bucket-name>`, `<vector-bucket-name>`, `<vector-index-name>`, `<region>`, and `<account-id>` with your actual values.

### 4. Lambda Layer (pypdf dependency)

The ingest Lambda requires `pypdf`. Package it as a Lambda Layer so all function code can be pasted inline — no zip uploads needed.

On your local machine (or WSL terminal):
```bash
mkdir -p python
pip install pypdf==4.0.1 -t python/
zip -r pypdf-layer.zip python/
```

Then create the layer via AWS CLI (no need to copy the zip anywhere):
```bash
aws lambda publish-layer-version \
  --layer-name intelligent-docs-pypdf-layer \
  --zip-file fileb://pypdf-layer.zip \
  --compatible-runtimes python3.12 \
  --region YOUR_REGION
```

Note the `LayerVersionArn` from the output — you'll need it when attaching the layer to the ingest Lambda.

Alternatively, via the Lambda console → **Layers** → **Create layer**:
- Name: `intelligent-docs-pypdf-layer`
- Upload `pypdf-layer.zip`
- Compatible runtime: Python 3.12

### 5. Lambda Functions

Create four Lambda functions, all with:
- Runtime: Python 3.12
- Region: your chosen region
- Role: `intelligent-docs-lambda-role`

Paste the code directly in the inline editor (no zip required).

#### 5a. intelligent-docs-ingest
- Handler: `lambda_function.lambda_handler`
- Timeout: 300s | Memory: 512 MB
- Code: paste contents of `backend/ingest/lambda_function_s3vectors.py` as `lambda_function.py`
- **Add layer**: attach the `intelligent-docs-pypdf-layer` created above
- Environment variables:

| Key | Value | Description |
|-----|-------|-------------|
| `DOCS_BUCKET` | `intelligent-docs-app` | S3 bucket where PDFs are stored |
| `VECTOR_BUCKET` | `intelligent-docs-vectors` | S3 vector bucket for storing embeddings |
| `VECTOR_INDEX` | `intelligent-docs-hr-policy-index` | Vector index to write chunks into |
| `EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | Bedrock model used to generate embeddings |
| `CHUNK_SIZE` | `200` | Number of words per text chunk |
| `CHUNK_OVERLAP` | `20` | Number of overlapping words between consecutive chunks |

#### 5b. intelligent-docs-search
- Handler: `lambda_function.lambda_handler`
- Timeout: 60s | Memory: 512 MB
- Code: paste contents of `backend/search/lambda_function_s3vectors.py` as `lambda_function.py`
- Environment variables:

| Key | Value | Description |
|-----|-------|-------------|
| `VECTOR_BUCKET` | `intelligent-docs-vectors` | S3 vector bucket to query |
| `VECTOR_INDEX` | `intelligent-docs-hr-policy-index` | Vector index to search against |
| `EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | Bedrock model used to embed the user's question |
| `LLM_MODEL` | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | Bedrock model used to generate the answer |
| `TOP_K` | `15` | Number of most similar chunks to retrieve from the index |

#### 5c. intelligent-docs-list
- Handler: `lambda_function.lambda_handler`
- Timeout: 30s | Memory: 256 MB
- Code: paste contents of `backend/docs/lambda_function.py`
- Environment variables:

| Key | Value | Description |
|-----|-------|-------------|
| `DOCS_BUCKET` | `intelligent-docs-app` | S3 bucket to list documents from |
| `VECTOR_BUCKET` | `intelligent-docs-vectors` | S3 vector bucket to list vectors from |
| `VECTOR_INDEX` | `intelligent-docs-hr-policy-index` | Vector index to list documents from |

#### 5d. intelligent-docs-upload
- Handler: `lambda_function.lambda_handler`
- Timeout: 15s | Memory: 256 MB
- Code: paste contents of `backend/upload_url/lambda_function.py` as `lambda_function.py`
- Environment variables:

| Key | Value | Description |
|-----|-------|-------------|
| `DOCS_BUCKET` | `intelligent-docs-app` | S3 bucket to generate the presigned upload URL for |

### 6. S3 Event Trigger

On the `intelligent-docs-app` bucket, add an event notification:
- Event types: `s3:ObjectCreated:*` and `s3:ObjectRemoved:*`
- Prefix filter: `docs/`
- Suffix filter: `.pdf`
- Destination: Lambda → `intelligent-docs-ingest`

Grant S3 permission to invoke the Lambda (S3 will prompt for this in the console).

### 7. API Gateway

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

Deploy the API to a stage named `demo`.

### 8. Cognito User Pool

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

### 9. Create Users

In Cognito → User Pool → Users, create:
- An admin user → add to `admins` group
- An employee user → add to `employees` group

### 10. Frontend Configuration

Edit `frontend/index.html` and update the config block at the top of the `<script>` section:

```javascript
const API_BASE = "https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com/demo";
const COGNITO = {
  domain:   "https://YOUR_DOMAIN.auth.YOUR_REGION.amazoncognito.com",
  clientId: "YOUR_APP_CLIENT_ID",
  redirect: window.location.origin + window.location.pathname
};
```

### 11. Run Locally

Serve `frontend/index.html` on the port you registered in Cognito (e.g. port 8081):

```bash
# Python
python3 -m http.server 8081 --directory frontend/

# Node (npx)
npx serve frontend/ -p 8081
```

Open `http://localhost:8081` in your browser.

---

## Sample Documents

Ready-to-use sample policy documents are in `sample-docs/`:
- `Employee_Handbook.pdf`
- `Leave_Policy.pdf`
- `WFH_Policy.pdf`
- `IT_Security_Policy.pdf`
- `Code_of_Conduct.pdf`
- `New_Joiner_Onboarding.pdf`

## Usage

1. Sign in with an admin account → upload policy PDFs via the Upload tab
2. Wait for "✓ Indexed" status (indexing takes ~30s per document)
3. Click the Documents tab to verify indexed documents
4. Ask questions in the chat — answers are generated from the uploaded documents
5. Sign in with an employee account → upload tab is hidden, queries work normally

## Troubleshooting

**`AccessDeniedException` when invoking Bedrock models**
The Lambda role is missing AWS Marketplace permissions required for Bedrock's auto-subscription. Ensure the inline policy includes `aws-marketplace:Subscribe`, `aws-marketplace:Unsubscribe`, and `aws-marketplace:ViewSubscriptions`. It can take up to 2 minutes after adding permissions for the subscription to complete.

**PDF uploaded but not indexed / no chunks appear**
Check CloudWatch Logs for the `intelligent-docs-ingest` function (Log groups → `/aws/lambda/intelligent-docs-ingest`). Common causes: wrong `DOCS_BUCKET` env var, missing S3 event trigger, or the pypdf layer not attached.

**Search returns "No documents have been indexed yet"**
Either no PDFs have been ingested, or the `VECTOR_BUCKET` / `VECTOR_INDEX` env vars on the search Lambda don't match what was created. Verify both Lambdas use the same values.

**S3 CORS error when uploading from the browser**
The browser upload goes directly to S3 via a presigned URL. If you see a CORS error, check that the CORS configuration is saved on the `intelligent-docs-app` bucket and that `AllowedMethods` includes `PUT`.

**API returns 401 Unauthorized**
The Cognito authorizer is not attached, or the API was not redeployed after attaching it. In API Gateway → your API → Authorizers, confirm the authorizer is linked to each method, then redeploy to the `demo` stage.

**Login redirects to wrong URL / blank page after login**
The callback URL in the Cognito App Client must exactly match the URL you're serving the frontend on (including port). Update it under Cognito → App Client → Hosted UI settings.

**Lambda times out during ingestion**
Large PDFs with many pages can exceed the default timeout. Ensure `intelligent-docs-ingest` timeout is set to 300s and memory to 512 MB.
