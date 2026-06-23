"""
Helpers S3 / MinIO pour les snapshots Hub.
Upload multipart (>100 Mo), download, delete.
"""
import asyncio
import logging
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100 Mo
MULTIPART_CHUNK = 16 * 1024 * 1024       # 16 Mo par part


def build_s3_client(endpoint, access_key, secret_key, region="eu-west-1"):
    if not endpoint or not access_key or not secret_key:
        return None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=BotoConfig(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3},
        ),
    )


async def upload_to_s3(file_path, bucket, key, endpoint, access_key, secret_key, region="eu-west-1"):
    client = build_s3_client(endpoint, access_key, secret_key, region)
    if not client:
        raise RuntimeError("Configuration S3 incomplete")

    file_size = file_path.stat().st_size
    logger.info(f"Upload S3: {file_path} -> s3://{bucket}/{key} ({file_size} bytes)")

    if file_size > MULTIPART_THRESHOLD:
        from boto3.s3.transfer import TransferConfig

        config = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD, multipart_chunksize=MULTIPART_CHUNK)

        def _upload():
            client.upload_file(str(file_path), bucket, key, Config=config)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _upload)
    else:
        with open(file_path, "rb") as f:
            client.put_object(Bucket=bucket, Key=key, Body=f.read())

    logger.info(f"Upload termine: s3://{bucket}/{key}")


async def download_from_s3(bucket, key, dest_path, endpoint, access_key, secret_key, region="eu-west-1"):
    client = build_s3_client(endpoint, access_key, secret_key, region)
    if not client:
        raise RuntimeError("Configuration S3 incomplete")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, key, str(dest_path))
    logger.info(f"Telechargement termine: s3://{bucket}/{key} -> {dest_path}")


def delete_from_s3(bucket, key, endpoint, access_key, secret_key, region="eu-west-1"):
    client = build_s3_client(endpoint, access_key, secret_key, region)
    if not client:
        return False
    try:
        client.delete_object(Bucket=bucket, Key=key)
        logger.info(f"Supprime du S3: s3://{bucket}/{key}")
        return True
    except ClientError as e:
        logger.error(f"Echec suppression S3 {bucket}/{key}: {e}")
        return False
