"""dishka_lifespan closes the container (and finalizes Scope.APP) on shutdown."""

from collections.abc import AsyncIterator, Iterator
from typing import NewType

import pytest
from dishka import Provider, Scope, make_async_container, make_container, provide
from fastmcp import FastMCP

from dishka_fastmcp import dishka_lifespan

AppResource = NewType('AppResource', str)


class AsyncResourceProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    @provide(scope=Scope.APP)
    async def resource(self) -> AsyncIterator[AppResource]:
        yield AppResource('R')
        self.closed = True


class SyncResourceProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    @provide(scope=Scope.APP)
    def resource(self) -> Iterator[AppResource]:
        yield AppResource('R')
        self.closed = True


@pytest.mark.asyncio
async def test_lifespan_closes_async_container() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    async with container() as request:
        await request.get(AppResource)

    lifespan = dishka_lifespan(container)
    async with lifespan(FastMCP('t')):
        assert not provider.closed

    assert provider.closed


@pytest.mark.asyncio
async def test_lifespan_closes_sync_container() -> None:
    provider = SyncResourceProvider()
    container = make_container(provider)
    with container() as request:
        request.get(AppResource)

    lifespan = dishka_lifespan(container)
    async with lifespan(FastMCP('t')):
        assert not provider.closed

    assert provider.closed
