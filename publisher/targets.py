"""Storage targets: dir:<path> (local) and r2:<bucket> (S3 API)."""
import logging
import os
import tempfile
import threading
import time

log = logging.getLogger("publisher.target")
CONTENT_TYPE = "application/json; charset=utf-8"


class DirTarget:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _fs(self, path):
        full = os.path.abspath(os.path.join(self.root, *path.split("/")))
        if not full.startswith(self.root + os.sep):
            raise ValueError(f"path escapes target: {path}")
        return full

    def get(self, path):
        try:
            with open(self._fs(path), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def put(self, path, data, cache_control):
        full = self._fs(path)
        d = os.path.dirname(full)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            for attempt in range(20):
                try:
                    os.replace(tmp, full)
                    return
                except PermissionError:  # Windows: the file is open in a reader
                    time.sleep(0.05 * (attempt + 1))
            os.replace(tmp, full)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def list(self, prefix):
        base = self._fs(prefix.rstrip("/"))
        out = []
        if not os.path.isdir(base):
            return out
        for dirpath, _, files in os.walk(base):
            for name in files:
                if name.startswith(".tmp-"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), self.root)
                out.append(rel.replace(os.sep, "/"))
        return out

    def delete(self, path):
        try:
            os.remove(self._fs(path))
        except FileNotFoundError:
            pass


class R2Target:
    def __init__(self, bucket):
        import boto3
        from botocore.config import Config
        acct = os.environ["R2_ACCOUNT_ID"]
        self.bucket = bucket
        self.s3 = boto3.client(
            "s3", endpoint_url=f"https://{acct}.r2.cloudflarestorage.com", region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(retries={"max_attempts": 5, "mode": "standard"}, max_pool_connections=32))
        self._lock = threading.Lock()

    def get(self, path):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=path)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def put(self, path, data, cache_control):
        self.s3.put_object(Bucket=self.bucket, Key=path, Body=data,
                           ContentType=CONTENT_TYPE, CacheControl=cache_control)

    def list(self, prefix):
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            out.extend(o["Key"] for o in page.get("Contents", []))
        return out

    def delete(self, path):
        self.s3.delete_object(Bucket=self.bucket, Key=path)


def make_target(spec):
    kind, _, value = spec.partition(":")
    if kind == "dir" and value:
        return DirTarget(value)
    if kind == "r2" and value:
        return R2Target(value)
    raise ValueError(f"bad --target {spec!r}: use dir:<path> or r2:<bucket>")


def with_retry(fn, *args, attempts=5, base=0.5):
    for i in range(attempts):
        try:
            return fn(*args)
        except Exception as e:
            if i == attempts - 1:
                raise
            log.warning("retrying %s after %s", getattr(fn, "__name__", fn), e)
            time.sleep(base * (2 ** i))
