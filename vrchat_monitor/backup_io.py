from __future__ import annotations

import gzip
import io
import json
from typing import Any


MAX_COMPRESSED_BACKUP_BYTES = 128 * 1024 * 1024
MAX_JSON_BACKUP_BYTES = 512 * 1024 * 1024


class _DuplicateBackupKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateBackupKey(key)
        result[key] = value
    return result


def encode_backup_gzip(
    payload: dict[str, Any],
    *,
    max_compressed_bytes: int = MAX_COMPRESSED_BACKUP_BYTES,
    max_json_bytes: int = MAX_JSON_BACKUP_BYTES,
) -> bytes:
    """Encode a backup only when the same local importer can restore it."""
    if max_compressed_bytes < 1 or max_json_bytes < 1:
        raise ValueError("备份容量上限无效")
    output = io.BytesIO()
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    json_bytes = 0
    with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=6, mtime=0) as archive:
        for chunk in encoder.iterencode(payload):
            encoded = chunk.encode("utf-8")
            json_bytes += len(encoded)
            if json_bytes > max_json_bytes:
                raise ValueError("备份数据超过可恢复的解压上限")
            archive.write(encoded)
    result = output.getvalue()
    if len(result) > max_compressed_bytes:
        raise ValueError("压缩备份超过可恢复的文件上限")
    return result


def decode_backup_upload(
    raw: bytes,
    *,
    max_compressed_bytes: int = MAX_COMPRESSED_BACKUP_BYTES,
    max_json_bytes: int = MAX_JSON_BACKUP_BYTES,
) -> dict[str, Any]:
    """Decode plain JSON or gzip JSON with explicit compressed and expanded limits."""
    if raw.startswith(b"\x1f\x8b"):
        if len(raw) > max_compressed_bytes:
            raise ValueError("压缩备份文件过大")
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as archive:
                decoded = archive.read(max_json_bytes + 1)
        except (EOFError, OSError) as error:
            raise ValueError("压缩备份文件已损坏") from error
        if len(decoded) > max_json_bytes:
            raise ValueError("备份解压后过大")
    else:
        if len(raw) > max_json_bytes:
            raise ValueError("备份文件过大")
        decoded = raw
    try:
        payload = json.loads(
            decoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except _DuplicateBackupKey as error:
        raise ValueError("备份文件包含重复字段") from error
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("备份文件不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("备份文件格式无效")
    return payload
