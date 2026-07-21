"""Static assertions for the public typing surface.

Checked by mypy and pyright in strict mode (see ``pyproject.toml``); pytest does
not collect this module. If the public types regress, these assignments fail.
"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from dishka import AsyncContainer, Container, make_async_container, make_container
from fastmcp import FastMCP

from dishka_fastmcp import FromDishka, dishka_lifespan, inject, setup_dishka


class Service:
    def value(self) -> int:
        return 1


async def async_handler(name: str, service: FromDishka[Service]) -> str:
    return f'{name}:{service.value()}'


def sync_handler(name: str, service: FromDishka[Service]) -> str:
    return f'{name}:{service.value()}'


# @inject preserves the wrapped signature and its sync/async nature.
injected_async: Callable[[str, Service], Awaitable[str]] = inject(async_handler)
injected_sync: Callable[[str, Service], str] = inject(sync_handler)

async_container: AsyncContainer = make_async_container()
sync_container: Container = make_container()
server: FastMCP[Any] = FastMCP('typing-surface')

# setup_dishka accepts either container kind.
setup_dishka(async_container, server)
setup_dishka(sync_container, server)

# dishka_lifespan produces something FastMCP accepts as a lifespan.
async_lifespan: Callable[[FastMCP[Any]], AbstractAsyncContextManager[None]] = dishka_lifespan(
    async_container,
)
sync_lifespan: Callable[[FastMCP[Any]], AbstractAsyncContextManager[None]] = dishka_lifespan(
    sync_container,
)
lifespan_server: FastMCP[Any] = FastMCP('typed', lifespan=dishka_lifespan(async_container))
