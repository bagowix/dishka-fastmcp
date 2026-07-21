"""Phase 2: injection into resources and prompts, and FastMCPProvider context."""

from collections.abc import Iterator
from typing import NewType

import pytest
from dishka import Provider, Scope, make_async_container, make_container, provide
from fastmcp import FastMCP
from fastmcp.server.context import Context
from mcp.types import ReadResourceRequestParams, TextContent

from dishka_fastmcp import FastMCPProvider, FromDishka, inject, setup_dishka

AppDep = NewType('AppDep', str)
ReqDep = NewType('ReqDep', str)


class AppProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.req_released = False

    @provide(scope=Scope.APP)
    def app_dep(self) -> AppDep:
        return AppDep('APP')

    @provide(scope=Scope.REQUEST)
    def req_dep(self) -> Iterator[ReqDep]:
        yield ReqDep('REQ')
        self.req_released = True


@pytest.mark.asyncio
async def test_async_resource_injects_dep_and_uri_params() -> None:
    provider = AppProvider()
    container = make_async_container(provider, FastMCPProvider())
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.resource('data://items/{name}')
    @inject
    async def item(
        name: str,
        app: FromDishka[AppDep],
        req: FromDishka[ReqDep],
        params: FromDishka[ReadResourceRequestParams],
    ) -> str:
        return f'{app}:{req}:{name}:{params.uri}'

    try:
        templates = await mcp.list_resource_templates()
        assert templates[0].uri_template == 'data://items/{name}'

        result = await mcp.read_resource('data://items/one')
        assert result.contents[0].content == 'APP:REQ:one:data://items/one'
        assert provider.req_released
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_async_prompt_injects_dep() -> None:
    provider = AppProvider()
    container = make_async_container(provider, FastMCPProvider())
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.prompt
    @inject
    async def greet(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    try:
        prompts = await mcp.list_prompts()
        assert [argument.name for argument in prompts[0].arguments or []] == ['name']

        result = await mcp.render_prompt('greet', {'name': 'two'})
        message = result.messages[0].content
        assert isinstance(message, TextContent)
        assert message.text == 'APP:two'
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_tool_injects_fastmcp_context() -> None:
    container = make_async_container(AppProvider(), FastMCPProvider())
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def whoami(ctx: FromDishka[Context]) -> str:
        return type(ctx).__name__

    try:
        result = await mcp.call_tool('whoami')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'Context'
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_tool_injects_fastmcp_server() -> None:
    container = make_async_container(AppProvider(), FastMCPProvider())
    mcp = FastMCP('named-server')
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def server_name(app: FromDishka[FastMCP]) -> str:
        return app.name

    try:
        result = await mcp.call_tool('server_name')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'named-server'
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_sync_resource_injects_from_sync_container() -> None:
    provider = AppProvider()
    container = make_container(provider, FastMCPProvider())
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.resource('data://sync/{name}')
    @inject
    def item(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    try:
        result = await mcp.read_resource('data://sync/one')
        assert result.contents[0].content == 'APP:one'
    finally:
        container.close()
