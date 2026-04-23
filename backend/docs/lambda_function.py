import json
import boto3
import os

REGION        = os.environ.get("AWS_REGION", "us-east-1")
VECTOR_BUCKET = os.environ["VECTOR_BUCKET"]
INDEX_NAME    = os.environ["VECTOR_INDEX"]

s3vectors = boto3.client("s3vectors", region_name=REGION)


def lambda_handler(event, context):
    try:
        docs = {}
        next_token = None
        while True:
            kwargs = dict(vectorBucketName=VECTOR_BUCKET, indexName=INDEX_NAME,
                          maxResults=1000, returnMetadata=True)
            if next_token:
                kwargs["nextToken"] = next_token
            resp = s3vectors.list_vectors(**kwargs)
            for v in resp.get("vectors", []):
                key = v.get("key", "")
                meta = v.get("metadata", {})
                # Fallback: parse doc_id from key "docs/filename.pdf::chunk_N"
                doc_id = meta.get("doc_id") or (key.split("::")[0] if "::" in key else key)
                doc_name = meta.get("doc_name") or doc_id.split("/")[-1]
                if not doc_id:
                    continue
                if doc_id not in docs:
                    docs[doc_id] = {"name": doc_name, "chunks": 0, "pages": set()}
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
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET, OPTIONS"
        },
        "body": json.dumps(body)
    }
