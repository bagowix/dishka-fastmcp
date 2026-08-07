"""Context ownership, isolation, and request-scope finalization."""

import asyncio
import gc
import weakref
from collections.abc import AsyncIterator, Generator, Iterator
from types import coroutine
from typing import NewType

import pytest
from dishka import Provider, Scope, make_async_container, make_container, provide
from fastmcp import Context, FastMCP
from mcp.types import TextContent

from dishka_fastmcp import FastMCPProvider, FromDishka, inject, setup_dishka
from dishka_fastmcp._container import get_async_container
from dishka_fastmcp.exceptions import DishkaFastMCPError

Label = NewType('Label', str)
RequestResource = NewType('RequestResource', str)


class LabelProvider(Provider):
    def __init__(self, label: Label) -> None:
        super().__init__()
        self._label = label

    @provide(scope=Scope.APP)
    def label(self) -> Label:
        return self._label


class AsyncResourceProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.released = asyncio.Event()

    @provide(scope=Scope.REQUEST)
    async def resource(self) -> AsyncIterator[RequestResource]:
        try:
            yield RequestResource('resource')
        finally:
            self.released.set()


class SyncResourceProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.released = False

    @provide(scope=Scope.REQUEST)
    def resource(self) -> Iterator[RequestResource]:
        try:
            yield RequestResource('resource')
        finally:
            self.released = True


@pytest.mark.asyncio
async def test_request_scope_finalizes_when_async_handler_raises() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def fail(resource: FromDishka[RequestResource]) -> None:
        del resource
        raise RuntimeError('expected failure')

    try:
        with pytest.raises(Exception, match='expected failure'):
            await mcp.call_tool('fail')
        assert provider.released.is_set()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_request_scope_finalizes_when_async_handler_is_cancelled() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    mcp = FastMCP('test')
    setup_dishka(container, mcp)
    entered = asyncio.Event()
    never = asyncio.Event()

    @mcp.tool
    @inject
    async def wait(resource: FromDishka[RequestResource]) -> None:
        del resource
        entered.set()
        await never.wait()

    try:
        task = asyncio.create_task(mcp.call_tool('wait'))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.released.is_set()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_request_scope_finalizes_when_sync_handler_raises() -> None:
    provider = SyncResourceProvider()
    container = make_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    def fail(resource: FromDishka[RequestResource]) -> None:
        del resource
        raise RuntimeError('expected failure')

    try:
        with pytest.raises(Exception, match='expected failure'):
            await mcp.call_tool('fail')
        assert provider.released
    finally:
        container.close()


@pytest.mark.asyncio
async def test_sync_handler_returning_awaitable_is_rejected() -> None:
    provider = SyncResourceProvider()
    container = make_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    def handler(resource: FromDishka[RequestResource]) -> str:
        async def finish() -> str:
            return resource

        return finish()  # type: ignore[return-value]

    try:
        with pytest.raises(Exception, match='returned an awaitable'):
            await mcp.call_tool('handler')
        assert provider.released
    finally:
        container.close()


@pytest.mark.asyncio
async def test_sync_handler_returning_non_coroutine_awaitable_is_rejected() -> None:
    class DeferredResult:
        def __init__(self, resource: RequestResource) -> None:
            self.resource = resource

        def __await__(self) -> Iterator[None]:
            yield

    container = make_container(SyncResourceProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)
    result_ref: weakref.ReferenceType[DeferredResult] | None = None

    @mcp.tool
    @inject
    def handler(resource: FromDishka[RequestResource]) -> str:
        nonlocal result_ref
        result = DeferredResult(resource)
        result_ref = weakref.ref(result)
        return result  # type: ignore[return-value]

    try:
        with pytest.raises(Exception, match='returned an awaitable'):
            await mcp.call_tool('handler')
        gc.collect()
        assert result_ref is not None
        assert result_ref() is None
    finally:
        container.close()


@pytest.mark.asyncio
async def test_sync_handler_closes_generator_based_coroutine_before_scope_closes() -> None:
    provider = SyncResourceProvider()
    container = make_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)
    alive_when_closed: list[bool] = []

    @mcp.tool
    @inject
    def handler(resource: FromDishka[RequestResource]) -> object:
        @coroutine
        def deferred() -> Generator[None, None, RequestResource]:
            try:
                yield
            finally:
                alive_when_closed.append(not provider.released)
                str(resource)

        result = deferred()
        result.send(None)
        return result

    try:
        with pytest.raises(Exception, match='returned an awaitable'):
            await mcp.call_tool('handler')
        assert alive_when_closed == [True]
        assert provider.released
    finally:
        container.close()


@pytest.mark.asyncio
async def test_sync_generator_handler_keeps_request_scope_open_during_iteration() -> None:
    provider = SyncResourceProvider()
    container = make_container(provider)
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    def handler(resource: FromDishka[RequestResource]) -> Iterator[str]:
        yield f'{resource}:{provider.released}'

    try:
        result = await mcp.call_tool('handler')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == '["resource:False"]'
        assert provider.released
    finally:
        container.close()


@pytest.mark.asyncio
async def test_async_generator_handler_keeps_request_scope_open_during_iteration() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    mcp = FastMCP('test')
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def handler(resource: FromDishka[RequestResource]) -> AsyncIterator[str]:
        yield f'{resource}:{provider.released.is_set()}'

    try:
        result = await mcp.call_tool('handler')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == '["resource:False"]'
        assert provider.released.is_set()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_sync_handler_returning_generator_is_rejected() -> None:
    container = make_container(SyncResourceProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    def handler(resource: FromDishka[RequestResource]) -> object:
        def deferred() -> Iterator[RequestResource]:
            yield resource

        return deferred()

    try:
        with pytest.raises(Exception, match='returned a generator'):
            await mcp.call_tool('handler')
    finally:
        container.close()


@pytest.mark.asyncio
async def test_sync_handler_returning_async_generator_is_rejected_and_closed() -> None:
    container = make_container(SyncResourceProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)
    returned: list[AsyncIterator[RequestResource]] = []

    @mcp.tool
    @inject
    def handler(resource: FromDishka[RequestResource]) -> object:
        async def deferred() -> AsyncIterator[RequestResource]:
            yield resource

        generator = deferred()
        returned.append(generator)
        return generator

    try:
        with pytest.raises(Exception, match='returned an async generator'):
            await mcp.call_tool('handler')
        with pytest.raises(StopAsyncIteration):
            await anext(returned[0])
    finally:
        container.close()


@pytest.mark.asyncio
async def test_async_handler_returning_awaitable_is_rejected() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def handler(resource: FromDishka[RequestResource]) -> object:
        async def finish() -> RequestResource:
            return resource

        return finish()

    try:
        with pytest.raises(Exception, match='returned an awaitable'):
            await mcp.call_tool('handler')
        assert provider.released.is_set()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_async_handler_returning_non_coroutine_awaitable_is_rejected() -> None:
    class DeferredResult:
        def __init__(self, resource: RequestResource) -> None:
            self.resource = resource

        def __await__(self) -> Iterator[None]:
            yield

    container = make_async_container(AsyncResourceProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)
    result_ref: weakref.ReferenceType[DeferredResult] | None = None

    @mcp.tool
    @inject
    async def handler(resource: FromDishka[RequestResource]) -> object:
        nonlocal result_ref
        result = DeferredResult(resource)
        result_ref = weakref.ref(result)
        return result

    try:
        with pytest.raises(Exception, match='returned an awaitable'):
            await mcp.call_tool('handler')
        gc.collect()
        assert result_ref is not None
        assert result_ref() is None
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_async_handler_closes_generator_based_coroutine_before_scope_closes() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)
    alive_when_closed: list[bool] = []

    @mcp.tool
    @inject
    async def handler(resource: FromDishka[RequestResource]) -> object:
        @coroutine
        def deferred() -> Generator[None, None, RequestResource]:
            try:
                yield
            finally:
                alive_when_closed.append(not provider.released.is_set())
                str(resource)

        result = deferred()
        result.send(None)
        return result

    try:
        with pytest.raises(Exception, match='returned an awaitable'):
            await mcp.call_tool('handler')
        assert alive_when_closed == [True]
        assert provider.released.is_set()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_async_handler_returning_task_cancels_it_before_scope_closes() -> None:
    provider = AsyncResourceProvider()
    container = make_async_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)
    task_stopped = asyncio.Event()
    alive_when_stopped: list[bool] = []

    @mcp.tool
    @inject
    async def handler(resource: FromDishka[RequestResource]) -> object:
        async def deferred() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                alive_when_stopped.append(not provider.released.is_set())
                str(resource)
                task_stopped.set()

        task = asyncio.create_task(deferred())
        await asyncio.sleep(0)
        return task

    try:
        with pytest.raises(Exception, match='returned an awaitable'):
            await mcp.call_tool('handler')
        assert task_stopped.is_set()
        assert alive_when_stopped == [True]
        assert provider.released.is_set()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_async_handler_returning_generator_is_rejected() -> None:
    container = make_async_container(AsyncResourceProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def handler(resource: FromDishka[RequestResource]) -> object:
        def deferred() -> Iterator[RequestResource]:
            yield resource

        return deferred()

    try:
        with pytest.raises(Exception, match='returned a generator'):
            await mcp.call_tool('handler')
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_async_handler_returning_async_generator_is_rejected() -> None:
    container = make_async_container(AsyncResourceProvider())
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    async def handler(resource: FromDishka[RequestResource]) -> object:
        async def deferred() -> AsyncIterator[RequestResource]:
            yield resource

        return deferred()

    try:
        with pytest.raises(Exception, match='returned an async generator'):
            await mcp.call_tool('handler')
    finally:
        await container.close()


def test_inject_without_dependencies_returns_the_function_unchanged() -> None:
    def plain(value: str) -> str:
        return value

    async def plain_async(value: str) -> str:
        return value

    assert inject(plain) is plain
    assert inject(plain_async) is plain_async


@pytest.mark.asyncio
async def test_concurrent_apps_resolve_their_own_containers() -> None:
    first = FastMCP('first')
    second = FastMCP('second')
    first_container = make_async_container(LabelProvider(Label('first')))
    second_container = make_async_container(LabelProvider(Label('second')))
    setup_dishka(first_container, first)
    setup_dishka(second_container, second)
    both_entered = asyncio.Event()
    entered = 0

    async def wait_for_both(label: Label) -> str:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        return label

    @first.tool
    @inject
    async def first_tool(label: FromDishka[Label]) -> str:
        return await wait_for_both(label)

    @second.tool
    @inject
    async def second_tool(label: FromDishka[Label]) -> str:
        return await wait_for_both(label)

    try:
        first_result, second_result = await asyncio.gather(
            first.call_tool('first_tool'),
            second.call_tool('second_tool'),
        )
        first_block = first_result.content[0]
        second_block = second_result.content[0]
        assert isinstance(first_block, TextContent)
        assert isinstance(second_block, TextContent)
        assert first_block.text == 'first'
        assert second_block.text == 'second'
    finally:
        await first_container.close()
        await second_container.close()


@pytest.mark.asyncio
async def test_nested_fastmcp_call_restores_outer_container() -> None:
    outer = FastMCP('outer')
    inner = FastMCP('inner')
    outer_container = make_async_container(LabelProvider(Label('outer')))
    inner_container = make_async_container(LabelProvider(Label('inner')))
    setup_dishka(outer_container, outer)
    setup_dishka(inner_container, inner)

    @inner.tool
    @inject
    async def inner_tool(label: FromDishka[Label]) -> str:
        return label

    @outer.tool
    @inject
    async def outer_tool(label: FromDishka[Label]) -> str:
        before = get_async_container((), {})
        result = await inner.call_tool('inner_tool')
        after = get_async_container((), {})
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert before is outer_container
        assert after is outer_container
        return f'{label}:{block.text}'

    try:
        result = await outer.call_tool('outer_tool')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'outer:inner'
    finally:
        await outer_container.close()
        await inner_container.close()


@pytest.mark.asyncio
async def test_fastmcp_context_is_available_to_sync_tool_resource_and_prompt() -> None:
    container = make_container(FastMCPProvider(), SyncResourceProvider())
    mcp = FastMCP('sync-context')
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    def tool(context: FromDishka[Context]) -> str:
        return context.fastmcp.name

    @mcp.resource('data://context')
    @inject
    def resource(context: FromDishka[Context], value: FromDishka[RequestResource]) -> str:
        return f'{context.fastmcp.name}:{value}'

    @mcp.prompt
    @inject
    def prompt(context: FromDishka[Context]) -> str:
        return context.fastmcp.name

    try:
        tool_result = await mcp.call_tool('tool')
        resource_result = await mcp.read_resource('data://context')
        prompt_result = await mcp.render_prompt('prompt')
        tool_block = tool_result.content[0]
        prompt_block = prompt_result.messages[0].content
        assert isinstance(tool_block, TextContent)
        assert isinstance(prompt_block, TextContent)
        assert tool_block.text == 'sync-context'
        assert resource_result.contents[0].content == 'sync-context:resource'
        assert prompt_block.text == 'sync-context'
    finally:
        container.close()


@pytest.mark.asyncio
async def test_mounted_server_tool_resolves_its_own_container() -> None:
    parent = FastMCP('parent')
    child = FastMCP('child')
    parent_container = make_async_container(LabelProvider(Label('parent')))
    child_container = make_async_container(LabelProvider(Label('child')))
    setup_dishka(parent_container, parent)
    setup_dishka(child_container, child)

    @child.tool
    @inject
    async def whoami(label: FromDishka[Label]) -> str:
        return label

    parent.mount(child, namespace='sub')

    try:
        result = await parent.call_tool('sub_whoami')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == 'child'
    finally:
        await parent_container.close()
        await child_container.close()


def test_app_and_container_are_collected_together() -> None:
    app = FastMCP('gc')
    container = make_container(context={FastMCP: app})
    setup_dishka(container, app)
    container.close()
    app_ref = weakref.ref(app)

    del app, container
    gc.collect()

    assert app_ref() is None


def test_setup_is_idempotent_for_same_container_and_rejects_a_different_one() -> None:
    mcp = FastMCP('test')
    first = make_container()
    second = make_container()
    try:
        setup_dishka(first, mcp)
        setup_dishka(first, mcp)
        with pytest.raises(DishkaFastMCPError, match='already registered'):
            setup_dishka(second, mcp)
    finally:
        first.close()
        second.close()


def test_background_task_context_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def task_context() -> object:
        return object()

    monkeypatch.setattr(
        'dishka_fastmcp._container.get_task_context',
        task_context,
    )
    with pytest.raises(DishkaFastMCPError, match='background tasks'):
        get_async_container((), {})
