import boto3, json, os, uuid

REGION = os.environ.get("AWS_REGION", "us-east-1")
BUCKET = os.environ["DOCS_BUCKET"]

s3 = boto3.client("s3", region_name=REGION)


def lambda_handler(event, context):
    # Check admin group from Cognito JWT claims (passed by API Gateway)
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    groups = claims.get("cognito:groups", "")
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
