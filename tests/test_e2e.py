"""End-to-end MCP protocol coverage through FastMCP's in-memory Client."""

from collections.abc import AsyncIterator
from typing import NewType

import pytest
from dishka import Provider, Scope, make_async_container, provide
from fastmcp import Client, Context, FastMCP
from mcp.types import TextContent

from dishka_fastmcp import (
    FastMCPProvider,
    FromDishka,
    dishka_lifespan,
    inject,
    setup_dishka,
)

AppValue = NewType('AppValue', str)
RequestValue = NewType('RequestValue', str)


class EndToEndProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.app_closed = False
        self.request_finalized = 0

    @provide(scope=Scope.APP)
    async def app_value(self) -> AsyncIterator[AppValue]:
        try:
            yield AppValue('app')
        finally:
            self.app_closed = True

    @provide(scope=Scope.REQUEST)
    async def request_value(self) -> AsyncIterator[RequestValue]:
        try:
            yield RequestValue('request')
        finally:
            self.request_finalized += 1


@pytest.mark.asyncio
async def test_client_protocol_runs_injection_and_finalizes_all_scopes() -> None:
    provider = EndToEndProvider()
    container = make_async_container(provider, FastMCPProvider())
    mcp = FastMCP('end-to-end', lifespan=dishka_lifespan(container))
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def tool(
        value: str,
        app: FromDishka[AppValue],
        request: FromDishka[RequestValue],
        context: FromDishka[Context],
    ) -> str:
        return f'{app}:{request}:{context.fastmcp.name}:{value}'

    @mcp.resource('data://end-to-end')
    @inject
    async def resource(
        app: FromDishka[AppValue],
        request: FromDishka[RequestValue],
    ) -> str:
        return f'{app}:{request}'

    @mcp.prompt
    @inject
    async def prompt(
        app: FromDishka[AppValue],
        request: FromDishka[RequestValue],
    ) -> str:
        return f'{app}:{request}'

    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert tools[0].inputSchema['properties'].keys() == {'value'}

        tool_result = await client.call_tool('tool', {'value': 'value'})
        resource_result = await client.read_resource('data://end-to-end')
        prompt_result = await client.get_prompt('prompt')

        tool_block = tool_result.content[0]
        prompt_block = prompt_result.messages[0].content
        assert isinstance(tool_block, TextContent)
        assert isinstance(prompt_block, TextContent)
        assert tool_block.text == 'app:request:end-to-end:value'
        assert resource_result[0].text == 'app:request'
        assert prompt_block.text == 'app:request'
        assert provider.request_finalized == 3
        assert not provider.app_closed

    assert provider.app_closed
