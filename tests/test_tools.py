"""Dependency injection into MCP tools."""

import functools
import inspect
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import NewType, ParamSpec, TypeVar, get_type_hints

import pytest
from dishka import (
    Provider,
    Scope,
    make_async_container,
    make_container,
    provide,
)
from fastmcp import Client, FastMCP
from fastmcp.tools import tool
from mcp.types import TextContent

from dishka_fastmcp import FromDishka, inject, setup_dishka
from dishka_fastmcp._container import get_async_container
from dishka_fastmcp.exceptions import DishkaFastMCPError

AppDep = NewType('AppDep', str)
ReqDep = NewType('ReqDep', str)

P = ParamSpec('P')
T = TypeVar('T')


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


def test_container_lookup_outside_fastmcp_request_raises_clear_error() -> None:
    with pytest.raises(DishkaFastMCPError, match='setup_dishka'):
        get_async_container((), {})


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

    # Sync tools run in a worker thread by default (run_in_thread=True).
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


@pytest.mark.parametrize('decorator', ['tool', 'prompt', 'resource', 'standalone tool'])
def test_inject_above_the_fastmcp_decorator_raises(decorator: str) -> None:
    # A server decorator has registered the bare function by then: the
    # dependency is in its schema, and the client fills it in.
    mcp = FastMCP('test')
    register = {
        'tool': mcp.tool,
        'prompt': mcp.prompt,
        'resource': mcp.resource('data://{app}'),
        'standalone tool': tool,
    }[decorator]

    async def handler(app: FromDishka[AppDep]) -> str:
        return app

    registered = register(handler)

    with pytest.raises(DishkaFastMCPError, match=r'@inject must sit below'):
        inject(registered)


@pytest.mark.asyncio
async def test_standalone_decorator_above_inject_registers_the_injected_function() -> None:
    container = make_async_container(AppProvider())
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @tool
    @inject
    async def greet(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    mcp.add_tool(greet)

    try:
        tools = await mcp.list_tools()
        assert tools[0].parameters['properties'].keys() == {'name'}
        result = await mcp.call_tool('greet', {'name': 'x'})
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'APP:x'
    finally:
        await container.close()


def map_errors(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    # A typical user decorator above @inject: error mapping, metrics, retries.
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return await func(*args, **kwargs)

    return wrapper


@pytest.mark.asyncio
async def test_plain_wraps_decorator_above_inject_registers_and_runs() -> None:
    # Since Python 3.14 wraps copies __annotate__ instead of __annotations__, and
    # FastMCP failed to build this schema with KeyError: 'name'.
    container = make_async_container(AppProvider())
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.tool
    @map_errors
    @inject
    async def greet(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    try:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert tools[0].input_schema['properties'].keys() == {'name'}
            result = await client.call_tool('greet', {'name': 'x'})
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'APP:x'
    finally:
        await container.close()


@pytest.mark.parametrize('kind', ['async', 'sync', 'async generator', 'generator'])
def test_plain_wraps_above_inject_keeps_the_cleaned_annotations(kind: str) -> None:
    async def async_handler(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    def sync_handler(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    async def async_stream(name: str, app: FromDishka[AppDep]) -> AsyncIterator[str]:
        yield f'{app}:{name}'

    def stream(name: str, app: FromDishka[AppDep]) -> Iterator[str]:
        yield f'{app}:{name}'

    handler = {
        'async': async_handler,
        'sync': sync_handler,
        'async generator': async_stream,
        'generator': stream,
    }[kind]
    outer = functools.wraps(inject(handler))(lambda: None)

    expected = get_type_hints(handler)
    del expected['app']
    assert get_type_hints(outer) == expected


@pytest.mark.parametrize('kind', ['async', 'sync'])
def test_inject_reads_annotations_a_decorator_below_assigned_directly(kind: str) -> None:
    # Assigning __annotations__ sets __annotate__ to None, as dishka's own
    # wrap_injection does, and since Python 3.14 wraps copies only __annotate__.
    async def async_handler(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    def sync_handler(name: str, app: FromDishka[AppDep]) -> str:
        return f'{app}:{name}'

    handler = {'async': async_handler, 'sync': sync_handler}[kind]
    handler.__annotations__ = dict(handler.__annotations__)

    assert inspect.signature(inject(handler)).parameters.keys() == {'name'}
