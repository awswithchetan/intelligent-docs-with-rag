# Intelligent Docs — HR Policy Assistant

A serverless RAG (Retrieval-Augmented Generation) application that lets employees ask natural language questions about company HR policy documents. Built on AWS using Amazon Bedrock, S3 Vectors, and Cognito.

---

## Architecture

<img width="980" height="540" alt="image" src="https://github.com/user-attachments/assets/48c91012-97b8-4c41-bb0b-99fcd2a71c21" />

## Tech Stack

| Component | Service | Estimated Cost |
|-----------|---------|----------------|
| Vector storage | Amazon S3 Vectors | ~$0.01/month (few MBs for sample docs) |
| Embeddings | Amazon Titan Embed Text v2 (512d) | ~$0.00 (fractions of a cent for sample docs) |
| Answer generation | Anthropic Claude 3.5 Haiku (via Bedrock) | ~$0.01–$0.05 per 100 questions |
| PDF processing | pypdf | Free (runs inside Lambda) |
| Auth | Amazon Cognito (Hosted UI, implicit flow) | Free (up to 50,000 MAUs) |
| API | Amazon API Gateway (REST) + Lambda (Python 3.14) | Free tier covers typical demo usage |
| Document storage | Amazon S3 | ~$0.00 (a few PDFs = negligible) |

> **Total estimated cost for demo usage: < $1/month.** All services either fall within the AWS Free Tier or cost fractions of a cent at the scale of a few documents and occasional queries.

## Sample Documents

Ready-to-use sample HR policy documents are in `sample-docs/`:
- `Employee_Handbook.pdf`
- `Leave_Policy.pdf`
- `WFH_Policy.pdf`
- `IT_Security_Policy.pdf`
- `Code_of_Conduct.pdf`
- `New_Joiner_Onboarding.pdf`

---

## Prerequisites

- AWS account
- AWS CLI configured locally

  **Mac:**
  ```bash
  brew install awscli
  aws configure
  ```
  **Ubuntu:**
  ```bash
  curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
  unzip awscliv2.zip && sudo ./aws/install
  aws configure
  ```
- Python 3 and `pip` installed locally (for building the Lambda layer)

  **Mac:**
  ```bash
  brew install python zip
  ```
  **Ubuntu:**
  ```bash
  sudo apt update && sudo apt install -y python3 python3-pip zip
  ```
- Bedrock model access: as of late 2025, all serverless models are **automatically enabled** — no manual activation needed

> All steps below use `ap-south-1` (Mumbai) as an example. You can use any AWS region that supports Bedrock, S3 Vectors, and Cognito — just replace `ap-south-1` consistently throughout.

---

## Deployment Steps

### Step 0 — Clone the Repository

```bash
git clone https://github.com/awswithchetan/intelligent-docs-with-rag.git
cd intelligent-docs-with-rag
```

---

### Step 1 — S3 Bucket (document storage)

1. Go to **S3 Console** → **Create bucket**
2. **Bucket name:** `intelligent-docs-app` (or your preferred name)
3. **Region:** your chosen region
4. Leave all other settings as default → **Create bucket**

**Add CORS configuration** to allow browser uploads via presigned URLs (the bucket itself remains private):

1. Open the bucket → **Permissions** tab → **Cross-origin resource sharing (CORS)** → **Edit**
2. Paste the following and save:

```json
[{
  "AllowedHeaders": ["*"],
  "AllowedMethods": ["GET", "PUT", "POST"],
  "AllowedOrigins": ["*"],
  "ExposeHeaders": []
}]
```

---

### Step 2 — S3 Vector Bucket & Index

1. Go to **S3 Console** → **Vector buckets** (left sidebar) → **Create vector bucket**
2. **Name:** `intelligent-docs-vectors`
3. **Encryption:** SSE-S3
4. Click **Create vector bucket**

**Create a vector index inside the bucket:**

1. Click on `intelligent-docs-vectors` → **Create index**
2. Configure:
   - **Name:** `intelligent-docs-hr-policy-index`
   - **Dimension:** `512` (must match the embedding model — Titan Embed v2 produces 512-dimensional vectors)
   - **Distance metric:** `Cosine` (measures similarity by angle between vectors, best for text embeddings)
   - **Non-filterable metadata key:** `text` (stores the original chunk text alongside the vector for retrieval)
3. Click **Create index**

---

### Step 3 — IAM Role for Lambda

1. Go to **IAM Console** → **Roles** → **Create role**
2. **Trusted entity:** AWS service → **Lambda**
3. **Role name:** `intelligent-docs-lambda-role`
4. Click **Create role**

**Attach managed policy:**

1. Open the role → **Permissions** tab → **Add permissions** → **Attach policies**
2. Search and attach: `AWSLambdaBasicExecutionRole`

**Add inline policy:**

1. **Add permissions** → **Create inline policy** → **JSON** tab
2. Paste the following (replace placeholders with your actual values):

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

3. **Policy name:** `intelligent-docs-lambda-policy` → **Create policy**

---

### Step 4 — Lambda Layer (pypdf dependency)

The ingest Lambda requires `pypdf`. Package it as a Lambda Layer so all function code can be pasted inline — no zip uploads needed.

**Build the layer package** on your local machine (or WSL terminal):

```bash
mkdir -p python
pip install pypdf==4.0.1 -t python/
zip -r pypdf-layer.zip python/
```

**Create the layer via AWS CLI** (recommended — works directly from WSL without copying files):

```bash
aws lambda publish-layer-version \
  --layer-name intelligent-docs-pypdf-layer \
  --zip-file fileb://pypdf-layer.zip \
  --compatible-runtimes python3.14 \
  --region YOUR_REGION
```

Note the `LayerVersionArn` from the output — you'll need it when attaching the layer to the ingest Lambda.

**Alternatively, via the Lambda console** → **Layers** → **Create layer**:
- Name: `intelligent-docs-pypdf-layer`
- Upload `pypdf-layer.zip`
- Compatible runtime: Python 3.14

---

### Step 5 — Lambda Functions

Create four Lambda functions with the following names:
- `intelligent-docs-ingest`
- `intelligent-docs-search`
- `intelligent-docs-list`
- `intelligent-docs-upload`

**Common steps for each function:**

1. Go to **Lambda Console** → **Create function** → **Author from scratch**
2. **Runtime:** Python 3.14
3. **Execution role:** Use existing role → `intelligent-docs-lambda-role`
4. Click **Create function**
5. After creating, paste the code in the inline editor and click **Deploy**

---

#### 5.1 intelligent-docs-ingest

**Configuration:**
- **Handler:** `lambda_function.lambda_handler`
- **Timeout:** 300 seconds
- **Memory:** 512 MB
- **Code:** paste contents of `backend/ingest/lambda_function_s3vectors.py` as `lambda_function.py`

**Attach the pypdf layer:**

1. Scroll to **Layers** section → **Add a layer**
2. **Layer source:** Custom layers
3. **Layer:** `intelligent-docs-pypdf-layer` → version `1`
4. Click **Add**

**Environment variables** (Configuration → Environment variables → Edit):

| Key | Value | Description |
|-----|-------|-------------|
| `DOCS_BUCKET` | `intelligent-docs-app` | S3 bucket where PDFs are stored |
| `VECTOR_BUCKET` | `intelligent-docs-vectors` | S3 vector bucket for storing embeddings |
| `VECTOR_INDEX` | `intelligent-docs-hr-policy-index` | Vector index to write chunks into |
| `EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | Bedrock model used to generate embeddings |
| `CHUNK_SIZE` | `200` | Number of words per text chunk |
| `CHUNK_OVERLAP` | `20` | Number of overlapping words between consecutive chunks |

---

#### 5.2 intelligent-docs-search

**Configuration:**
- **Handler:** `lambda_function.lambda_handler`
- **Timeout:** 60 seconds
- **Memory:** 512 MB
- **Code:** paste contents of `backend/search/lambda_function_s3vectors.py` as `lambda_function.py`

**Environment variables:**

| Key | Value | Description |
|-----|-------|-------------|
| `VECTOR_BUCKET` | `intelligent-docs-vectors` | S3 vector bucket to query |
| `VECTOR_INDEX` | `intelligent-docs-hr-policy-index` | Vector index to search against |
| `EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | Bedrock model used to embed the user's question |
| `LLM_MODEL` | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | Bedrock inference profile used to generate the answer |
| `TOP_K` | `15` | Number of most similar chunks to retrieve from the index |

> **Note on LLM_MODEL:** Use the cross-region inference profile ID (prefixed with `us.`) — direct model IDs are not supported for on-demand throughput for newer Claude models.

---

#### 5.3 intelligent-docs-list

**Configuration:**
- **Handler:** `lambda_function.lambda_handler`
- **Timeout:** 30 seconds
- **Memory:** 256 MB
- **Code:** paste contents of `backend/docs/lambda_function.py` as `lambda_function.py`

**Environment variables:**

| Key | Value | Description |
|-----|-------|-------------|
| `DOCS_BUCKET` | `intelligent-docs-app` | S3 bucket to list documents from |
| `VECTOR_BUCKET` | `intelligent-docs-vectors` | S3 vector bucket to list vectors from |
| `VECTOR_INDEX` | `intelligent-docs-hr-policy-index` | Vector index to list documents from |

---

#### 5.4 intelligent-docs-upload

**Configuration:**
- **Handler:** `lambda_function.lambda_handler`
- **Timeout:** 15 seconds
- **Memory:** 256 MB
- **Code:** paste contents of `backend/upload_url/lambda_function.py` as `lambda_function.py`

**Environment variables:**

| Key | Value | Description |
|-----|-------|-------------|
| `DOCS_BUCKET` | `intelligent-docs-app` | S3 bucket to generate the presigned upload URL for |

---

### Step 6 — S3 Event Trigger

1. Go to **S3 Console** → open `intelligent-docs-app` bucket
2. **Properties** tab → **Event notifications** → **Create event notification**
3. Configure:
   - **Event name:** `intelligent-docs-ingest-trigger`
   - **Prefix:** `docs/`
   - **Suffix:** `.pdf`
   - **Event types:** Check `s3:ObjectCreated:*` and `s3:ObjectRemoved:*`
   - **Destination:** Lambda function → `intelligent-docs-ingest`
4. Click **Save changes** (AWS will automatically add the required resource-based policy to the Lambda)

---

### Step 7 — API Gateway

1. Go to **API Gateway Console** → **Create API** → **REST API** → **Build**
2. **API name:** `intelligent-docs-api`
3. Click **Create API**

**Create resources and methods:**

For each of the three routes below, create the resource and method:

| Resource | Method | Lambda function |
|----------|--------|-----------------|
| `/upload-url` | POST | `intelligent-docs-upload` |
| `/search` | POST | `intelligent-docs-search` |
| `/docs` | GET | `intelligent-docs-list` |

**For each method:**
1. Select the resource → **Create method**
2. **Method type:** POST (or GET for `/docs`)
3. **Integration type:** Lambda function
4. ✅ Check **Lambda proxy integration**
5. **Lambda function:** select the corresponding function
6. Click **Save**

**Enable CORS on each resource:**
1. Select the resource (e.g. `/upload-url`) → **Enable CORS**
2. Leave defaults → **Save**

This automatically creates the OPTIONS method with the correct headers.

**Deploy the API:**
1. **Deploy API** → **New stage** → **Stage name:** `demo`
2. Click **Deploy**
3. Note the **Invoke URL** — you'll need it for the frontend config

**Validate the API (without auth):**

Before adding Cognito, test that the Lambda integrations are working. At this point the methods have no authorizer, so you can call them directly:

```bash
# Should return {"docs": []} if no documents indexed yet
curl https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com/demo/docs

# Should return a presigned URL (upload Lambda working)
curl -X POST https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com/demo/upload-url \
  -H "Content-Type: application/json" \
  -d '{"filename": "test.pdf"}'
```

If these return valid JSON responses, the Lambda integrations are working correctly. Proceed to add Cognito auth in the next steps.

---

### Step 8 — Cognito User Pool

1. Go to **Cognito Console** → **User pools** → **Create user pool**

**Step through the wizard:**

2. **Application type:** Single-page application (SPA)
3. **App name:** `intelligent-docs-app`
4. **Sign-in options:** Email
5. **Return URL:** `http://localhost:8080` (or whatever port you'll serve the frontend on)
6. Click **Create**

This creates both the User Pool and App Client in one step.

**Note the following values** — you'll need them for the frontend config and API Gateway:
- **User Pool ID** (e.g. `us-east-1_xxxxxxxxx`) — shown on the User Pool overview page
- **App Client ID** — User Pool → **App clients** tab
- **Cognito Domain** — User Pool → **Branding** → **Domain** (e.g. `https://xxxxx.auth.us-east-1.amazoncognito.com`)

**Configure the App Client for Hosted UI:**

1. User Pool → **App clients** → click your app client
2. **Login pages** tab → **Edit**
3. Under **OAuth 2.0 grant types**, enable:
   - ✅ Authorization code grant
   - ✅ Implicit grant
4. Under **OpenID Connect scopes**, ensure these are selected:
   - ✅ `openid`
   - ✅ `email`
   - ✅ `profile`
5. **Allowed callback URLs:** `http://localhost:8080` (must exactly match the port you serve the frontend on)
6. Click **Save changes**

**Create user groups:**

1. User Pool → **Groups** tab → **Create group**
2. Create group: `admins`
3. Create group: `employees`

**Create users:**

1. User Pool → **Users** tab → **Create user**
2. Create an admin user (set a temporary password)
3. Create an employee user
4. Add the admin user to the `admins` group
5. Add the employee user to the `employees` group

---

### Step 9 — Add Cognito Authorizer to API Gateway

1. Go to **API Gateway Console** → `intelligent-docs-api` → **Authorizers** → **Create authorizer**
2. Configure:
   - **Name:** `intelligent-docs-cognito-auth`
   - **Authorizer type:** Cognito
   - **Cognito user pool:** `intelligent-docs-users`
   - **Token source:** `Authorization`
3. Click **Create authorizer**

**Attach the authorizer to each method:**

For each of the three methods (`POST /upload-url`, `POST /search`, `GET /docs`):
1. Click the method → **Method request** → **Edit**
2. **Authorization:** select `intelligent-docs-cognito-auth`
3. Click **Save**

**Redeploy the API:**
1. **Deploy API** → select stage `demo` → **Deploy**

---

### Step 10 — Frontend Configuration

Edit `frontend/index.html` and update the config block at the top of the `<script>` section:

```javascript
const API_BASE = "https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com/demo";
const COGNITO = {
  domain:   "https://YOUR_DOMAIN.auth.YOUR_REGION.amazoncognito.com",
  clientId: "YOUR_APP_CLIENT_ID",
  redirect: window.location.origin + window.location.pathname
};
```

Replace:
- `YOUR_API_ID` — from the API Gateway Invoke URL
- `YOUR_REGION` — your AWS region (e.g. `us-east-1`)
- `YOUR_DOMAIN` — your Cognito domain prefix
- `YOUR_APP_CLIENT_ID` — from Cognito App Client

---

### Step 11 — Run Locally

Serve `frontend/index.html` on the **same port** you registered as the Cognito callback URL:

```bash
# Python
python3 -m http.server 8080 --directory frontend/

# Node (npx)
npx serve frontend/ -p 8080
```

Open `http://localhost:8080` in your browser.

---

## Usage

1. Sign in with the **admin** account → go to the **Upload** tab → upload policy PDFs
2. Wait ~30 seconds per document for indexing (status changes to "Indexed")
3. Go to the **Documents** tab to verify indexed documents
4. Ask questions in the **Chat** tab — answers are generated from the uploaded documents
5. Sign in with the **employee** account — Upload tab is hidden, chat works normally

---

## Troubleshooting

**1. `AccessDeniedException` when invoking Bedrock models**

The Lambda role is missing AWS Marketplace permissions required for Bedrock's auto-subscription. Ensure the inline policy includes `aws-marketplace:Subscribe`, `aws-marketplace:Unsubscribe`, and `aws-marketplace:ViewSubscriptions`. It can take up to 2 minutes after adding permissions for the subscription to complete.

**2. `ValidationException: Invocation of model ID ... with on-demand throughput isn't supported`**

You're using a direct model ID for a newer Claude model. Use the cross-region inference profile ID instead — prefix the model ID with `us.` (e.g. `us.anthropic.claude-3-5-haiku-20241022-v1:0`).

**3. PDF uploaded but not indexed / no chunks appear**

Check CloudWatch Logs for the `intelligent-docs-ingest` function (Log groups → `/aws/lambda/intelligent-docs-ingest`). Common causes: wrong `DOCS_BUCKET` env var, missing S3 event trigger, or the pypdf layer not attached.

**4. Search returns "No documents have been indexed yet"**

Either no PDFs have been ingested, or the `VECTOR_BUCKET` / `VECTOR_INDEX` env vars on the search Lambda don't match what was created. Verify all Lambdas use the same values.

**5. CORS error from API Gateway**

Usually caused by the Lambda throwing an unhandled exception — API Gateway doesn't add CORS headers to error responses. Check CloudWatch Logs for the Lambda to find the real error.

**6. S3 CORS error when uploading from the browser**

The browser upload goes directly to S3 via a presigned URL. Check that the CORS configuration is saved on the docs bucket and that `AllowedMethods` includes `PUT`.

**7. API returns 401 Unauthorized**

The Cognito authorizer is not attached to the method, or the API was not redeployed after attaching it. Confirm the authorizer is linked to each method, then redeploy to the `demo` stage.

**8. Upload returns 403 / upload tab shows error**

The logged-in user is not in the `admins` Cognito group. The upload Lambda checks for the `admins` group in the JWT claims. Verify the user is added to the `admins` group in Cognito.

**9. Login page shows "Invalid request"**

The OAuth scope or callback URL doesn't match. Ensure the App Client has `profile` scope enabled and the callback URL exactly matches the port you're serving the frontend on (including `http://` and no trailing path).

**10. Login redirects to wrong URL / blank page after login**

The callback URL in the Cognito App Client must exactly match the URL you're serving the frontend on (including port). Update it under Cognito → App Client → Login pages.

**11. Lambda times out during ingestion**

Large PDFs can exceed the default timeout. Ensure `intelligent-docs-ingest` timeout is set to 300s and memory to 512 MB.

---

## Cleanup

To avoid ongoing charges, delete the following resources when done:

**S3:**
1. Empty and delete the `intelligent-docs-app` bucket (S3 Console → bucket → Empty → Delete)
2. Delete all vectors in `intelligent-docs-vectors` → delete the vector index → delete the vector bucket

**Lambda:**
1. Delete all four Lambda functions: `intelligent-docs-ingest`, `intelligent-docs-search`, `intelligent-docs-list`, `intelligent-docs-upload`
2. Delete the Lambda layer: `intelligent-docs-pypdf-layer`

**IAM:**
1. Delete the inline policy `intelligent-docs-lambda-policy` from the role
2. Delete the role `intelligent-docs-lambda-role`

**API Gateway:**
1. Delete the `intelligent-docs-api` REST API

**Cognito:**
1. Delete the User Pool (this also deletes the App Client and all users)

**CloudWatch:**
1. Delete the log groups for each Lambda under `/aws/lambda/intelligent-docs-*` (optional — log storage cost is negligible)
