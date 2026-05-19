"""Azure Blob helpers (managed identity at runtime; PAT only at image build for pip)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient, BlobServiceClient


def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def _split_blob_url(blob_url: str) -> tuple[str, str, str]:
    """Return (account_url, container_name, blob_path_with_slashes)."""

    parsed = urlparse(blob_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported blob URL scheme: {parsed.scheme}")
    account_url = f"{parsed.scheme}://{parsed.netloc}"
    parts = unquote(parsed.path).lstrip("/").split("/", 1)
    if len(parts) < 2:
        raise ValueError(f"could not parse container/path from blob URL: {blob_url}")
    return account_url, parts[0], parts[1]


def download_blob_to_bytes(blob_url: str) -> bytes:
    if blob_url.startswith("file:"):
        parsed = urlparse(blob_url)
        return Path(parsed.path).read_bytes()
    client = BlobClient.from_blob_url(blob_url, credential=_credential())
    return client.download_blob().readall()


def download_blob_to_path(blob_url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = download_blob_to_bytes(blob_url)
    dest.write_bytes(data)


def upload_json_blob(
    account_url: str,
    container: str,
    blob_path: str,
    payload: dict[str, Any],
) -> str:
    bsc = BlobServiceClient(account_url=account_url, credential=_credential())
    blob = bsc.get_blob_client(container=container, blob=blob_path)
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    blob.upload_blob(body, overwrite=True)
    return str(blob.url)


def read_manifest_json(manifest_blob_url: str) -> dict[str, Any]:
    raw = download_blob_to_bytes(manifest_blob_url)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def infer_account_and_container_from_artifacts(manifest: dict[str, Any]) -> tuple[str, str]:
    arts = manifest.get("artifacts")
    if not isinstance(arts, list) or not arts:
        raise ValueError("manifest has no artifacts")
    first = arts[0]
    if not isinstance(first, dict) or not first.get("blob_url"):
        raise ValueError("manifest artifact missing blob_url")
    account_url, container, _ = _split_blob_url(str(first["blob_url"]))
    return account_url, container


def build_run_blob_path(deal_id: str, compile_id: str, *parts: str) -> str:
    base = f"deals/{deal_id}/runs/{compile_id}"
    if not parts:
        return base
    return "/".join((base, *parts))
