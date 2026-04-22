import json
import boto3
import os

s3vectors = boto3.client("s3vectors", region_name="ap-south-1")

VECTOR_BUCKET = "intelligent-docs-vectors"
INDEX_NAME    = "hr-policy-index"


def lambda_handler(event, context):
    try:
        # List all vectors to get distinct docs + chunk counts
        docs = {}
        next_token = None
        while True:
            kwargs = dict(vectorBucketName=VECTOR_BUCKET, indexName=INDEX_NAME,
                          maxResults=1000, returnMetadata=True)
            if next_token:
                kwargs["nextToken"] = next_token
            resp = s3vectors.list_vectors(**kwargs)
            for v in resp.get("vectors", []):
                meta = v.get("metadata", {})
                doc_id = meta.get("doc_id", "")
                if not doc_id:
                    continue
                if doc_id not in docs:
                    docs[doc_id] = {
                        "name": meta.get("doc_name", doc_id),
                        "chunks": 0,
                        "pages": set()
                    }
                docs[doc_id]["chunks"] += 1
                docs[doc_id]["pages"].add(meta.get("page", "0"))
            next_token = resp.get("nextToken")
            if not next_token:
                break

        result = [
            {"name": d["name"], "chunks": d["chunks"], "pages": len(d["pages"])}
            for d in docs.values()
        ]
        return resp_ok({"docs": result})
    except Exception as e:
        print(f"Error: {e}")
        return resp_ok({"docs": []})


def resp_ok(body):
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET, OPTIONS"
        },
        "body": json.dumps(body)
    }
