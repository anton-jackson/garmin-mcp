"""Sync the garminconnect session token directory to/from a GCS bucket.

Cloud Run containers are ephemeral, so we persist the login token in GCS. On
startup we pull it down to a local temp dir; after a successful (re)login we
push it back up. This avoids re-doing Garmin SSO on every cold start.
"""
from __future__ import annotations

import os
import tarfile
import tempfile
from pathlib import Path

from google.cloud import storage

_BLOB_NAME = "garminconnect-session.tar"


class SessionStore:
    def __init__(self, bucket: str, local_dir: Path):
        self._bucket_name = bucket
        self._local_dir = local_dir
        self._client: storage.Client | None = None

    @property
    def client(self) -> storage.Client:
        if self._client is None:
            self._client = storage.Client()
        return self._client

    def pull(self) -> None:
        self._local_dir.mkdir(parents=True, exist_ok=True)
        blob = self.client.bucket(self._bucket_name).blob(_BLOB_NAME)
        if not blob.exists():
            return
        with tempfile.NamedTemporaryFile(suffix=".tar") as tmp:
            blob.download_to_filename(tmp.name)
            with tarfile.open(tmp.name, "r") as tar:
                tar.extractall(self._local_dir)

    def push(self) -> None:
        if not self._local_dir.exists():
            return
        with tempfile.NamedTemporaryFile(suffix=".tar") as tmp:
            with tarfile.open(tmp.name, "w") as tar:
                tar.add(self._local_dir, arcname=".")
            blob = self.client.bucket(self._bucket_name).blob(_BLOB_NAME)
            blob.upload_from_filename(tmp.name)


def default_store() -> SessionStore | None:
    bucket = os.environ.get("GARMIN_SESSION_BUCKET")
    if not bucket:
        return None
    local = Path(os.environ.get("GARMIN_SESSION_DIR", "/tmp/garminconnect"))
    return SessionStore(bucket=bucket, local_dir=local)
