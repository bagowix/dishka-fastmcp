"""DishkaMiddleware(session_scope=True) keeps one SESSION scope per MCP connection."""

import asyncio
import logging
from collections.abc import AsyncIterator
from itertools import count
from typing import Any, Literal

import pytest
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import asgi_server
from mcp.types import TextContent

from dishka_fastmcp import (
    DishkaMiddleware,
    FastMCPProvider,
    FromDishka,
    dishka_lifespan,
    get_request_container,
    inject,
    setup_dishka,
)


class Pool:
    """An APP-scoped object that outlives every session."""


class Upstream:
    """A SESSION-scoped object whose number tells connections apart."""

    def __init__(self, number: int) -> None:
        self.number = number


class Call:
    """A REQUEST-scoped object built on the session's Upstream."""

    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream


class SessionProvider(Provider):
    def __init__(
        self,
        *,
        first: int = 1,
        fail_on_close: bool = False,
        open_delay: float = 0,
    ) -> None:
        super().__init__()
        self._numbers = count(first)
        self._fail_on_close = fail_on_close
        self._open_delay = open_delay
        self.events: list[str] = []
        self.upstream_closed = asyncio.Event()

    @provide(scope=Scope.APP)
    async def pool(self) -> AsyncIterator[Pool]:
        yield Pool()
        self.events.append('pool closed')

    @provide(scope=Scope.SESSION)
    async def upstream(self, pool: Pool) -> AsyncIterator[Upstream]:
        del pool
        await asyncio.sleep(self._open_delay)  # lets concurrent requests race to open it
        upstream = Upstream(next(self._numbers))
        yield upstream
        self.events.append(f'upstream {upstream.number} closed')
        self.upstream_closed.set()
        if self._fail_on_close:
            raise RuntimeError(f'upstream {upstream.number} failed to close')

    call = provide(Call, scope=Scope.REQUEST)

    @property
    def closed(self) -> list[int]:
        return [int(event.split()[1]) for event in self.events if event.startswith('upstream')]


def served(provider: SessionProvider, *, session_scope: bool = True) -> FastMCP:
    container = make_async_container(provider, FastMCPProvider())
    server = FastMCP(
        'root',
        lifespan=dishka_lifespan(container),
        middleware=[DishkaMiddleware(session_scope=session_scope)],
    )

    @server.tool
    @inject
    async def which(call: FromDishka[Call]) -> str:
        return f'{call.upstream.number}:{id(call)}'

    return server


async def which(client: Client) -> tuple[int, int]:
    result = await client.call_tool('which')
    block = result.content[0]
    assert isinstance(block, TextContent)
    upstream, call = block.text.split(':')
    return int(upstream), int(call)


@pytest.mark.asyncio
async def test_legacy_connection_keeps_one_session_until_it_closes() -> None:
    provider = SessionProvider()
    server = served(provider)

    async with Client(server, mode='legacy') as client:
        seen = [await which(client) for _ in range(3)]
        closed_while_connected = list(provider.closed)

    assert [upstream for upstream, _ in seen] == [1, 1, 1]
    assert len({call for _, call in seen}) == 3
    assert closed_while_connected == []
    assert provider.events == ['upstream 1 closed', 'pool closed']


@pytest.mark.asyncio
async def test_modern_connection_lives_one_request_and_so_does_its_session() -> None:
    provider = SessionProvider()
    server = served(provider)

    async with Client(server, mode='auto') as client:
        seen = []
        for _ in range(3):
            seen.append(await which(client))
            assert provider.closed == [upstream for upstream, _ in seen]

    assert [upstream for upstream, _ in seen] == [1, 2, 3]


@pytest.mark.asyncio
async def test_session_dependencies_live_one_request_without_session_scope() -> None:
    provider = SessionProvider()
    server = served(provider, session_scope=False)

    async with Client(server, mode='legacy') as client:
        seen = [await which(client) for _ in range(2)]
        closed_while_connected = list(provider.closed)

    assert [upstream for upstream, _ in seen] == [1, 2]
    assert closed_while_connected == [1, 2]


@pytest.mark.asyncio
async def test_concurrent_requests_on_one_connection_open_one_session() -> None:
    provider = SessionProvider(open_delay=0.05)
    server = served(provider)

    async with Client(server, mode='legacy') as client:
        seen = await asyncio.wait_for(
            asyncio.gather(*(which(client) for _ in range(4))),
            timeout=5,
        )

    assert {upstream for upstream, _ in seen} == {1}
    assert provider.closed == [1]


class Nested:
    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream


class NestedProvider(SessionProvider):
    @provide(scope=Scope.SESSION)
    async def nested(self, container: AsyncContainer) -> Nested:
        return Nested(await container.get(Upstream))


@pytest.mark.asyncio
async def test_session_factory_may_resolve_through_its_own_container() -> None:
    provider = NestedProvider()
    container = make_async_container(provider)
    server = FastMCP(
        'root',
        lifespan=dishka_lifespan(container),
        middleware=[DishkaMiddleware(session_scope=True)],
    )

    @server.tool
    @inject
    async def nested(value: FromDishka[Nested], upstream: FromDishka[Upstream]) -> bool:
        return value.upstream is upstream

    async with Client(server, mode='legacy') as client:
        result = await asyncio.wait_for(client.call_tool('nested'), timeout=5)

    assert result.data is True


@pytest.mark.asyncio
async def test_each_connection_gets_a_session_of_its_own() -> None:
    provider = SessionProvider()
    server = served(provider)

    async with Client(server, mode='legacy') as first, Client(server, mode='legacy') as second:
        seen = [await which(first), await which(second), await which(first)]

    assert [upstream for upstream, _ in seen] == [1, 2, 1]
    assert sorted(provider.closed) == [1, 2]


@pytest.mark.asyncio
async def test_mounted_server_and_middleware_container_share_the_session() -> None:
    provider = SessionProvider()
    server = served(provider)
    router = FastMCP('router')
    seen: dict[str, Upstream] = {}

    @router.tool
    @inject
    async def mounted(upstream: FromDishka[Upstream]) -> int:
        seen['container'] = await get_request_container().get(Upstream)
        return upstream.number

    server.mount(router, namespace='router')

    async with Client(server, mode='legacy') as client:
        root_upstream, _ = await which(client)
        mounted_result = await client.call_tool('router_mounted')

    assert mounted_result.data == root_upstream == 1
    assert seen['container'].number == 1
    assert provider.closed == [1]


@pytest.mark.asyncio
async def test_mounted_servers_with_containers_of_their_own_keep_sessions_apart() -> None:
    root = FastMCP('root')
    providers = {'a': SessionProvider(first=1), 'b': SessionProvider(first=101)}
    containers = []
    for name, provider in providers.items():
        container = make_async_container(provider)
        containers.append(container)
        router = FastMCP(name, middleware=[DishkaMiddleware(session_scope=True)])
        setup_dishka(container, router)

        @router.tool
        @inject
        async def which(call: FromDishka[Call]) -> int:
            return call.upstream.number

        root.mount(router, namespace=name)

    try:
        async with Client(root, mode='legacy') as client:
            seen = [(await client.call_tool(f'{name}_which')).data for name in 'abab']
    finally:
        for container in containers:
            await container.close()

    assert seen == [1, 101, 1, 101]
    assert providers['a'].closed == [1]
    assert providers['b'].closed == [101]


@pytest.mark.asyncio
async def test_stateful_http_session_lives_until_the_client_ends_it() -> None:
    provider = SessionProvider()
    server = served(provider)

    async with asgi_server(server) as running:
        async with running.client(mode='legacy') as client:
            seen = [await which(client) for _ in range(3)]
            closed_while_connected = list(provider.closed)
        # The client's DELETE ends the session; a server task then closes it.
        await asyncio.wait_for(provider.upstream_closed.wait(), timeout=5)

    assert [upstream for upstream, _ in seen] == [1, 1, 1]
    assert closed_while_connected == []
    assert provider.events == ['upstream 1 closed', 'pool closed']


@pytest.mark.asyncio
async def test_sse_connection_keeps_one_session_under_the_default_client() -> None:
    provider = SessionProvider()
    server = served(provider)

    async with asgi_server(server, transport='sse') as running:
        async with running.client() as client:
            seen = [await which(client) for _ in range(3)]
            closed_while_connected = list(provider.closed)
        await asyncio.wait_for(provider.upstream_closed.wait(), timeout=5)

    assert [upstream for upstream, _ in seen] == [1, 1, 1]
    assert closed_while_connected == []


@pytest.mark.parametrize(
    ('stateless_http', 'mode'),
    [(False, 'auto'), (True, 'legacy'), (True, 'auto')],
)
@pytest.mark.asyncio
async def test_single_exchange_http_gets_a_session_per_request(
    stateless_http: bool,  # noqa: FBT001
    mode: Literal['auto', 'legacy'],
) -> None:
    provider = SessionProvider()
    server = served(provider)

    async with (
        asgi_server(server, stateless_http=stateless_http) as running,
        running.client(mode=mode) as client,
    ):
        seen = [await which(client) for _ in range(3)]

    assert [upstream for upstream, _ in seen] == [1, 2, 3]
    assert sorted(provider.closed) == [1, 2, 3]


@pytest.mark.parametrize('mode', ['auto', 'legacy'])
@pytest.mark.asyncio
async def test_session_finalizer_errors_are_logged_by_the_sdk_not_raised(
    mode: Literal['auto', 'legacy'],
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = SessionProvider(fail_on_close=True)
    server = served(provider)
    seen: list[Any] = []

    with caplog.at_level(logging.ERROR, logger='mcp.server.runner'):
        async with Client(server, mode=mode) as client:
            seen.append(await which(client))

    assert provider.closed == [1]
    assert [record.getMessage() for record in caplog.records] == [
        'connection exit_stack cleanup raised',
    ]
