from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from basketiq.config import Settings
from basketiq.storage.base import ObjectNotFoundError, PresignedUpload, StorageUnavailableError


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3})
        common: dict[str, Any] = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "region_name": settings.s3_region,
            "config": cfg,
        }
        self.bucket = settings.s3_bucket
        self._client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **common)
        # Presigned URLs embed the host in the signature, so they are signed for the public host.
        self._signer = boto3.client(
            "s3", endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url, **common
        )

    def ensure_bucket(self, allowed_origins: list[str]) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket)
        self._client.put_bucket_cors(
            Bucket=self.bucket,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": allowed_origins,
                        "AllowedMethods": ["POST", "GET"],
                        "AllowedHeaders": ["*"],
                        "MaxAgeSeconds": 600,
                    }
                ]
            },
        )

    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, ttl_seconds: int
    ) -> PresignedUpload:
        try:
            post = self._signer.generate_presigned_post(
                self.bucket,
                key,
                Fields={"Content-Type": content_type},
                Conditions=[["content-length-range", 1, max_bytes], {"Content-Type": content_type}],
                ExpiresIn=ttl_seconds,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return PresignedUpload(
            url=post["url"],
            fields=post["fields"],
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )

    def presign_download(self, key: str, ttl_seconds: int, content_type: str | None = None) -> str:
        params: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ResponseContentType"] = content_type
        params["ResponseContentDisposition"] = "inline"
        return str(self._signer.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl_seconds))

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageUnavailableError(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError(str(exc)) from exc

    def size(self, key: str) -> int:
        try:
            return int(self._client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as exc:
            raise ObjectNotFoundError(key) from exc

    def get(self, key: str, max_bytes: int) -> bytes:
        try:
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
            body = obj["Body"].read(max_bytes + 1)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                raise ObjectNotFoundError(key) from exc
            raise StorageUnavailableError(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return bytes(body)

    def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys: list[Any] = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                    deleted += len(keys)
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return deleted
