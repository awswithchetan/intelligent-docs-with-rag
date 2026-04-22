"""
Ingestion Lambda — Intelligent Docs RAG
Triggered by S3 ObjectCreated and ObjectRemoved events on the docs/ prefix.

Flow (upload):
  PDF uploaded → extract text → chunk → embed each chunk → upsert into index.json

Flow (delete):
  PDF deleted → remove all chunks for that doc from index.json
"""

import json
import boto3
import os
import re
from datetime import datetime, timezone
from io import BytesIO

s3      = boto3.client("s3", region_name="ap-south-1", endpoint_url="https://s3.ap-south-1.amazonaws.com")
bedrock = boto3.client("bedrock-runtime", region_name="ap-south-1")

BUCKET       = os.environ["DOCS_BUCKET"]
INDEX_KEY    = "index/index.json"
TITAN_MODEL  = "amazon.titan-embed-text-v2:0"
CHUNK_SIZE   = 200   # words per chunk
CHUNK_OVERLAP = 20   # words overlap between chunks


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

    # 4. Embed each chunk
    records = []
    for i, chunk in enumerate(chunks):
        try:
            embedding = embed_text(chunk["text"])
        except Exception as e:
            print(f"  Skipping chunk {i+1}: {e}")
            continue
        records.append({
            "doc_id"    : doc_id,
            "doc_name"  : doc_name,
            "chunk_id"  : f"{doc_id}::chunk_{i}",
            "text"      : chunk["text"],
            "page"      : chunk["page"],
            "embedding" : embedding,
            "metadata"  : {
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
        })
        print(f"  Embedded chunk {i+1}/{len(chunks)}")

    # 5. Upsert into index (remove old chunks for this doc, add new ones)
    index = load_index(bucket)
    index = [r for r in index if r["doc_id"] != doc_id]  # remove old version
    index.extend(records)
    save_index(bucket, index)

    print(f"Index updated. Total chunks: {len(index)}, docs: {len(set(r['doc_id'] for r in index))}")
    return {"status": "success", "doc_id": doc_id, "chunks": len(records)}


def handle_delete(bucket, doc_id):
    print(f"Processing delete: {doc_id}")
    index = load_index(bucket)
    before = len(index)
    index = [r for r in index if r["doc_id"] != doc_id]
    save_index(bucket, index)
    print(f"Removed {before - len(index)} chunks for {doc_id}")
    return {"status": "deleted", "doc_id": doc_id}


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
    # flatten all words with page tracking
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
            "page": chunk_pages_set[0],  # starting page of chunk
        })
        start += CHUNK_SIZE - CHUNK_OVERLAP  # overlap

    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    body = json.dumps({"inputText": text[:8000], "dimensions": 512, "normalize": True})
    resp = bedrock.invoke_model(
        modelId=TITAN_MODEL, contentType="application/json",
        accept="application/json", body=body
    )
    return json.loads(resp["body"].read())["embedding"]


# ── Index helpers ─────────────────────────────────────────────────────────────

def load_index(bucket: str) -> list:
    try:
        obj = s3.get_object(Bucket=bucket, Key=INDEX_KEY)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return []
    except Exception as e:
        print(f"Index load error: {e}")
        return []


def save_index(bucket: str, index: list):
    s3.put_object(
        Bucket=bucket, Key=INDEX_KEY,
        Body=json.dumps(index),
        ContentType="application/json"
    )
