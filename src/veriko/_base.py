"""Building blocks shared by the models."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any, ClassVar, Generic, Tuple, Type, TypeVar  # noqa: UP035

from ._http import ResponseInfo

T = TypeVar("T")
A = TypeVar("A", bound="ApiObject")


class ResponseCarrier:
    """A result that remembers the response it came from.

    `response_info` holds the HTTP metadata of the call that produced the object:
    rate-limit headers, `Idempotent-Replayed`, `Deprecation` and `Sunset`, `ETag`
    and the request identifier. It is `None` on an object built by hand.
    """

    response_info: ResponseInfo | None = None


def attach(result: T, info: ResponseInfo | None) -> T:
    """Stores `info` on `result` and returns it."""
    if info is not None:
        with contextlib.suppress(AttributeError, TypeError):
            object.__setattr__(result, "response_info", info)
    return result


class ApiList(list[T], Generic[T], ResponseCarrier):
    """A list result that also carries the metadata of its response.

    `meta` is the `meta` object of the response body.
    """

    def __init__(self, items: Any = (), *, meta: dict[str, Any] | None = None) -> None:
        super().__init__(items)
        self.meta: dict[str, Any] = meta or {}


class ApiDict(dict[str, Any], ResponseCarrier):
    """A dictionary result that also carries the metadata of its response.

    It behaves as the plain `dict` those methods returned before.
    """


_RESERVED = frozenset({"id", "type", "meta", "links", "raw", "response_info"})


# Field name -> (model of its value, whether the value is a list of them). It is spelled with
# `Type` because `type` is also the name of a field of the object.
NestedFields = Mapping[str, Tuple[Type["ApiObject"], bool]]  # noqa: UP006


class ApiObject(ApiDict):
    """A dictionary result with typed attribute access.

    The object *is* the dictionary the method returned before it had a model:
    `result["total"]`, `result.get("total")`, `"total" in result`, iteration,
    `==` against a dictionary and `json.dumps()` all behave as they did. The fields
    the spec declares are also readable as attributes, `result.total`, with the type
    each subclass annotates. A field the response does not carry reads as `None`.

    Besides the attributes, the parts of the response that used to be dropped are
    kept on the object, outside the dictionary: `id` and `type` of the resource,
    `links`, `meta` (with `request_id`) and `raw`, the whole body.
    """

    id: str | None
    type: str | None
    links: dict[str, Any]
    meta: dict[str, Any]
    raw: dict[str, Any]

    # The fields of the model, filled in for each subclass from its annotations.
    _fields: ClassVar[frozenset[str]] = frozenset()
    # Field name -> key in the JSON, when they differ (`from_` is `from`).
    _keys: ClassVar[Mapping[str, str]] = {}
    # Field name -> (model of its value, whether the value is a list of them).
    _nested: ClassVar[NestedFields] = {}
    # What the dictionary holds: the `attributes` of the resource or the whole `data`.
    _content: ClassVar[str] = "attributes"
    # Where the fields live: in the dictionary itself or in its `attributes`.
    _reads: ClassVar[str] = "self"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        names: set[str] = set()
        for klass in cls.__mro__:
            for name, annotation in vars(klass).get("__annotations__", {}).items():
                if name.startswith("_") or name in _RESERVED or "ClassVar" in str(annotation):
                    continue
                names.add(name)
        cls._fields = frozenset(names)

    def __init__(self, data: Any = (), /, **fields: Any) -> None:
        super().__init__(data, **fields)
        self.id = None
        self.type = None
        self.links = {}
        self.meta = {}
        self.raw = {}

    def __getattr__(self, name: str) -> Any:
        cls = type(self)
        if name.startswith("_") or name not in cls._fields:
            raise AttributeError(f"{cls.__name__!r} object has no attribute {name!r}")
        source: Any = self
        if cls._reads == "attributes":
            source = self.get("attributes")
        if not isinstance(source, dict):
            return None
        value = source.get(cls._keys.get(name, name))
        nested = cls._nested.get(name)
        if value is None or nested is None:
            return value
        model, many = nested
        if many:
            return [model(item) for item in value if isinstance(item, dict)]
        return model(value) if isinstance(value, dict) else None

    @classmethod
    def from_item(
        cls: Type[A],  # noqa: UP006
        item: Mapping[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> A:
        """Builds the object from a `data` resource."""
        if cls._content == "data":
            content: Any = item
        else:
            content = item.get("attributes")
        obj = cls(content if isinstance(content, dict) else {})
        obj.id = str(item["id"]) if item.get("id") is not None else None
        obj.type = item["type"] if isinstance(item.get("type"), str) else None
        links = item.get("links")
        obj.links = links if isinstance(links, dict) else {}
        obj.meta = meta or {}
        obj.raw = raw if raw is not None else dict(item)
        return obj

    @classmethod
    def from_response(cls: Type[A], body: Mapping[str, Any]) -> A:  # noqa: UP006
        """Builds the object from a response body with a `data` resource."""
        data = body.get("data")
        meta = body.get("meta")
        return cls.from_item(
            data if isinstance(data, dict) else {},
            meta=meta if isinstance(meta, dict) else {},
            raw=dict(body),
        )

    @classmethod
    def list_from_response(cls: Type[A], body: Mapping[str, Any]) -> ApiList[A]:  # noqa: UP006
        """Builds a list of objects from a response body whose `data` is a list."""
        data = body.get("data")
        raw_meta = body.get("meta")
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        items = (
            [cls.from_item(item, meta=meta) for item in data if isinstance(item, dict)]
            if isinstance(data, list)
            else []
        )
        return ApiList(items, meta=meta)
