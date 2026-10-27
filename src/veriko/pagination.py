"""Paginación: una página, y la forma de recorrerlas todas.

Las colecciones de la API viajan con `meta.pagination`. El SDK devuelve una
`Page`, que se comporta como una secuencia de sus elementos y además expone los
contadores; para recorrer una colección entera están los iteradores `iter_*` del
cliente, que piden la página siguiente cuando hace falta.

https://docs.veriko.mx/es/concepts/pagination
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")

DEFAULT_PER_PAGE = 25


@dataclass(frozen=True)
class Page(Generic[T]):
    """Una página de resultados, con los contadores que devolvió la API."""

    items: Sequence[T]
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE
    total: int = 0
    total_pages: int = 1
    meta: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]

    @property
    def has_next(self) -> bool:
        """`True` cuando queda al menos una página por pedir."""
        return self.page < self.total_pages

    @classmethod
    def from_response(
        cls,
        body: dict[str, Any],
        parse: Callable[[dict[str, Any]], T],
    ) -> Page[T]:
        datos = body.get("data")
        elementos = (
            [parse(item) for item in datos if isinstance(item, dict)]
            if isinstance(datos, list)
            else []
        )
        meta_bruta = body.get("meta")
        meta = meta_bruta if isinstance(meta_bruta, dict) else {}
        paginacion_bruta = meta.get("pagination")
        paginacion = paginacion_bruta if isinstance(paginacion_bruta, dict) else {}
        total = paginacion.get("total")
        return cls(
            items=elementos,
            page=int(paginacion.get("page") or 1),
            per_page=int(paginacion.get("per_page") or len(elementos) or DEFAULT_PER_PAGE),
            total=int(total if total is not None else len(elementos)),
            total_pages=int(paginacion.get("total_pages") or 1),
            meta=meta,
            raw=body,
        )


def iterate_pages(
    fetch: Callable[[int], Page[T]],
    *,
    start_page: int = 1,
    max_pages: int | None = None,
) -> Iterator[T]:
    """Recorre las páginas una a una y va entregando sus elementos.

    Pide la siguiente sólo cuando la anterior se agotó, de modo que un consumidor
    que corta a la mitad no gasta peticiones de más.
    """
    pagina_actual = start_page
    paginas_leidas = 0
    while True:
        pagina = fetch(pagina_actual)
        yield from pagina.items
        paginas_leidas += 1
        if max_pages is not None and paginas_leidas >= max_pages:
            return
        if not pagina.has_next or not pagina.items:
            return
        pagina_actual += 1


__all__ = ["DEFAULT_PER_PAGE", "Page", "iterate_pages"]
