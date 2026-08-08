from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from vlog_editor.project import read_json, write_json


def file_fingerprint(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    stat = path.stat()
    digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    with path.open("rb") as handle:
        digest.update(handle.read(chunk_size))
        if stat.st_size > chunk_size:
            handle.seek(max(0, stat.st_size - chunk_size))
            digest.update(handle.read(chunk_size))
    return digest.hexdigest()


def cache_key(path: Path, namespace: str, settings: dict[str, Any]) -> str:
    payload = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(f"{namespace}:{file_fingerprint(path)}:{payload}".encode()).hexdigest()


class JsonCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.is_file():
            return None
        try:
            return read_json(path)
        except (OSError, ValueError):
            return None

    def put(self, namespace: str, key: str, value: Any) -> None:
        write_json(self._path(namespace, key), value)
