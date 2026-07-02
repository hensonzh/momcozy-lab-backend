from __future__ import annotations

import asyncio

import boto3
from botocore.config import Config

from .base import StoredObject
from .local import LocalObjectStorage


class S3ObjectStorage:
    def __init__(
        self,
        *,
        bucket: str,
        region: str = "",
        endpoint_url: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
    ) -> None:
        self.bucket = bucket
        self.region = region or None
        self.endpoint_url = endpoint_url or None
        self.client = boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
            config=Config(signature_version="s3v4"),
        )

    async def put_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        normalized_key = LocalObjectStorage._normalize_key(key)
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=normalized_key,
            Body=body,
            ContentType=content_type,
        )
        return StoredObject(
            key=normalized_key,
            uri=f"s3://{self.bucket}/{normalized_key}",
            size_bytes=len(body),
            content_type=content_type,
        )

    async def get_bytes(self, *, key: str) -> bytes:
        normalized_key = LocalObjectStorage._normalize_key(key)
        response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=normalized_key)
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    async def delete(self, *, key: str) -> None:
        normalized_key = LocalObjectStorage._normalize_key(key)
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=normalized_key)
