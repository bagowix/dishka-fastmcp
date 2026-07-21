"""Phase 1 vertical slice: dependency injection into MCP tools via on_call_tool."""

import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import NewType

import pytest
from dishka import (
    Provider,
    Scope,
    make_async_container,
    make_container,
    provide,
)
from fastmcp import FastMCP
from fastmcp.server.context import Context
from mcp.types import TextContent

from dishka_fastmcp import FromDishka, inject, setup_dishka
from dishka_fastmcp._middleware import DishkaMiddleware

AppDep = NewType('AppDep', str)
ReqDep = NewType('ReqDep', str)


class AppProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.req_released = threading.Event()

    @provide(scope=Scope.APP)
    def app_dep(self) -> AppDep:
        return AppDep('APP')

    @provide(scope=Scope.REQUEST)
    def req_dep(self) -> Iterator[ReqDep]:
        yield ReqDep('REQ')
        self.req_released.set()


@pytest.mark.asyncio
async def test_async_tool_injects_and_hides_deps_from_schema() -> None:
    provider = AppProvider()
    container = make_async_container(provider)
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def greet(
        name: str,
        app: FromDishka[AppDep],
        req: FromDishka[ReqDep],
    ) -> str:
        return f'{app}:{req}:{name}'

    try:
        tools = await mcp.list_tools()
        assert tools[0].parameters['properties'].keys() == {'name'}

        result = await mcp.call_tool('greet', {'name': 'x'})
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'APP:REQ:x'
        assert provider.req_released.is_set()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_sync_tool_run_in_thread_propagates_container() -> None:
    provider = AppProvider()
    container = make_container(provider)
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    main_thread = threading.get_ident()
    seen: dict[str, int] = {}

    # Sync tools run in a worker thread by default; we do not pass the
    # run_in_thread kwarg (added after fastmcp 3.2.4) so the floor stays low.
    @mcp.tool
    @inject
    def work(app: FromDishka[AppDep], req: FromDishka[ReqDep]) -> str:
        seen['thread'] = threading.get_ident()
        return f'{app}:{req}'

    try:
        result = await mcp.call_tool('work')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'APP:REQ'
        assert seen['thread'] != main_thread
        assert provider.req_released.is_set()
    finally:
        container.close()


@pytest.mark.asyncio
async def test_missing_setup_dishka_raises_clear_error() -> None:
    mcp = FastMCP('test')

    @mcp.tool
    @inject
    async def work(req: FromDishka[ReqDep]) -> None:
        del req

    with pytest.raises(Exception, match='setup_dishka'):
        await mcp.call_tool('work')


@pytest.mark.asyncio
async def test_async_tool_with_sync_container_raises() -> None:
    container = make_container(AppProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def work(app: FromDishka[AppDep]) -> None:
        del app

    try:
        with pytest.raises(Exception, match='needs an AsyncContainer'):
            await mcp.call_tool('work')
    finally:
        container.close()


@pytest.mark.asyncio
async def test_sync_tool_with_async_container_raises() -> None:
    container = make_async_container(AppProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    def work(app: FromDishka[AppDep]) -> None:
        del app

    try:
        with pytest.raises(Exception, match='needs a Container'):
            await mcp.call_tool('work')
    finally:
        await container.close()


def test_context_data_omits_context_without_fastmcp_context() -> None:
    message = object()
    context = SimpleNamespace(message=message, fastmcp_context=None)
    data = DishkaMiddleware._context_data(context)  # type: ignore[arg-type]
    assert data == {type(message): message}
    assert Context not in data
    assert FastMCP not in data
