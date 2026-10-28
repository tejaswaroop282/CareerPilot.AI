"""boto3 S3 access. Same code for MinIO/RustFS locally (S3_ENDPOINT_URL set) and AWS (IAM role, endpoint empty).

boto3 is blocking, so async callers use the *_async wrappers (thread pool).
"""
import asyncio
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings


@lru_cache
def get_s3():
    s = get_settings()
    kwargs: dict = {"region_name": s.aws_region}
    if s.s3_endpoint_url:
        kwargs.update(
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.minio_root_user,
            aws_secret_access_key=s.minio_root_password,
        )
    return boto3.client("s3", **kwargs)


def ensure_bucket() -> None:
    """Make sure the bucket exists. On AWS the bucket is normally created once (see docs/DEPLOYMENT.md) and the instance
    role may not be allowed to create buckets, so a failed create gives a clear message instead of a stack trace."""
    s = get_settings()
    s3, bucket = get_s3(), s.s3_bucket
    try:
        s3.head_bucket(Bucket=bucket)
        return
    except ClientError:
        pass
    kwargs: dict = {"Bucket": bucket}
    if not s.s3_endpoint_url and s.aws_region != "us-east-1":  # real S3 outside us-east-1 requires the location constraint
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": s.aws_region}
    try:
        s3.create_bucket(**kwargs)
    except ClientError as e:
        raise RuntimeError(
            f"S3 bucket '{bucket}' does not exist and could not be created ({e.response['Error']['Code']}). "
            "Create it once in the AWS console (same region as AWS_REGION) and grant the instance role access."
        ) from e


def put_object(key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
    get_s3().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=body, ContentType=content_type)


def get_object(key: str) -> bytes:
    return get_s3().get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"].read()


def copy_object(src_key: str, dst_key: str) -> None:
    bucket = get_settings().s3_bucket
    get_s3().copy_object(Bucket=bucket, Key=dst_key, CopySource={"Bucket": bucket, "Key": src_key})


def delete_object(key: str) -> None:
    get_s3().delete_object(Bucket=get_settings().s3_bucket, Key=key)


def delete_prefix(prefix: str) -> int:
    s3, bucket = get_s3(), get_settings().s3_bucket
    deleted = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


def list_keys(prefix: str) -> list[str]:
    s3, bucket = get_s3(), get_settings().s3_bucket
    return [
        o["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
        for o in page.get("Contents", [])
    ]


def presigned_url(key: str, expires: int = 600) -> str:
    return get_s3().generate_presigned_url(
        "get_object", Params={"Bucket": get_settings().s3_bucket, "Key": key}, ExpiresIn=expires
    )


async def put_object_async(key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
    await asyncio.to_thread(put_object, key, body, content_type)


async def get_object_async(key: str) -> bytes:
    return await asyncio.to_thread(get_object, key)


async def copy_object_async(src_key: str, dst_key: str) -> None:
    await asyncio.to_thread(copy_object, src_key, dst_key)


async def delete_object_async(key: str) -> None:
    await asyncio.to_thread(delete_object, key)


async def delete_prefix_async(prefix: str) -> int:
    return await asyncio.to_thread(delete_prefix, prefix)
