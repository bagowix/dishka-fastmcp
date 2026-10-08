"""Mounted servers take the container of the server serving the MCP request."""

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, NewType

import pytest
from dishka import (
    AsyncContainer,
    Container,
    Provider,
    Scope,
    make_async_container,
    make_container,
    provide,
)
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy
from fastmcp.utilities.lifespan import combine_lifespans
from mcp.types import TextContent

from dishka_fastmcp import DishkaFastMCPError, FromDishka, dishka_lifespan, inject, setup_dishka
from dishka_fastmcp._container import get_async_container

Label = NewType('Label', str)


class LabelProvider(Provider):
    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label
        self.finalized = 0

    @provide(scope=Scope.APP)
    def label(self) -> Label:
        return Label(self._label)

    @provide(scope=Scope.REQUEST)
    async def request_marker(self) -> AsyncIterator[int]:
        try:
            yield 1
        finally:
            self.finalized += 1


def labelled_router(name: str = 'router') -> FastMCP:
    router = FastMCP(name)

    @router.tool
    @inject
    async def whoami(label: FromDishka[Label]) -> str:
        return label

    return router


def served_root(container: AsyncContainer | Container, name: str = 'root') -> FastMCP:
    server = FastMCP(name, lifespan=dishka_lifespan(container))
    setup_dishka(container, server)
    return server


async def call_text(client: Client, tool: str) -> str:
    result = await client.call_tool(tool)
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


@pytest.mark.parametrize(
    ('mount_kwargs', 'tool', 'resource', 'template', 'prompt'),
    [
        ({}, 'tool', 'data://router', 'data://item/x', 'prompt'),
        (
            {'namespace': 'ns'},
            'ns_tool',
            'data://ns/router',
            'data://ns/item/x',
            'ns_prompt',
        ),
        (
            {'namespace': 'ns', 'tool_names': {'tool': 'renamed'}},
            'ns_renamed',
            'data://ns/router',
            'data://ns/item/x',
            'ns_prompt',
        ),
    ],
)
@pytest.mark.asyncio
async def test_root_serves_mounted_tool_resource_template_and_prompt(
    mount_kwargs: dict[str, Any],
    tool: str,
    resource: str,
    template: str,
    prompt: str,
) -> None:
    provider = LabelProvider('root')
    server = served_root(make_async_container(provider))
    router = FastMCP('router')

    @router.tool(name='tool')
    @inject
    async def router_tool(label: FromDishka[Label], marker: FromDishka[int]) -> str:
        return f'{label}:{marker}'

    @router.resource('data://router')
    @inject
    async def router_resource(label: FromDishka[Label]) -> str:
        return label

    @router.resource('data://item/{item}')
    @inject
    async def router_template(item: str, label: FromDishka[Label]) -> str:
        return f'{label}:{item}'

    @router.prompt(name='prompt')
    @inject
    async def router_prompt(label: FromDishka[Label]) -> str:
        return label

    server.mount(router, **mount_kwargs)

    async with Client(server) as client:
        tools = await client.list_tools()
        prompts = await client.list_prompts()
        tool_text = await call_text(client, tool)
        resource_result = await client.read_resource(resource)
        template_result = await client.read_resource(template)
        prompt_result = await client.get_prompt(prompt)

    prompt_block = prompt_result.messages[0].content
    assert [(t.name, t.input_schema.get('properties', {})) for t in tools] == [(tool, {})]
    assert [(p.name, p.arguments or []) for p in prompts] == [(prompt, [])]
    assert tool_text == 'root:1'
    assert resource_result[0].text == 'root'
    assert template_result[0].text == 'root:x'
    assert isinstance(prompt_block, TextContent)
    assert prompt_block.text == 'root'
    assert provider.finalized == 1


@pytest.mark.asyncio
async def test_root_serves_two_levels_of_mounting() -> None:
    server = served_root(make_async_container(LabelProvider('root')))
    middle = FastMCP('middle')
    middle.mount(labelled_router('leaf'), namespace='leaf')
    server.mount(middle, namespace='middle')

    async with Client(server) as client:
        assert await call_text(client, 'middle_leaf_whoami') == 'root'


@pytest.mark.asyncio
async def test_router_mounted_while_serving_is_covered() -> None:
    server = served_root(make_async_container(LabelProvider('root')))

    async with Client(server) as client:
        server.mount(labelled_router(), namespace='late')
        assert await call_text(client, 'late_whoami') == 'root'


@pytest.mark.asyncio
async def test_sync_tool_in_mounted_router_resolves_root_container_in_worker_thread() -> None:
    server = served_root(make_container(LabelProvider('root')))
    router = FastMCP('router')
    main_thread = threading.get_ident()

    @router.tool
    @inject
    def whoami(label: FromDishka[Label]) -> str:
        assert threading.get_ident() != main_thread
        return label

    server.mount(router)

    async with Client(server) as client:
        assert await call_text(client, 'whoami') == 'root'


@pytest.mark.asyncio
async def test_mounted_server_with_its_own_setup_keeps_its_container() -> None:
    middle_container = make_async_container(LabelProvider('middle'))
    server = served_root(make_async_container(LabelProvider('root')))
    middle = labelled_router('middle')
    setup_dishka(middle_container, middle)
    middle.mount(labelled_router('leaf'), namespace='leaf')
    server.mount(middle, namespace='middle')

    try:
        async with Client(server) as client:
            assert await call_text(client, 'middle_whoami') == 'middle'
            # A server without a container of its own takes the serving root's.
            assert await call_text(client, 'middle_leaf_whoami') == 'root'
    finally:
        await middle_container.close()


@pytest.mark.asyncio
async def test_mounted_router_with_its_own_dishka_lifespan_keeps_its_container() -> None:
    router_container = make_async_container(LabelProvider('router'))
    router = FastMCP('router', lifespan=dishka_lifespan(router_container))

    @router.tool
    @inject
    async def whoami(label: FromDishka[Label]) -> str:
        return label

    server = served_root(make_async_container(LabelProvider('root')))
    server.mount(router)

    async with Client(server) as client:
        assert await call_text(client, 'whoami') == 'router'


@pytest.mark.asyncio
async def test_router_shared_by_two_roots_uses_the_root_serving_the_request() -> None:
    router = labelled_router()
    first = served_root(make_async_container(LabelProvider('first')), 'first')
    second = served_root(make_async_container(LabelProvider('second')), 'second')
    first.mount(router)
    second.mount(router)

    async with Client(first) as first_client, Client(second) as second_client:
        clients = [first_client, second_client] * 10
        labels = await asyncio.gather(*(call_text(client, 'whoami') for client in clients))

    assert labels == ['first', 'second'] * 10


@pytest.mark.asyncio
async def test_root_without_dishka_lifespan_does_not_borrow_another_roots_container() -> None:
    router = labelled_router()
    served = served_root(make_async_container(LabelProvider('served')), 'served')
    bare = FastMCP('bare')
    served.mount(router)
    bare.mount(router)

    async with Client(served) as served_client, Client(bare) as bare_client:
        assert await call_text(served_client, 'whoami') == 'served'
        with pytest.raises(ToolError, match='dishka_lifespan'):
            await bare_client.call_tool('whoami')


@pytest.mark.asyncio
async def test_direct_call_outside_an_mcp_request_does_not_reach_mounted_routers() -> None:
    container = make_async_container(LabelProvider('root'))
    server = FastMCP('root')
    setup_dishka(container, server)
    server.mount(labelled_router())

    try:
        with pytest.raises(ToolError, match='dishka_lifespan'):
            await server.call_tool('whoami')
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_root_with_opaque_lifespan_state_reports_missing_setup() -> None:
    @asynccontextmanager
    async def opaque_state(_: FastMCP) -> AsyncIterator[object]:
        yield object()

    container = make_async_container(LabelProvider('root'))
    server = FastMCP('root', lifespan=opaque_state)
    setup_dishka(container, server)
    server.mount(labelled_router())

    try:
        async with Client(server) as client:
            with pytest.raises(ToolError, match='dishka_lifespan'):
                await client.call_tool('whoami')
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_dishka_lifespan_combined_with_another_serves_mounted_routers() -> None:
    @asynccontextmanager
    async def database(_: FastMCP) -> AsyncIterator[dict[str, str]]:
        yield {'database': 'pool'}

    container = make_async_container(LabelProvider('root'))
    server = FastMCP('root', lifespan=combine_lifespans(database, dishka_lifespan(container)))
    setup_dishka(container, server)
    server.mount(labelled_router())

    async with Client(server) as client:
        assert await call_text(client, 'whoami') == 'root'


@pytest.mark.asyncio
async def test_proxied_server_needs_its_own_setup() -> None:
    container = make_async_container(LabelProvider('root'))
    server = served_root(container)
    router = labelled_router()
    server.mount(create_proxy(router), namespace='proxy')

    async with Client(server) as client:
        with pytest.raises(ToolError, match='dishka_lifespan'):
            await client.call_tool('proxy_whoami')
        setup_dishka(container, router)
        assert await call_text(client, 'proxy_whoami') == 'root'


@pytest.mark.asyncio
async def test_server_called_from_a_tool_uses_the_container_of_the_request() -> None:
    server = served_root(make_async_container(LabelProvider('root')))
    inner = labelled_router('inner')

    @server.tool
    async def delegate() -> str:
        result = await inner.call_tool('whoami')
        block = result.content[0]
        assert isinstance(block, TextContent)
        return block.text

    async with Client(server) as client:
        assert await call_text(client, 'delegate') == 'root'


@pytest.mark.asyncio
async def test_lookup_from_a_mounted_servers_lifespan_reports_missing_setup() -> None:
    # The root is the active server here, but no operation (and no Context) runs.
    @asynccontextmanager
    async def child_lifespan(_: FastMCP) -> AsyncIterator[dict[str, str]]:
        with pytest.raises(DishkaFastMCPError, match='setup_dishka'):
            get_async_container((), {})
        yield {}

    server = FastMCP('root')
    server.mount(FastMCP('child', lifespan=child_lifespan))

    async with Client(server):
        pass
