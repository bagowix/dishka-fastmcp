"""dishka_lifespan closes the container (and finalizes Scope.APP) on shutdown."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import NewType

import pytest
from dishka import Provider, Scope, make_async_container, make_container, provide
from dishka.exceptions import ExitError
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.applications import Starlette

from dishka_fastmcp import DishkaFastMCPError, dishka_lifespan, setup_dishka
from dishka_fastmcp._container import get_registered_container

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


class FailingCloseProvider(Provider):
    @provide(scope=Scope.APP)
    def resource(self) -> Iterator[AppResource]:
        yield AppResource('R')
        raise RuntimeError('close failed')


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
async def test_lifespan_rejects_container_other_than_the_registered_one() -> None:
    mcp = FastMCP('t')
    registered = make_container()
    other = make_container()
    setup_dishka(registered, mcp)
    lifespan = dishka_lifespan(other)

    try:
        with pytest.raises(DishkaFastMCPError, match='different container'):
            async with lifespan(mcp):
                pass
    finally:
        registered.close()
        other.close()


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


@pytest.mark.asyncio
async def test_lifespan_unregisters_and_allows_a_new_container() -> None:
    mcp = FastMCP('t')
    container = make_container()
    setup_dishka(container, mcp)

    async with dishka_lifespan(container)(mcp):
        assert get_registered_container(mcp) is container

    assert get_registered_container(mcp) is None

    replacement = make_container()
    setup_dishka(replacement, mcp)
    async with dishka_lifespan(replacement)(mcp):
        assert get_registered_container(mcp) is replacement

    assert get_registered_container(mcp) is None


@pytest.mark.asyncio
async def test_lifespan_unregisters_when_container_close_fails() -> None:
    mcp = FastMCP('t')
    container = make_container(FailingCloseProvider())
    with container() as request:
        request.get(AppResource)
    setup_dishka(container, mcp)

    with pytest.raises(ExitError, match='Cleanup context errors'):
        async with dishka_lifespan(container)(mcp):
            pass

    assert get_registered_container(mcp) is None


@pytest.mark.asyncio
async def test_lifespan_works_with_fastmcp_combine_lifespans() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    mcp = FastMCP('t')
    setup_dishka(container, mcp)
    events: list[str] = []

    @asynccontextmanager
    async def application_lifespan(
        app: FastMCP,
    ) -> AsyncIterator[dict[str, bool]]:
        assert app is mcp
        events.append('application-started')
        try:
            yield {'application': True}
        finally:
            events.append(f'application-stopped:container-closed={provider.closed}')

    async with container() as request:
        await request.get(AppResource)

    combined = combine_lifespans(
        application_lifespan,
        dishka_lifespan(container),
    )
    async with combined(mcp) as state:
        assert state == {'application': True}
        assert get_registered_container(mcp) is container
        assert not provider.closed

    assert events == [
        'application-started',
        'application-stopped:container-closed=True',
    ]
    assert get_registered_container(mcp) is None


@pytest.mark.asyncio
async def test_multiple_fastmcp_lifespans_can_be_combined_at_asgi_layer() -> None:
    first_provider = AsyncResourceProvider()
    second_provider = AsyncResourceProvider()
    first_container = make_async_container(first_provider)
    second_container = make_async_container(second_provider)
    first = FastMCP('first', lifespan=dishka_lifespan(first_container))
    second = FastMCP('second', lifespan=dishka_lifespan(second_container))
    setup_dishka(first_container, first)
    setup_dishka(second_container, second)
    first_app = first.http_app()
    second_app = second.http_app()
    asgi_app = Starlette()

    combined = combine_lifespans(first_app.lifespan, second_app.lifespan)
    async with combined(asgi_app):
        async with first_container() as request:
            await request.get(AppResource)
        async with second_container() as request:
            await request.get(AppResource)

        assert get_registered_container(first) is first_container
        assert get_registered_container(second) is second_container
        assert not first_provider.closed
        assert not second_provider.closed

    assert first_provider.closed
    assert second_provider.closed
    assert get_registered_container(first) is None
    assert get_registered_container(second) is None
