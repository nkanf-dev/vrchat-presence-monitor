from __future__ import annotations

import gzip
import io
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, BinaryIO

import ijson


MAX_JSON_DEPTH = 64
MAX_JSON_OBJECTS = 250_000
MAX_JSON_CONTAINERS = 500_000
MAX_OBJECT_MEMBERS = 256
# The production container is 512 MiB. Keep the retained payload well below that
# so request bytes, parser buffers, event normalization, and SQLite all have headroom.
MAX_MATERIALIZED_BYTES = 128 * 1024 * 1024

_MATERIALIZED_ROOT_FIELDS = frozenset({"format", "version", "friends", "status_events"})
_DICT_ENTRY_BYTES = 64
_LIST_ENTRY_BYTES = 16


class _ExpandedBackupTooLarge(Exception):
    pass


class _LimitedReader:
    def __init__(self, source: BinaryIO, maximum: int):
        self.source = source
        self.maximum = maximum
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return self.source.read(0)
        remaining_with_sentinel = self.maximum - self.total + 1
        if size < 0 or size > remaining_with_sentinel:
            size = remaining_with_sentinel
        chunk = self.source.read(size)
        self.total += len(chunk)
        if self.total > self.maximum:
            raise _ExpandedBackupTooLarge
        return chunk


@dataclass
class _Frame:
    kind: str
    materialized: bool
    value: dict[str, Any] | list[Any] | None
    root: bool = False
    pending_key: str | None = None
    seen_keys: set[str] = field(default_factory=set)
    member_count: int = 0


class _BoundedBuilder:
    def __init__(self):
        self.frames: list[_Frame] = []
        self.payload: dict[str, Any] | None = None
        self.object_count = 0
        self.container_count = 0
        self.materialized_bytes = 0
        self.finished = False

    def consume(self, event: str, value: Any) -> None:
        if self.finished:
            raise ValueError("备份文件不是有效 JSON")
        if event in {"start_map", "start_array"}:
            self._start_container(event)
            return
        if event == "map_key":
            self._map_key(value)
            return
        if event in {"string", "number", "boolean", "null"}:
            self._scalar(value)
            return
        if event in {"end_map", "end_array"}:
            self._end_container(event)
            return
        raise ValueError("备份文件不是有效 JSON")

    def result(self) -> dict[str, Any]:
        if not self.finished or self.frames or self.payload is None:
            raise ValueError("备份文件不是有效 JSON")
        return self.payload

    def _start_container(self, event: str) -> None:
        if len(self.frames) + 1 > MAX_JSON_DEPTH:
            raise ValueError("备份 JSON 嵌套层级过深")
        self.container_count += 1
        if self.container_count > MAX_JSON_CONTAINERS:
            raise ValueError("备份 JSON 容器过多")

        kind = "map" if event == "start_map" else "array"
        if kind == "map":
            self.object_count += 1
            if self.object_count > MAX_JSON_OBJECTS:
                raise ValueError("备份 JSON 对象过多")

        if not self.frames:
            if kind != "map":
                raise ValueError("备份文件格式无效")
            materialized = True
            root = True
        else:
            materialized = self._materialize_next(self.frames[-1])
            root = False

        container: dict[str, Any] | list[Any] | None = None
        if materialized:
            container = {} if kind == "map" else []
            self._charge(sys.getsizeof(container))
        self.frames.append(
            _Frame(kind=kind, materialized=materialized, value=container, root=root)
        )

    def _map_key(self, key: Any) -> None:
        if not self.frames or self.frames[-1].kind != "map" or not isinstance(key, str):
            raise ValueError("备份文件不是有效 JSON")
        frame = self.frames[-1]
        if frame.pending_key is not None:
            raise ValueError("备份文件不是有效 JSON")
        frame.member_count += 1
        if frame.member_count > MAX_OBJECT_MEMBERS:
            raise ValueError("备份 JSON 对象字段过多")
        if key in frame.seen_keys:
            raise ValueError("备份文件包含重复字段")
        frame.seen_keys.add(key)
        frame.pending_key = key

    def _scalar(self, value: Any) -> None:
        if not self.frames:
            raise ValueError("备份文件格式无效")
        if isinstance(value, Decimal):
            value = float(value)
        parent = self.frames[-1]
        if self._materialize_next(parent):
            self._attach(parent, value, nested=False)
        else:
            self._consume_slot(parent)

    def _end_container(self, event: str) -> None:
        if not self.frames:
            raise ValueError("备份文件不是有效 JSON")
        frame = self.frames.pop()
        expected = "map" if event == "end_map" else "array"
        if frame.kind != expected or frame.pending_key is not None:
            raise ValueError("备份文件不是有效 JSON")
        if not self.frames:
            if not frame.root or not isinstance(frame.value, dict):
                raise ValueError("备份文件格式无效")
            self.payload = frame.value
            self.finished = True
            return

        parent = self.frames[-1]
        if frame.materialized:
            self._attach(parent, frame.value, nested=True)
        else:
            self._consume_slot(parent)

    @staticmethod
    def _materialize_next(parent: _Frame) -> bool:
        if not parent.materialized:
            return False
        if parent.kind == "array":
            return True
        if parent.pending_key is None:
            raise ValueError("备份文件不是有效 JSON")
        return not parent.root or parent.pending_key in _MATERIALIZED_ROOT_FIELDS

    def _attach(self, parent: _Frame, value: Any, *, nested: bool) -> None:
        if parent.kind == "array":
            if not isinstance(parent.value, list):
                raise ValueError("备份文件不是有效 JSON")
            charge = _LIST_ENTRY_BYTES
            if not nested:
                charge += sys.getsizeof(value)
            self._charge(charge)
            parent.value.append(value)
            return

        if not isinstance(parent.value, dict):
            raise ValueError("备份文件不是有效 JSON")
        key = parent.pending_key
        if key is None:
            raise ValueError("备份文件不是有效 JSON")
        charge = _DICT_ENTRY_BYTES + sys.getsizeof(key)
        if not nested:
            charge += sys.getsizeof(value)
        self._charge(charge)
        parent.value[key] = value
        parent.pending_key = None

    @staticmethod
    def _consume_slot(parent: _Frame) -> None:
        if parent.kind == "map":
            if parent.pending_key is None:
                raise ValueError("备份文件不是有效 JSON")
            parent.pending_key = None

    def _charge(self, amount: int) -> None:
        if self.materialized_bytes + amount > MAX_MATERIALIZED_BYTES:
            raise ValueError("备份 JSON 内存放大过高")
        self.materialized_bytes += amount


def decode_backup(raw: bytes, maximum_expanded: int) -> dict[str, Any]:
    """Stream-decode one compatible backup into a bounded import payload."""
    compressed = raw.startswith(b"\x1f\x8b")
    source: BinaryIO
    if compressed:
        source = gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb")
    else:
        source = io.BytesIO(raw)
    limited = _LimitedReader(source, maximum_expanded)
    builder = _BoundedBuilder()
    try:
        for event, value in ijson.basic_parse(limited):
            builder.consume(event, value)
        return builder.result()
    except _ExpandedBackupTooLarge as error:
        raise ValueError("备份解压后过大") from error
    except (EOFError, OSError) as error:
        if compressed:
            raise ValueError("压缩备份文件已损坏") from error
        raise ValueError("备份文件不是有效 JSON") from error
    except ijson.JSONError as error:
        raise ValueError("备份文件不是有效 JSON") from error
    finally:
        if compressed:
            source.close()
