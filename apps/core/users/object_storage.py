import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _client():
    if not settings.OBJECT_STORAGE_ENDPOINT_URL:
        raise ImproperlyConfigured("OBJECT_STORAGE_ENDPOINT_URL must be configured.")
    if not settings.OBJECT_STORAGE_ACCESS_KEY or not settings.OBJECT_STORAGE_SECRET_KEY:
        raise ImproperlyConfigured("Object-storage credentials must be configured.")
    return boto3.client(
        "s3",
        endpoint_url=settings.OBJECT_STORAGE_ENDPOINT_URL,
        aws_access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.OBJECT_STORAGE_SECRET_KEY,
        region_name=settings.OBJECT_STORAGE_REGION,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _ensure_bucket(client):
    if not settings.OBJECT_STORAGE_AUTO_CREATE_BUCKET:
        return
    try:
        client.head_bucket(Bucket=settings.OBJECT_STORAGE_BUCKET)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        try:
            client.create_bucket(Bucket=settings.OBJECT_STORAGE_BUCKET)
        except ClientError as create_error:
            if create_error.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                raise


def upload_private_object(key, body, content_type):
    client = _client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def read_private_object(key):
    response = _client().get_object(Bucket=settings.OBJECT_STORAGE_BUCKET, Key=key)
    return response["Body"].read()


def delete_private_object(key):
    _client().delete_object(Bucket=settings.OBJECT_STORAGE_BUCKET, Key=key)
