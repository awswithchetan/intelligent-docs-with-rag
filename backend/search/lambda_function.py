"""
Search Lambda — Intelligent Docs RAG
Handles natural language questions against indexed HR policy documents.

Flow:
  User question → embed → cosine search → top K chunks → Claude generates answer with citations
"""

import json
import boto3
import math
import os
from datetime import datetime, timezone

s3      = boto3.client("s3", region_name="ap-south-1", endpoint_url="https://s3.ap-south-1.amazonaws.com")
bedrock = boto3.client("bedrock-runtime", region_name="ap-south-1")

BUCKET       = os.environ["DOCS_BUCKET"]
INDEX_KEY    = "index/index.json"
CLAUDE_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
TITAN_MODEL  = "amazon.titan-embed-text-v2:0"
TOP_K        = 8     # top chunks to retrieve
MIN_SCORE    = 0.20  # minimum similarity threshold

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

    # 1. Load index
    index = load_index()
    if not index:
        return resp(200, {"answer": "No documents have been indexed yet. Please ask your admin to upload policy documents.", "sources": []})

    # 2. Embed question
    query_vec = embed_text(question)

    # 3. Cosine similarity + keyword boost
    query_words = set(question.lower().split())
    scored = []
    for chunk in index:
        score = cosine_similarity(query_vec, chunk["embedding"])
        # keyword boost
        chunk_text = chunk["text"].lower()
        matches = sum(1 for w in query_words if len(w) > 3 and w in chunk_text)
        scored.append((score + matches * 0.1, score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 4. Take top K above threshold
    top_chunks = [
        (final_score, base_score, chunk)
        for final_score, base_score, chunk in scored[:TOP_K]
        if base_score >= MIN_SCORE
    ]

    if not top_chunks:
        return resp(200, {
            "answer": "I couldn't find relevant information in the policy documents for your question.",
            "sources": [],
            "total_chunks_searched": len(index)
        })

    # 5. Build context for Claude
    context_parts = []
    sources = []
    seen_chunks = set()
    for _, _, chunk in top_chunks:
        if chunk["chunk_id"] in seen_chunks:
            continue
        seen_chunks.add(chunk["chunk_id"])
        context_parts.append(
            f"[Source: {chunk['doc_name']}, Page {chunk['page']}]\n{chunk['text']}"
        )
        sources.append({
            "doc_name": chunk["doc_name"],
            "doc_id"  : chunk["doc_id"],
            "page"    : chunk["page"],
        })

    context_text = "\n\n---\n\n".join(context_parts)

    # 6. Generate answer with Claude
    answer = generate_answer(question, context_text)

    # deduplicate sources
    seen = set()
    unique_sources = []
    for s in sources:
        key = f"{s['doc_name']}:{s['page']}"
        if key not in seen:
            seen.add(key)
            unique_sources.append(s)

    print(f"Answer generated. Sources: {[s['doc_name'] for s in unique_sources]}")
    return resp(200, {
        "answer" : answer,
        "sources": unique_sources,
        "total_chunks_searched": len(index)
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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


def load_index() -> list:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=INDEX_KEY)
        return json.loads(obj["Body"].read())
    except Exception as e:
        print(f"Index load error: {e}")
        return []


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
