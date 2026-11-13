"""Cloudflare R2 对象存储(S3 兼容 API,boto3):备份、公共文件、自定义域名访问。"""
from __future__ import annotations

import json
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError


class R2Store:
    def __init__(self, cfg, db, events):
        self.cfg = cfg
        self.db = db
        self.events = events
        self._client = None

    # ------------------------------------------------------------------
    def conf(self) -> dict:
        return self.cfg.get("r2", {}) or {}

    def configured(self) -> bool:
        c = self.conf()
        return bool(c.get("account_id") and c.get("access_key")
                    and c.get("secret_key") and c.get("bucket"))

    def client(self):
        if self._client is not None:
            return self._client
        c = self.conf()
        endpoint = f"https://{c['account_id']}.r2.cloudflarestorage.com"
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=c["access_key"],
            aws_secret_access_key=c["secret_key"],
            region_name="auto",
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 2}),
        )
        return self._client

    def reset_client(self) -> None:
        self._client = None

    # ------------------------------------------------------------------
    def test(self) -> dict:
        """连通性测试:列举桶内对象数量。"""
        if not self.configured():
            return {"ok": False, "error": "R2 配置不完整"}
        try:
            resp = self.client().list_objects_v2(
                Bucket=self.conf()["bucket"], MaxKeys=1)
            return {"ok": True, "objects": resp.get("KeyCount", 0)}
        except ClientError as e:
            return {"ok": False, "error": e.response.get("Error", {}).get("Message", str(e))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def public_url(self, key: str) -> str:
        """公共访问地址:优先自定义域名,其次 r2.dev 公开地址;都没有则返回签名地址。"""
        c = self.conf()
        base = (c.get("public_base") or "").strip().rstrip("/")
        if base:
            return f"{base}/{key}"
        return self.presigned_get(key, 3600)

    def presigned_get(self, key: str, expires: int = 3600) -> str:
        return self.client().generate_presigned_url(
            "get_object", Params={"Bucket": self.conf()["bucket"], "Key": key},
            ExpiresIn=expires)

    def put_file(self, key: str, path: Path, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else None
        self.client().upload_file(str(path), self.conf()["bucket"], key, ExtraArgs=extra)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client().put_object(Bucket=self.conf()["bucket"], Key=key,
                                 Body=data, ContentType=content_type)

    def download_to(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client().download_file(self.conf()["bucket"], key, str(dest))

    def get_bytes(self, key: str) -> bytes:
        resp = self.client().get_object(Bucket=self.conf()["bucket"], Key=key)
        return resp["Body"].read()

    def delete(self, key: str) -> None:
        self.client().delete_object(Bucket=self.conf()["bucket"], Key=key)

    def list_prefix(self, prefix: str, limit: int = 500) -> list[dict]:
        out = []
        paginator = self.client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.conf()["bucket"], Prefix=prefix):
            for obj in page.get("Contents", []):
                out.append({"key": obj["Key"], "size": obj["Size"],
                            "modified": obj["LastModified"].timestamp()})
                if len(out) >= limit:
                    return out
        return out

    def head(self, key: str) -> dict | None:
        try:
            resp = self.client().head_object(Bucket=self.conf()["bucket"], Key=key)
            return {"size": resp["ContentLength"],
                    "modified": resp["LastModified"].timestamp(),
                    "content_type": resp.get("ContentType")}
        except ClientError:
            return None

    # ------------------------------------------------------------------
    def save_config(self, data: dict) -> dict:
        """更新 R2 配置(空密码字段表示保持不变)。"""
        current = dict(self.conf())
        for field in ("account_id", "bucket", "public_base"):
            if field in data and data[field] is not None:
                current[field] = str(data[field]).strip()
        if data.get("access_key"):
            current["access_key"] = data["access_key"].strip()
        if data.get("secret_key"):
            current["secret_key"] = data["secret_key"].strip()
            self._client = None
        current["enabled"] = bool(self.configured_value(current))
        self.cfg.set("r2", current)
        return self.public_conf()

    @staticmethod
    def configured_value(c: dict) -> bool:
        return bool(c.get("account_id") and c.get("access_key")
                    and c.get("secret_key") and c.get("bucket"))

    def public_conf(self) -> dict:
        c = self.conf()
        return {
            "enabled": self.configured(),
            "account_id": c.get("account_id", ""),
            "bucket": c.get("bucket", ""),
            "public_base": c.get("public_base", ""),
            "has_credentials": bool(c.get("access_key") and c.get("secret_key")),
        }
