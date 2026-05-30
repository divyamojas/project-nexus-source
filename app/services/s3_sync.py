from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def _get_s3_client():
    import boto3

    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def _upload(bucket: str, key: str, data: Any) -> None:
    s3 = _get_s3_client()
    body = json.dumps(data, default=str).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    logger.info("S3 sync: uploaded s3://%s/%s", bucket, key)


async def sync_file(bucket: str, key: str, data: Any) -> None:
    if not settings.s3_enabled:
        return
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _upload, bucket, key, data)
    except Exception as exc:
        logger.warning("S3 sync_file failed: %s", exc)


async def sync_full() -> None:
    if not settings.s3_enabled:
        logger.debug("S3 sync disabled; skipping full sync")
        return
    logger.info("S3 full sync triggered (no-op placeholder)")
