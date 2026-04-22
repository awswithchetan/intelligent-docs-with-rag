"""
Search Lambda — S3 Vectors version
Handles natural language questions against indexed HR policy documents.

Flow:
  User question → embed → S3 Vectors QueryVectors → top K chunks → Claude generates answer
"""

import json
import boto3
import os

bedrock   = boto3.client("bedrock-runtime", region_name="ap-south-1")
s3vectors = boto3.client("s3vectors", region_name="ap-south-1")

VECTOR_BUCKET = "intelligent-docs-vectors"
INDEX_NAME    = "hr-policy-index"
CLAUDE_MODEL  = "anthropic.claude-3-haiku-20240307-v1:0"
TITAN_MODEL   = "amazon.titan-embed-text-v2:0"
TOP_K         = 8

SYSTEM_PROMPT = """You are an HR policy assistant. Answer the employee's question 
using ONLY the provided policy document excerpts. 

Rules:
- Be clear, concise and helpful
- If the answer is not in the provided context, say "I couldn't find this in the policy documents."
- Always cite which document and page your answer comes from
- Use bullet points for lists
- Do not make up information"""


def lambda_handler(event, context):
    body     = json.loads(event.get("body", "{}"))
    question = body.get("question", "").strip()

    if not question:
        return resp(400, {"error": "question is required"})

    print(f"Question: {question}")

    # 1. Embed question
    query_vec = embed_text(question)

    # 2. Query S3 Vectors — returns top K semantically similar chunks
    try:
        response = s3vectors.query_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=INDEX_NAME,
            queryVector={"float32": query_vec},
            topK=TOP_K,
            returnMetadata=True
        )
        results = response.get("vectors", [])
    except Exception as e:
        print(f"S3 Vectors query error: {e}")
        return resp(200, {"answer": "No documents have been indexed yet. Please ask your admin to upload policy documents.", "sources": []})

    if not results:
        return resp(200, {
            "answer": "No documents have been indexed yet. Please ask your admin to upload policy documents.",
            "sources": []
        })

    # 3. Build context for Claude
    context_parts = []
    sources = []
    seen = set()
    for v in results:
        meta = v.get("metadata", {})
        text = meta.get("text", "")
        doc_name = meta.get("doc_name", "unknown")
        page = meta.get("page", "?")
        
        context_parts.append(f"[Source: {doc_name}, Page {page}]\n{text}")
        
        key = f"{doc_name}:{page}"
        if key not in seen:
            seen.add(key)
            sources.append({"doc_name": doc_name, "doc_id": meta.get("doc_id", ""), "page": page})

    context_text = "\n\n---\n\n".join(context_parts)

    # 4. Generate answer with Claude
    answer = generate_answer(question, context_text)

    print(f"Answer generated. Sources: {[s['doc_name'] for s in sources]}")
    return resp(200, {
        "answer": answer,
        "sources": sources,
        "total_chunks_searched": len(results)
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_answer(question: str, context: str) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Policy document excerpts:\n\n{context}\n\n---\n\nEmployee question: {question}"
        }]
    }
    resp_body = bedrock.invoke_model(
        modelId=CLAUDE_MODEL, contentType="application/json",
        accept="application/json", body=json.dumps(body)
    )
    return json.loads(resp_body["body"].read())["content"][0]["text"].strip()


def embed_text(text: str) -> list[float]:
    body = json.dumps({"inputText": text[:8000], "dimensions": 512, "normalize": True})
    response = bedrock.invoke_model(
        modelId=TITAN_MODEL, contentType="application/json",
        accept="application/json", body=body
    )
    return json.loads(response["body"].read())["embedding"]


def resp(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type"                : "application/json",
            "Access-Control-Allow-Origin" : "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS"
        },
        "body": json.dumps(body)
    }
