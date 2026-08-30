from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import zlib
from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .backup_json import (
    MAX_JSON_CONTAINERS,
    MAX_JSON_DEPTH,
    MAX_JSON_OBJECTS,
    MAX_OBJECT_MEMBERS,
    iter_backup_events,
)
from .storage import (
    BACKUP_EVENT_ID_LIMIT,
    EVENT_EXPORT_COLUMNS,
    FRIEND_EXPORT_COLUMNS,
    Store,
    now,
)


BACKUP_V3_FORMAT = "vrchat-monitor-hosted-backup"
BACKUP_V3_VERSION = 3
BACKUP_V3_FIELDS = (
    "friends",
    "status_events",
    "friend_annotations",
    "tags",
    "friend_tags",
    "friend_identity_events",
    "friend_tracking_events",
    "collection_samples",
    "event_anomalies",
    "tenant_preferences",
    "raw_fetches",
)
DEFAULT_MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_NESTED_ARRAY_ITEMS = 256

_ROOT_FIELDS = {
    "format",
    "version",
    "scope",
    "exported_at",
    *BACKUP_V3_FIELDS,
}
_METADATA_FIELDS = {"format", "version", "scope", "exported_at"}
_JSON_ENCODER = json.JSONEncoder(
    ensure_ascii=False,
    separators=(",", ":"),
    allow_nan=False,
)
_EPOCH = "1970-01-01T00:00:00+00:00"

_EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "friends": FRIEND_EXPORT_COLUMNS,
    "status_events": (
        *EVENT_EXPORT_COLUMNS,
        "previous_event_id",
    ),
    "friend_annotations": (
        "friend_id",
        "note",
        "pinned",
        "revision",
        "updated_at",
    ),
    "tags": ("id", "name", "color", "created_at", "updated_at"),
    "friend_tags": ("friend_id", "tag_id", "created_at"),
    "friend_identity_events": (
        "event_id",
        "friend_id",
        "field",
        "old_value",
        "new_value",
        "occurred_at",
        "source",
    ),
    "friend_tracking_events": (
        "event_id",
        "friend_id",
        "tracked",
        "occurred_at",
        "source",
    ),
    "collection_samples": (
        "sample_id",
        "observed_at",
        "source",
        "outcome",
        "authoritative",
        "expected_interval_seconds",
        "friend_count",
        "online_count",
        "duration_ms",
        "error_category",
    ),
    "event_anomalies": (
        "anomaly_id",
        "event_kind",
        "event_id",
        "reason",
        "detected_at",
    ),
    "tenant_preferences": ("timezone", "updated_at"),
    "raw_fetches": (
        "client_fetch_id",
        "occurred_at",
        "method",
        "path",
        "status_code",
        "content_type",
        "body",
        "error",
    ),
}

_EXPORT_ORDER = {
    "friends": "id",
    "status_events": "occurred_at,client_event_id",
    "friend_annotations": "friend_id",
    "tags": "id",
    "friend_tags": "tag_id,friend_id",
    "friend_identity_events": "occurred_at,event_id",
    "friend_tracking_events": "occurred_at,event_id",
    "collection_samples": "observed_at,sample_id",
    "event_anomalies": "detected_at,anomaly_id",
    "tenant_preferences": "tenant_id",
    "raw_fetches": "occurred_at,client_fetch_id",
}


def _encode_json(value: Any) -> Iterator[bytes]:
    for chunk in _JSON_ENCODER.iterencode(value):
        yield chunk.encode("utf-8")


def _export_record(field: str, row: sqlite3.Row) -> dict[str, Any]:
    if field == "status_events":
        return Store._export_event_item(row)
    if field == "raw_fetches":
        return {
            "client_fetch_id": row["client_fetch_id"],
            "occurred_at": row["occurred_at"],
            "method": row["method"],
            "path": row["path"],
            "status_code": row["status_code"],
            "content_type": row["content_type"],
            "body_b64": base64.b64encode(bytes(row["body"] or b"")).decode("ascii"),
            "error": row["error"],
        }
    return {column: row[column] for column in _EXPORT_COLUMNS[field]}


def _iter_tenant_json(
    store: Store,
    tenant_id: str,
    include_raw: bool,
) -> Iterator[bytes]:
    exported_at = now()
    with store.connection() as db:
        db.execute("BEGIN")
        Store._require_tenant(db, tenant_id)
        yield b'{"format":'
        yield from _encode_json(BACKUP_V3_FORMAT)
        yield b',"version":3,"scope":'
        yield from _encode_json("full" if include_raw else "normalized")
        yield b',"exported_at":'
        yield from _encode_json(exported_at)
        for field in BACKUP_V3_FIELDS:
            yield b',"' + field.encode("ascii") + b'":['
            first = True
            if field != "raw_fetches" or include_raw:
                columns = _EXPORT_COLUMNS[field]
                cursor = db.execute(
                    f"SELECT {','.join(columns)} FROM {field} "
                    f"WHERE tenant_id=? ORDER BY {_EXPORT_ORDER[field]}",
                    (tenant_id,),
                )
                while True:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    if not first:
                        yield b","
                    first = False
                    yield from _encode_json(_export_record(field, row))
            yield b"]"
        yield b"}"


def stream_tenant_backup(
    store: Store,
    tenant_id: str,
    include_raw: bool = True,
) -> Iterator[bytes]:
    """Stream one tenant backup as gzip without materializing its collections."""
    compressor = zlib.compressobj(level=9, wbits=31)
    for chunk in _iter_tenant_json(store, tenant_id, bool(include_raw)):
        compressed = compressor.compress(chunk)
        if compressed:
            yield compressed
    tail = compressor.flush()
    if tail:
        yield tail


def _required_keys(
    record: dict[str, Any],
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    if set(record) - allowed:
        raise ValueError("v3 备份记录包含不支持的字段")
    if required_set - set(record):
        raise ValueError("v3 备份记录缺少字段")


def _text(value: Any, maximum: int, label: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}格式无效")
    if required and (not value or value != value.strip()):
        raise ValueError(f"{label}格式无效")
    if len(value) > maximum:
        raise ValueError(f"{label}超过长度上限（{maximum}）")
    return value


def _timestamp(value: Any, label: str) -> str:
    text = _text(value, 64, label, required=True)
    return Store._timestamp(text, _EPOCH, label)


def _validated_timestamp(value: Any, label: str) -> str:
    text = _text(value, 64, label, required=True)
    Store._timestamp(text, _EPOCH, label)
    return text


def _boolean(value: Any, label: str) -> int:
    return Store._boolean(value, label)


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label}格式无效")
    return value


def _stable_id(value: Any, maximum: int, label: str) -> str:
    return _text(value, maximum, label, required=True)


def _normalize_record(field: str, record: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    if field == "friends":
        _required_keys(record, FRIEND_EXPORT_COLUMNS)
        values = Store._friend_values(record, _EPOCH)
        normalized = dict(zip(FRIEND_EXPORT_COLUMNS, values))
        friend_id = _stable_id(normalized["id"], 128, "玩家 ID")
        return friend_id, friend_id, "", normalized

    if field == "status_events":
        _required_keys(record, EVENT_EXPORT_COLUMNS, ("previous_event_ids",))
        event_id = _stable_id(
            record["client_event_id"], BACKUP_EVENT_ID_LIMIT, "历史记录稳定 ID"
        )
        friend_id = _stable_id(record["friend_id"], 128, "历史记录玩家 ID")
        previous = record.get("previous_event_ids", [])
        if not isinstance(previous, list) or len(previous) > 1:
            raise ValueError("历史记录旧稳定 ID 格式无效")
        normalized_previous = [
            _stable_id(item, 64, "历史记录旧稳定 ID") for item in previous
        ]
        normalized = {
            "client_event_id": event_id,
            "friend_id": friend_id,
            "occurred_at": _timestamp(record["occurred_at"], "历史记录时间"),
            "old_status": _text(record["old_status"], 40, "旧状态"),
            "new_status": _text(record["new_status"], 40, "新状态"),
            "location": _text(record["location"], 1024, "历史位置"),
            "platform": _text(record["platform"], 80, "历史平台"),
            "source": _text(record["source"], 80, "历史来源"),
        }
        if normalized_previous:
            normalized["previous_event_ids"] = normalized_previous
        return event_id, friend_id, "", normalized

    if field == "friend_annotations":
        columns = _EXPORT_COLUMNS[field]
        _required_keys(record, columns)
        friend_id = _stable_id(record["friend_id"], 128, "批注玩家 ID")
        normalized = {
            "friend_id": friend_id,
            "note": _text(record["note"], 8192, "批注"),
            "pinned": _boolean(record["pinned"], "置顶状态"),
            "revision": _stable_id(record["revision"], 256, "批注修订 ID"),
            "updated_at": _timestamp(record["updated_at"], "批注更新时间"),
        }
        return friend_id, friend_id, "", normalized

    if field == "tags":
        _required_keys(record, _EXPORT_COLUMNS[field])
        tag_id = _stable_id(record["id"], 128, "标签 ID")
        normalized = {
            "id": tag_id,
            "name": _text(record["name"], 120, "标签名称", required=True),
            "color": _text(record["color"], 32, "标签颜色", required=True),
            "created_at": _timestamp(record["created_at"], "标签创建时间"),
            "updated_at": _timestamp(record["updated_at"], "标签更新时间"),
        }
        return tag_id, tag_id, "", normalized

    if field == "friend_tags":
        _required_keys(record, _EXPORT_COLUMNS[field])
        friend_id = _stable_id(record["friend_id"], 128, "标签玩家 ID")
        tag_id = _stable_id(record["tag_id"], 128, "标签 ID")
        normalized = {
            "friend_id": friend_id,
            "tag_id": tag_id,
            "created_at": _timestamp(record["created_at"], "标签关联时间"),
        }
        stable = json.dumps([friend_id, tag_id], separators=(",", ":"))
        return stable, friend_id, tag_id, normalized

    if field == "friend_identity_events":
        _required_keys(record, _EXPORT_COLUMNS[field])
        event_id = _stable_id(record["event_id"], 256, "身份记录稳定 ID")
        friend_id = _stable_id(record["friend_id"], 128, "身份记录玩家 ID")
        identity_field = _text(record["field"], 32, "身份字段", required=True)
        if identity_field not in {"username", "display_name"}:
            raise ValueError("身份字段格式无效")
        normalized = {
            "event_id": event_id,
            "friend_id": friend_id,
            "field": identity_field,
            "old_value": _text(record["old_value"], 256, "旧身份值"),
            "new_value": _text(record["new_value"], 256, "新身份值"),
            "occurred_at": _timestamp(record["occurred_at"], "身份记录时间"),
            "source": _text(record["source"], 80, "身份记录来源"),
        }
        return event_id, friend_id, "", normalized

    if field == "friend_tracking_events":
        _required_keys(record, _EXPORT_COLUMNS[field])
        event_id = _stable_id(record["event_id"], 256, "追踪记录稳定 ID")
        friend_id = _stable_id(record["friend_id"], 128, "追踪记录玩家 ID")
        normalized = {
            "event_id": event_id,
            "friend_id": friend_id,
            "tracked": _boolean(record["tracked"], "追踪状态"),
            "occurred_at": _timestamp(record["occurred_at"], "追踪记录时间"),
            "source": _text(record["source"], 80, "追踪记录来源"),
        }
        return event_id, friend_id, "", normalized

    if field == "collection_samples":
        _required_keys(record, _EXPORT_COLUMNS[field])
        sample_id = _stable_id(record["sample_id"], 256, "采样稳定 ID")
        interval = _optional_nonnegative_int(
            record["expected_interval_seconds"], "采样间隔"
        )
        if interval is None or interval < 45 or interval > 3600:
            raise ValueError("采样间隔格式无效")
        outcome = _text(record["outcome"], 40, "采样结果", required=True)
        if outcome not in {"success", "failure"}:
            raise ValueError("采样结果格式无效")
        normalized = {
            "sample_id": sample_id,
            "observed_at": _timestamp(record["observed_at"], "采样时间"),
            "source": _text(record["source"], 80, "采样来源", required=True),
            "outcome": outcome,
            "authoritative": _boolean(record["authoritative"], "采样权威标记"),
            "expected_interval_seconds": interval,
            "friend_count": _optional_nonnegative_int(record["friend_count"], "玩家数量"),
            "online_count": _optional_nonnegative_int(record["online_count"], "在线数量"),
            "duration_ms": _optional_nonnegative_int(record["duration_ms"], "采样耗时"),
            "error_category": _text(record["error_category"], 80, "错误分类"),
        }
        return sample_id, "", "", normalized

    if field == "event_anomalies":
        _required_keys(record, _EXPORT_COLUMNS[field])
        anomaly_id = _stable_id(record["anomaly_id"], 256, "异常稳定 ID")
        event_id = _stable_id(record["event_id"], 512, "异常目标 ID")
        normalized = {
            "anomaly_id": anomaly_id,
            "event_kind": _text(record["event_kind"], 80, "异常目标类型", required=True),
            "event_id": event_id,
            "reason": _text(record["reason"], 256, "异常原因", required=True),
            "detected_at": _timestamp(record["detected_at"], "异常检测时间"),
        }
        return anomaly_id, normalized["event_kind"], event_id, normalized

    if field == "tenant_preferences":
        _required_keys(record, _EXPORT_COLUMNS[field])
        timezone_name = _text(record["timezone"], 80, "时区", required=True)
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError("时区格式无效") from error
        normalized = {
            "timezone": timezone_name,
            "updated_at": _timestamp(record["updated_at"], "偏好更新时间"),
        }
        return "tenant_preferences", "", "", normalized

    if field == "raw_fetches":
        required = (
            "client_fetch_id",
            "occurred_at",
            "method",
            "path",
            "status_code",
            "content_type",
            "body_b64",
            "error",
        )
        _required_keys(record, required)
        fetch_id = _stable_id(record["client_fetch_id"], 256, "原始响应稳定 ID")
        status_code = record["status_code"]
        if status_code is not None and (
            isinstance(status_code, bool) or not isinstance(status_code, int)
        ):
            raise ValueError("响应状态码格式无效")
        body_b64 = _text(record["body_b64"], MAX_RECORD_BYTES, "原始响应正文")
        try:
            base64.b64decode(body_b64.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error) as error:
            raise ValueError("原始响应正文格式无效") from error
        normalized = {
            "client_fetch_id": fetch_id,
            "occurred_at": _validated_timestamp(record["occurred_at"], "原始响应时间"),
            "method": _text(record["method"], 12, "请求方法", required=True),
            "path": _text(record["path"], 2048, "请求路径"),
            "status_code": status_code,
            "content_type": _text(record["content_type"], 256, "响应类型"),
            "body_b64": body_b64,
            "error": _text(record["error"], 500, "请求错误"),
        }
        return fetch_id, "", "", normalized

    raise ValueError("v3 备份包含不支持的数据集")


class _V3Parser:
    def __init__(
        self,
        raw: bytes,
        maximum_expanded: int,
        db: sqlite3.Connection,
    ) -> None:
        self.events = iter_backup_events(raw, maximum_expanded)
        self.maximum_record_bytes = min(MAX_RECORD_BYTES, maximum_expanded)
        self.db = db
        self.container_count = 0
        self.object_count = 0
        self.ordinal = 0

    def _next(self) -> tuple[str, Any]:
        try:
            return next(self.events)
        except StopIteration as error:
            raise ValueError("备份文件不是有效 JSON") from error

    def _open_container(self, kind: str, depth: int) -> None:
        if depth > MAX_JSON_DEPTH:
            raise ValueError("备份 JSON 嵌套层级过深")
        self.container_count += 1
        if self.container_count > MAX_JSON_CONTAINERS:
            raise ValueError("备份 JSON 容器过多")
        if kind == "map":
            self.object_count += 1
            if self.object_count > MAX_JSON_OBJECTS:
                raise ValueError("备份 JSON 对象过多")

    def _parse_value(self, event: str, value: Any, depth: int) -> Any:
        if event in {"string", "boolean", "null"}:
            return value
        if event == "number":
            return float(value) if isinstance(value, Decimal) else value
        if event == "start_array":
            self._open_container("array", depth)
            result: list[Any] = []
            while True:
                child_event, child_value = self._next()
                if child_event == "end_array":
                    return result
                if len(result) >= MAX_NESTED_ARRAY_ITEMS:
                    raise ValueError("v3 备份记录数组元素过多")
                result.append(self._parse_value(child_event, child_value, depth + 1))
        if event == "start_map":
            self._open_container("map", depth)
            result_map: dict[str, Any] = {}
            while True:
                child_event, child_value = self._next()
                if child_event == "end_map":
                    return result_map
                if child_event != "map_key" or not isinstance(child_value, str):
                    raise ValueError("备份文件不是有效 JSON")
                if child_value in result_map:
                    raise ValueError("备份文件包含重复字段")
                if len(result_map) >= MAX_OBJECT_MEMBERS:
                    raise ValueError("备份 JSON 对象字段过多")
                value_event, nested_value = self._next()
                result_map[child_value] = self._parse_value(
                    value_event, nested_value, depth + 1
                )
        raise ValueError("备份文件不是有效 JSON")

    def _stage_array(self, field: str, depth: int) -> int:
        self._open_container("array", depth)
        count = 0
        while True:
            event, value = self._next()
            if event == "end_array":
                return count
            record = self._parse_value(event, value, depth + 1)
            if not isinstance(record, dict):
                raise ValueError("v3 备份数组包含无效记录")
            encoded = json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            if len(encoded.encode("utf-8")) > self.maximum_record_bytes:
                raise ValueError("v3 备份单条记录过大")
            stable, ref1, ref2, normalized = _normalize_record(field, record)
            self.ordinal += 1
            try:
                self.db.execute(
                    """INSERT INTO backup_v3_stage(
                        kind,stable_id,ref1,ref2,ordinal,payload_json
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        field,
                        stable,
                        ref1,
                        ref2,
                        self.ordinal,
                        json.dumps(
                            normalized,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("v3 备份包含重复稳定 ID") from error
            count += 1

    def parse(self) -> tuple[dict[str, Any], dict[str, int]]:
        try:
            return self._parse()
        finally:
            self.events.close()

    def _parse(self) -> tuple[dict[str, Any], dict[str, int]]:
        event, _ = self._next()
        if event != "start_map":
            raise ValueError("备份文件格式无效")
        self._open_container("map", 1)
        seen: set[str] = set()
        metadata: dict[str, Any] = {}
        counts: dict[str, int] = {}
        while True:
            event, value = self._next()
            if event == "end_map":
                break
            if event != "map_key" or not isinstance(value, str):
                raise ValueError("备份文件不是有效 JSON")
            if value in seen:
                raise ValueError("备份文件包含重复字段")
            if len(seen) >= MAX_OBJECT_MEMBERS:
                raise ValueError("备份 JSON 对象字段过多")
            seen.add(value)
            if value not in _ROOT_FIELDS:
                raise ValueError("v3 备份包含不支持的顶层字段")
            value_event, scalar = self._next()
            if value in BACKUP_V3_FIELDS:
                if value_event != "start_array":
                    raise ValueError("v3 备份数据集必须是数组")
                counts[value] = self._stage_array(value, 2)
                continue
            if value_event not in {"string", "number", "boolean", "null"}:
                raise ValueError("v3 备份元数据格式无效")
            metadata[value] = (
                float(scalar) if isinstance(scalar, Decimal) else scalar
            )
        try:
            next(self.events)
        except StopIteration:
            pass
        else:
            raise ValueError("备份文件不是有效 JSON")

        if seen != _ROOT_FIELDS:
            raise ValueError("v3 备份缺少必要字段")
        if (
            metadata.get("format") != BACKUP_V3_FORMAT
            or type(metadata.get("version")) is not int
            or metadata.get("version") != BACKUP_V3_VERSION
        ):
            raise ValueError("不是有效的 v3 备份文件")
        if metadata.get("scope") not in {"full", "normalized"}:
            raise ValueError("v3 备份范围格式无效")
        _timestamp(metadata.get("exported_at"), "备份导出时间")
        if metadata["scope"] == "normalized" and counts["raw_fetches"]:
            raise ValueError("规范化 v3 备份不能包含原始响应")
        return metadata, counts


def _create_staging(db: sqlite3.Connection) -> None:
    db.execute("DROP TABLE IF EXISTS temp.backup_v3_stage")
    db.execute(
        """CREATE TEMP TABLE backup_v3_stage(
            kind TEXT NOT NULL,
            stable_id TEXT NOT NULL,
            ref1 TEXT NOT NULL,
            ref2 TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(kind,stable_id)
        ) WITHOUT ROWID"""
    )
    db.execute(
        """CREATE INDEX backup_v3_stage_references
        ON backup_v3_stage(kind,ref1,ref2)"""
    )


def _stage_records(
    db: sqlite3.Connection, field: str, batch_size: int = 500
) -> Iterator[list[dict[str, Any]]]:
    cursor = db.execute(
        """SELECT payload_json FROM backup_v3_stage
        WHERE kind=? ORDER BY ordinal""",
        (field,),
    )
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield [json.loads(row["payload_json"]) for row in rows]


def _validate_references(db: sqlite3.Connection, tenant_id: str) -> None:
    friend_kinds = (
        "status_events",
        "friend_annotations",
        "friend_tags",
        "friend_identity_events",
        "friend_tracking_events",
    )
    placeholders = ",".join("?" for _ in friend_kinds)
    missing_friend = db.execute(
        f"""SELECT kind,stable_id FROM backup_v3_stage AS item
        WHERE kind IN ({placeholders})
          AND NOT EXISTS(
            SELECT 1 FROM backup_v3_stage AS staged_friend
            WHERE staged_friend.kind='friends' AND staged_friend.stable_id=item.ref1
          )
          AND NOT EXISTS(
            SELECT 1 FROM friends
            WHERE tenant_id=? AND id=item.ref1
          ) LIMIT 1""",
        (*friend_kinds, tenant_id),
    ).fetchone()
    if missing_friend is not None:
        raise ValueError("v3 备份记录引用了不存在的玩家")

    missing_tag = db.execute(
        """SELECT stable_id FROM backup_v3_stage AS item
        WHERE kind='friend_tags'
          AND NOT EXISTS(
            SELECT 1 FROM backup_v3_stage AS staged_tag
            WHERE staged_tag.kind='tags' AND staged_tag.stable_id=item.ref2
          )
          AND NOT EXISTS(
            SELECT 1 FROM tags WHERE tenant_id=? AND id=item.ref2
          ) LIMIT 1""",
        (tenant_id,),
    ).fetchone()
    if missing_tag is not None:
        raise ValueError("v3 备份标签关联引用了不存在的标签")

    anomaly_targets = {
        "status_event": ("status_events", "status_events", "client_event_id"),
        "collection_sample": ("collection_samples", "collection_samples", "sample_id"),
        "friend_tracking": (
            "friend_tracking_events",
            "friend_tracking_events",
            "event_id",
        ),
        "friend_identity": (
            "friend_identity_events",
            "friend_identity_events",
            "event_id",
        ),
        "raw_fetch": ("raw_fetches", "raw_fetches", "client_fetch_id"),
    }
    for row in db.execute(
        """SELECT stable_id,ref1,ref2 FROM backup_v3_stage
        WHERE kind='event_anomalies' ORDER BY ordinal"""
    ).fetchall():
        target = anomaly_targets.get(str(row["ref1"]))
        if target is None:
            raise ValueError("v3 备份异常记录引用类型无效")
        stage_kind, live_table, live_id = target
        if db.execute(
            """SELECT 1 FROM backup_v3_stage
            WHERE kind=? AND stable_id=?""",
            (stage_kind, row["ref2"]),
        ).fetchone() is not None:
            continue
        if db.execute(
            f"SELECT 1 FROM {live_table} WHERE tenant_id=? AND {live_id}=?",
            (tenant_id, row["ref2"]),
        ).fetchone() is None:
            raise ValueError("v3 备份异常记录引用了不存在的记录")


def _merge_mutable(
    db: sqlite3.Connection,
    tenant_id: str,
    table: str,
    key_column: str,
    columns: tuple[str, ...],
    record: dict[str, Any],
) -> bool:
    key = record[key_column]
    existing = db.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE tenant_id=? AND {key_column}=?",
        (tenant_id, key),
    ).fetchone()
    values = tuple(record[column] for column in columns)
    if existing is None:
        db.execute(
            f"INSERT INTO {table}(tenant_id,{','.join(columns)}) "
            f"VALUES({','.join('?' for _ in range(len(columns) + 1))})",
            (tenant_id, *values),
        )
        return True
    old_values = tuple(existing[column] for column in columns)
    if old_values == values:
        return False
    old_updated = str(existing["updated_at"])
    new_updated = str(record["updated_at"])
    if new_updated < old_updated:
        return False
    if new_updated == old_updated:
        raise ValueError(f"{table} 稳定 ID 与现有内容冲突")
    assignments = ",".join(f"{column}=?" for column in columns if column != key_column)
    update_columns = tuple(column for column in columns if column != key_column)
    db.execute(
        f"UPDATE {table} SET {assignments} WHERE tenant_id=? AND {key_column}=?",
        (*(record[column] for column in update_columns), tenant_id, key),
    )
    return True


def _merge_immutable(
    db: sqlite3.Connection,
    tenant_id: str,
    table: str,
    key_column: str,
    columns: tuple[str, ...],
    record: dict[str, Any],
) -> bool:
    key = record[key_column]
    existing = db.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE tenant_id=? AND {key_column}=?",
        (tenant_id, key),
    ).fetchone()
    values = tuple(record[column] for column in columns)
    if existing is not None:
        if tuple(existing[column] for column in columns) != values:
            raise ValueError(f"{table} 稳定 ID 与现有内容冲突")
        return False
    db.execute(
        f"INSERT INTO {table}(tenant_id,{','.join(columns)}) "
        f"VALUES({','.join('?' for _ in range(len(columns) + 1))})",
        (tenant_id, *values),
    )
    return True


def _merge_staged(
    store: Store,
    db: sqlite3.Connection,
    tenant_id: str,
) -> dict[str, int]:
    result = {field: 0 for field in BACKUP_V3_FIELDS}
    for batch in _stage_records(db, "friends"):
        imported = store.ingest(
            tenant_id,
            "import",
            batch,
            [],
            "import",
            friend_batch_limit=None,
            event_batch_limit=None,
            _db=db,
        )
        result["friends"] += int(imported["friends"])
    for batch in _stage_records(db, "status_events"):
        imported = store.ingest(
            tenant_id,
            "import",
            [],
            batch,
            "import",
            friend_batch_limit=None,
            event_batch_limit=None,
            _db=db,
        )
        result["status_events"] += int(imported["events"])

    for batch in _stage_records(db, "friend_annotations"):
        for record in batch:
            result["friend_annotations"] += int(
                _merge_mutable(
                    db,
                    tenant_id,
                    "friend_annotations",
                    "friend_id",
                    _EXPORT_COLUMNS["friend_annotations"],
                    record,
                )
            )

    for batch in _stage_records(db, "tags"):
        for record in batch:
            name_owner = db.execute(
                """SELECT id FROM tags
                WHERE tenant_id=? AND name=? COLLATE NOCASE""",
                (tenant_id, record["name"]),
            ).fetchone()
            if name_owner is not None and str(name_owner["id"]) != record["id"]:
                raise ValueError("tags 稳定 ID 与现有名称冲突")
            result["tags"] += int(
                _merge_mutable(
                    db,
                    tenant_id,
                    "tags",
                    "id",
                    _EXPORT_COLUMNS["tags"],
                    record,
                )
            )

    for batch in _stage_records(db, "friend_tags"):
        for record in batch:
            existing = db.execute(
                """SELECT created_at FROM friend_tags
                WHERE tenant_id=? AND friend_id=? AND tag_id=?""",
                (tenant_id, record["friend_id"], record["tag_id"]),
            ).fetchone()
            if existing is not None:
                if str(existing["created_at"]) != record["created_at"]:
                    raise ValueError("friend_tags 稳定 ID 与现有内容冲突")
                continue
            db.execute(
                """INSERT INTO friend_tags(tenant_id,friend_id,tag_id,created_at)
                VALUES(?,?,?,?)""",
                (
                    tenant_id,
                    record["friend_id"],
                    record["tag_id"],
                    record["created_at"],
                ),
            )
            result["friend_tags"] += 1

    immutable_specs = {
        "friend_identity_events": ("event_id", _EXPORT_COLUMNS["friend_identity_events"]),
        "friend_tracking_events": ("event_id", _EXPORT_COLUMNS["friend_tracking_events"]),
        "collection_samples": ("sample_id", _EXPORT_COLUMNS["collection_samples"]),
        "event_anomalies": ("anomaly_id", _EXPORT_COLUMNS["event_anomalies"]),
    }
    for field, (key, columns) in immutable_specs.items():
        for batch in _stage_records(db, field):
            for record in batch:
                result[field] += int(
                    _merge_immutable(db, tenant_id, field, key, columns, record)
                )

    for batch in _stage_records(db, "tenant_preferences"):
        for record in batch:
            existing = db.execute(
                """SELECT timezone,updated_at FROM tenant_preferences
                WHERE tenant_id=?""",
                (tenant_id,),
            ).fetchone()
            values = (record["timezone"], record["updated_at"])
            if existing is None:
                db.execute(
                    """INSERT INTO tenant_preferences(tenant_id,timezone,updated_at)
                    VALUES(?,?,?)""",
                    (tenant_id, *values),
                )
                result["tenant_preferences"] += 1
            elif tuple(existing) == values:
                continue
            elif record["updated_at"] < str(existing["updated_at"]):
                continue
            elif record["updated_at"] == str(existing["updated_at"]):
                raise ValueError("tenant_preferences 稳定 ID 与现有内容冲突")
            else:
                db.execute(
                    """UPDATE tenant_preferences SET timezone=?,updated_at=?
                    WHERE tenant_id=?""",
                    (*values, tenant_id),
                )
                result["tenant_preferences"] += 1

    for batch in _stage_records(db, "raw_fetches"):
        for staged in batch:
            record = dict(staged)
            record["body"] = base64.b64decode(record.pop("body_b64"), validate=True)
            result["raw_fetches"] += int(
                _merge_immutable(
                    db,
                    tenant_id,
                    "raw_fetches",
                    "client_fetch_id",
                    _EXPORT_COLUMNS["raw_fetches"],
                    record,
                )
            )
    return result


def import_tenant_backup(
    store: Store,
    tenant_id: str,
    raw: bytes,
    maximum_expanded: int = DEFAULT_MAX_EXPANDED_BYTES,
) -> dict[str, int]:
    """Stage, validate, and atomically merge one tenant-scoped v3 backup."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ValueError("备份文件格式无效")
    maximum = int(maximum_expanded)
    if maximum <= 0:
        raise ValueError("备份解压上限无效")
    encoded = bytes(raw)
    with store.lock, store.connection() as db:
        _create_staging(db)
        try:
            _V3Parser(encoded, maximum, db).parse()
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            Store._require_tenant(db, tenant_id)
            _validate_references(db, tenant_id)
            result = _merge_staged(store, db, tenant_id)
            db.commit()
            return result
        except sqlite3.IntegrityError as error:
            db.rollback()
            raise ValueError("v3 备份稳定 ID 或引用与现有数据冲突") from error
        except Exception:
            db.rollback()
            raise
        finally:
            db.execute("DROP TABLE IF EXISTS temp.backup_v3_stage")
            db.commit()
