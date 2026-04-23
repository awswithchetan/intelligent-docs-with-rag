"""
Ingestion Lambda — S3 Vectors version
Triggered by S3 ObjectCreated and ObjectRemoved events on the docs/ prefix.

Flow (upload):
  PDF uploaded → extract text → chunk → embed each chunk → put into S3 Vectors index

Flow (delete):
  PDF deleted → delete all vectors for that doc from S3 Vectors index
"""

import json
import boto3
import os
import re
from datetime import datetime, timezone
from io import BytesIO

REGION        = os.environ.get("AWS_REGION", "ap-south-1")

s3         = boto3.client("s3", region_name=REGION)
bedrock    = boto3.client("bedrock-runtime", region_name=REGION)
s3vectors  = boto3.client("s3vectors", region_name=REGION)

BUCKET          = os.environ["DOCS_BUCKET"]
VECTOR_BUCKET   = os.environ.get("VECTOR_BUCKET", "intelligent-docs-vectors")
INDEX_NAME      = os.environ.get("VECTOR_INDEX", "intelligent-docs-hr-policy-index")
TITAN_MODEL     = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")
CHUNK_SIZE      = int(os.environ.get("CHUNK_SIZE", "200"))
CHUNK_OVERLAP   = int(os.environ.get("CHUNK_OVERLAP", "20"))


def lambda_handler(event, context):
    record    = event["Records"][0]
    event_type = record["eventName"]
    bucket    = record["s3"]["bucket"]["name"]
    from urllib.parse import unquote_plus
    key       = unquote_plus(record["s3"]["object"]["key"])

    if not key.startswith("docs/") or not key.lower().endswith(".pdf"):
        print(f"Skipping: {key}")
        return {"status": "skipped"}

    doc_id = key  # use full S3 key as doc identifier

    if event_type.startswith("ObjectRemoved"):
        return handle_delete(bucket, doc_id)
    else:
        return handle_upload(bucket, key, doc_id)


def handle_upload(bucket, key, doc_id):
    print(f"Processing upload: {key}")

    # 1. Download PDF
    pdf_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    # 2. Extract text from PDF
    pages = extract_text_from_pdf(pdf_bytes)
    if not pages:
        print("No text extracted from PDF")
        return {"status": "error", "reason": "no text extracted"}

    doc_name = key.split("/")[-1]
    print(f"Extracted {len(pages)} pages from {doc_name}")

    # 3. Chunk text with page tracking
    chunks = chunk_pages(pages)
    print(f"Created {len(chunks)} chunks")

    # 4. Embed each chunk and prepare vectors
    vectors = []
    for i, chunk in enumerate(chunks):
        try:
            embedding = embed_text(chunk["text"])
        except Exception as e:
            print(f"  Skipping chunk {i+1}: {e}")
            continue
        
        vectors.append({
            "key": f"{doc_id}::chunk_{i}",
            "data": {"float32": embedding},
            "metadata": {
                "text": chunk["text"],  # non-filterable
                "doc_id": doc_id,
                "doc_name": doc_name,
                "page": str(chunk["page"]),
                "chunk_index": str(i),
                "uploaded_at": datetime.now(timezone.utc).isoformat()
            }
        })
        print(f"  Embedded chunk {i+1}/{len(chunks)}")

    # 5. Delete old vectors for this doc, then insert new ones
    delete_vectors_for_doc(doc_id)
    
    # Insert in batches of 100
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i+batch_size]
        s3vectors.put_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=INDEX_NAME,
            vectors=batch
        )
        print(f"  Inserted batch {i//batch_size + 1}/{(len(vectors)-1)//batch_size + 1}")

    print(f"Indexed {len(vectors)} chunks for {doc_name}")
    return {"status": "success", "doc_id": doc_id, "chunks": len(vectors)}


def handle_delete(bucket, doc_id):
    print(f"Processing delete: {doc_id}")
    delete_vectors_for_doc(doc_id)
    print(f"Deleted all chunks for {doc_id}")
    return {"status": "deleted", "doc_id": doc_id}


def delete_vectors_for_doc(doc_id):
    """Delete all vectors for a given doc_id by querying with metadata filter"""
    # Query to find all vectors for this doc
    try:
        response = s3vectors.query_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=INDEX_NAME,
            queryVector={"float32": [0.0] * 512},
            topK=2000,
            filter={"doc_id": {"eq": doc_id}}
        )
        
        keys_to_delete = [v["key"] for v in response.get("vectors", [])]
        
        if keys_to_delete:
            # Delete in batches of 100
            batch_size = 100
            for i in range(0, len(keys_to_delete), batch_size):
                batch = keys_to_delete[i:i+batch_size]
                s3vectors.delete_vectors(
                    vectorBucketName=VECTOR_BUCKET,
                    indexName=INDEX_NAME,
                    keys=batch
                )
            print(f"Deleted {len(keys_to_delete)} vectors for {doc_id}")
    except Exception as e:
        print(f"Error deleting vectors: {e}")


# ── PDF text extraction ───────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> list[dict]:
    """Returns list of {page: int, text: str}"""
    try:
        import pypdf
        reader = pypdf.PdfReader(BytesIO(pdf_bytes))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            text = re.sub(r'\s+', ' ', text).strip()
            if text:
                pages.append({"page": i + 1, "text": text})
        return pages
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return []


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_pages(pages: list[dict]) -> list[dict]:
    """Chunk pages into overlapping word windows, tracking source page."""
    chunks = []
    words_with_pages = []
    for p in pages:
        for word in p["text"].split():
            words_with_pages.append((word, p["page"]))

    total = len(words_with_pages)
    start = 0
    while start < total:
        end = min(start + CHUNK_SIZE, total)
        chunk_words = [w for w, _ in words_with_pages[start:end]]
        chunk_pages_set = [pg for _, pg in words_with_pages[start:end]]
        chunks.append({
            "text": " ".join(chunk_words),
            "page": chunk_pages_set[0],
        })
        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    body = json.dumps({"inputText": text[:8000], "dimensions": 512, "normalize": True})
    resp = bedrock.invoke_model(
        modelId=TITAN_MODEL, contentType="application/json",
        accept="application/json", body=body
    )
    return json.loads(resp["body"].read())["embedding"]
