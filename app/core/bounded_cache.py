"""Bounded in-memory session caches (LRU) for DB/Redis fallback paths."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LRUDict(OrderedDict, Generic[K, V]):
    def __init__(self, maxsize: int = 500, *args: Any, **kwargs: Any) -> None:
        self.maxsize = max(1, int(maxsize or 500))
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: K, value: V) -> None:  # type: ignore[override]
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self.maxsize:
            self.popitem(last=False)

    def get_touch(self, key: K, default: V | None = None) -> V | None:
        if key not in self:
            return default
        self.move_to_end(key)
        return self[key]
