"""Pagination: one page, and the way to walk through all of them.

Collections of the API travel with `meta.pagination`. The SDK returns a `Page`,
which behaves as a sequence of its elements and also exposes the counters; to walk
a whole collection there are the `iter_*` iterators of the client, which request
the next page when needed.

https://docs.veriko.mx/en/concepts/pagination
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from ._base import ResponseCarrier

T = TypeVar("T")

DEFAULT_PER_PAGE = 25


@dataclass(frozen=True)
class PageSize:
    """The page size of an endpoint: what it returns without `per_page`, and its cap.

    The API trims a larger `per_page` to `maximum`; it does not reject it.
    """

    default: int
    maximum: int


VALIDATIONS_PAGE = PageSize(default=10, maximum=50)
DELIVERIES_PAGE = PageSize(default=50, maximum=100)
IMPORT_PREVIEW_PAGE = PageSize(default=25, maximum=100)


@dataclass(frozen=True)
class Page(ResponseCarrier, Generic[T]):
    """A page of results, with the counters the API returned."""

    items: Sequence[T]
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE
    total: int = 0
    total_pages: int = 1
    meta: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    _fetch: Callable[[int], Page[T]] | None = field(default=None, repr=False, compare=False)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]

    @property
    def has_next(self) -> bool:
        """`True` when at least one page remains to be requested."""
        return self.page < self.total_pages

    def next_page(self) -> Page[T] | None:
        """Requests the page after this one, with the same filters; `None` on the last."""
        if self._fetch is None or not self.has_next:
            return None
        return self._fetch(self.page + 1)

    @classmethod
    def from_response(
        cls,
        body: dict[str, Any],
        parse: Callable[[dict[str, Any]], T],
        *,
        default_per_page: int = DEFAULT_PER_PAGE,
        fetch: Callable[[int], Page[T]] | None = None,
    ) -> Page[T]:
        data = body.get("data")
        items = (
            [parse(item) for item in data if isinstance(item, dict)]
            if isinstance(data, list)
            else []
        )
        raw_meta = body.get("meta")
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        raw_pagination = meta.get("pagination")
        pagination = raw_pagination if isinstance(raw_pagination, dict) else {}
        total = pagination.get("total")
        return cls(
            items=items,
            page=int(pagination.get("page") or 1),
            per_page=int(pagination.get("per_page") or default_per_page),
            total=int(total if total is not None else len(items)),
            total_pages=int(pagination.get("total_pages") or 1),
            meta=meta,
            raw=body,
            _fetch=fetch,
        )


def iterate_pages(
    fetch: Callable[[int], Page[T]],
    *,
    start_page: int = 1,
    max_pages: int | None = None,
) -> Iterator[T]:
    """Walks the pages one by one and yields their elements.

    It requests the next one only when the previous one ran out, so a consumer that
    stops halfway does not spend extra requests.
    """
    current_page = start_page
    pages_read = 0
    while True:
        page = fetch(current_page)
        yield from page.items
        pages_read += 1
        if max_pages is not None and pages_read >= max_pages:
            return
        if not page.has_next or not page.items:
            return
        current_page += 1


__all__ = [
    "DEFAULT_PER_PAGE",
    "DELIVERIES_PAGE",
    "IMPORT_PREVIEW_PAGE",
    "VALIDATIONS_PAGE",
    "Page",
    "PageSize",
    "iterate_pages",
]
