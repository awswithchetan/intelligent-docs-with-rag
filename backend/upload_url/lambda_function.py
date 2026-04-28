import boto3, json, os, uuid, base64

REGION = os.environ.get("AWS_REGION", "us-east-1")
BUCKET = os.environ["DOCS_BUCKET"]

s3 = boto3.client("s3", region_name=REGION)


def decode_jwt_claims(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}


def lambda_handler(event, context):
    # Try claims from API Gateway authorizer first, then decode token directly
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    groups = claims.get("cognito:groups", "")

    # Fallback: decode the token directly (handles access_token or id_token)
    if not groups:
        token = (event.get("headers") or {}).get("Authorization", "")
        token_claims = decode_jwt_claims(token)
        groups = token_claims.get("cognito:groups", [])
        if isinstance(groups, list):
            groups = " ".join(groups)

    if "admins" not in groups:
        return resp(403, {"error": "Admin access required to upload documents"})

    body     = json.loads(event.get("body") or "{}")
    filename = body.get("filename", f"{uuid.uuid4()}.pdf")
    key      = f"docs/{filename}"
    url      = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=300
    )
    return resp(200, {"upload_url": url, "key": key})


def resp(code, body):
    return {
        "statusCode": code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS"
        },
        "body": json.dumps(body)
    }
