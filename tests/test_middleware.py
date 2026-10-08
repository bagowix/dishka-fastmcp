"""DishkaMiddleware opens one REQUEST scope for the whole MCP request."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Iterator, Sequence
from itertools import count
from typing import Any, Literal, NewType

import anyio.from_thread
import pytest
from dishka import AsyncContainer, Provider, Scope, make_async_container, make_container, provide
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import MCPError, ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers import Provider as ComponentProvider
from fastmcp.tools import Tool
from fastmcp.tools.function_tool import FunctionTool
from fastmcp_tasks import TasksExtension
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
    TextContent,
    TextResourceContents,
)

from dishka_fastmcp import (
    DishkaFastMCPError,
    DishkaMiddleware,
    FastMCPProvider,
    FromDishka,
    dishka_lifespan,
    get_request_container,
    inject,
    setup_dishka,
)

User = NewType('User', str)
Located = NewType('Located', int)
NO_ARGUMENTS: dict[str, Any] = {'type': 'object', 'properties': {}}


class Marker:
    """A REQUEST-scoped object whose identity tells requests apart."""

    def __init__(self, number: int) -> None:
        self.number = number


class RequestProvider(Provider):
    def __init__(self, first: int = 1) -> None:
        super().__init__()
        self._numbers = count(first)
        self.finalized: list[int] = []

    @provide(scope=Scope.REQUEST)
    async def marker(self) -> AsyncIterator[Marker]:
        await asyncio.sleep(0)  # lets concurrent lookups race for the same scope
        marker = Marker(next(self._numbers))
        try:
            yield marker
        finally:
            self.finalized.append(marker.number)

    @provide(scope=Scope.REQUEST)
    def user(self, ctx: Context) -> User:
        meta = ctx.request_context.meta if ctx.request_context is not None else None
        return User((meta or {}).get('user', 'anonymous'))

    @provide(scope=Scope.REQUEST)
    async def located(self, container: AsyncContainer) -> Located:
        return Located((await container.get(Marker)).number)


class Recorder(Middleware):
    """Sees the scope DishkaMiddleware opened: which requests got one, and their Marker."""

    def __init__(self) -> None:
        self.scoped: list[str] = []
        self.unscoped: list[str] = []
        self.markers: dict[str, Marker] = {}

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> object:
        method = str(context.method)
        try:
            container = get_request_container()
        except DishkaFastMCPError:
            self.unscoped.append(method)
        else:
            self.scoped.append(method)
            if method in {'tools/call', 'resources/read', 'prompts/get'}:
                self.markers[method] = await container.get(Marker)
        return await call_next(context)


class MarkerTools(ComponentProvider):
    """Lists one tool per request, named after that request's Marker."""

    async def _list_tools(self) -> Sequence[Tool]:
        try:
            container = get_request_container()
        except DishkaFastMCPError:  # FastMCP also lists components outside requests
            return []
        marker = await container.get(Marker)

        async def call(**arguments: object) -> int:
            del arguments
            return (await get_request_container().get(Marker)).number

        return [FunctionTool(fn=call, name=f'tool_{marker.number}', parameters=NO_ARGUMENTS)]


def served(provider: RequestProvider, *middleware: Middleware) -> FastMCP:
    container = make_async_container(provider, FastMCPProvider())
    server = FastMCP(
        'root',
        lifespan=dishka_lifespan(container),
        middleware=[DishkaMiddleware(), *middleware],
        mask_error_details=False,
    )
    setup_dishka(container, server)
    return server


async def call_text(client: Client, tool: str, meta: dict[str, Any] | None = None) -> str:
    result = await client.call_tool(tool, meta=meta)
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


@pytest.mark.asyncio
async def test_dynamic_provider_lists_tools_from_a_scope_of_its_own_per_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = RequestProvider()
    server = served(provider)
    server.add_provider(MarkerTools())
    fastmcp_logger = logging.getLogger('fastmcp')
    fastmcp_logger.addHandler(caplog.handler)

    try:
        async with Client(server) as client:
            first = [tool.name for tool in await client.list_tools()]
            second = [tool.name for tool in await client.list_tools()]
    finally:
        fastmcp_logger.removeHandler(caplog.handler)

    assert first == ['tool_1']
    assert second == ['tool_2']
    assert provider.finalized == [1, 2]
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []


@pytest.mark.asyncio
async def test_concurrent_lookups_in_one_request_share_one_instance() -> None:
    provider = RequestProvider()
    server = served(provider)
    server.add_provider(MarkerTools())
    server.add_provider(MarkerTools())

    async with Client(server) as client:
        tools = [tool.name for tool in await client.list_tools()]

    # Each provider names its tool after the Marker it got; FastMCP lists a name once.
    assert tools == ['tool_1']
    assert provider.finalized == [1]


@pytest.mark.parametrize('mode', ['auto', 'legacy'])
@pytest.mark.asyncio
async def test_middleware_container_and_handler_share_one_instance_finalized_after_the_handler(
    mode: Literal['auto', 'legacy'],
) -> None:
    provider = RequestProvider()
    recorder = Recorder()
    server = served(provider, recorder)
    seen: dict[str, Any] = {}

    @server.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        seen['injected'] = marker
        seen['container'] = await get_request_container().get(Marker)
        seen['finalized'] = list(provider.finalized)
        return marker.number

    async with Client(server, mode=mode) as client:
        await call_text(client, 'work')

    marker = recorder.markers['tools/call']
    assert seen['injected'] is marker
    assert seen['container'] is marker
    assert seen['finalized'] == []
    assert provider.finalized == [marker.number]


@pytest.mark.asyncio
async def test_concurrent_requests_get_scopes_of_their_own() -> None:
    provider = RequestProvider()
    server = served(provider)
    both_running = asyncio.Barrier(2)

    @server.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        await both_running.wait()
        return marker.number

    async with Client(server) as client:
        first, second = await asyncio.wait_for(
            asyncio.gather(call_text(client, 'work'), call_text(client, 'work')),
            timeout=5,
        )

    assert {first, second} == {'1', '2'}
    assert sorted(provider.finalized) == [1, 2]


@pytest.mark.asyncio
async def test_closing_a_generator_early_leaves_the_shared_scope_open() -> None:
    provider = RequestProvider()
    server = served(provider)

    @inject
    async def stream(marker: FromDishka[Marker]) -> AsyncIterator[Marker]:
        yield marker
        yield marker

    @server.tool
    async def work() -> bool:
        messages = stream()
        first = await anext(messages)
        await messages.aclose()
        still_open = not provider.finalized
        return still_open and await get_request_container().get(Marker) is first

    async with Client(server) as client:
        shared = await call_text(client, 'work')

    assert shared == 'true'
    assert provider.finalized == [1]


@pytest.mark.parametrize(
    ('mode', 'handshake'),
    [('auto', 'server/discover'), ('legacy', 'initialize')],
)
@pytest.mark.asyncio
async def test_every_request_gets_a_scope(
    mode: Literal['auto', 'legacy'],
    handshake: str,
) -> None:
    recorder = Recorder()
    server = served(RequestProvider(), recorder)

    @server.tool
    async def tool() -> str:
        return 'tool'

    @server.resource('data://item')
    async def resource() -> str:
        return 'resource'

    @server.prompt
    async def prompt() -> str:
        return 'prompt'

    async with Client(server, mode=mode) as client:
        await client.list_tools()
        await client.list_resources()
        await client.list_prompts()
        await client.call_tool('tool')
        await client.read_resource('data://item')
        await client.get_prompt('prompt')

    assert recorder.unscoped == []
    assert set(recorder.scoped) >= {
        handshake,
        'tools/list',
        'resources/list',
        'prompts/list',
        'tools/call',
        'resources/read',
        'prompts/get',
    }


@pytest.mark.asyncio
async def test_function_tool_resolves_a_runtime_type_from_the_request_scope() -> None:
    provider = RequestProvider()
    recorder = Recorder()
    server = served(provider, recorder)
    dependency: type[Marker] = Marker

    async def call(**arguments: object) -> int:
        del arguments
        return (await get_request_container().get(dependency)).number

    server.add_tool(FunctionTool(fn=call, name='runtime', parameters=NO_ARGUMENTS))

    async with Client(server) as client:
        number = await call_text(client, 'runtime')

    assert int(number) == recorder.markers['tools/call'].number
    assert provider.finalized == [int(number)]


@pytest.mark.asyncio
async def test_handler_exception_closes_the_scope_and_propagates_unwrapped() -> None:
    provider = RequestProvider()
    errors: list[BaseException] = []

    class Observer(Middleware):
        async def on_request(
            self,
            context: MiddlewareContext[Any],
            call_next: CallNext[Any, Any],
        ) -> object:
            try:
                return await call_next(context)
            except Exception as exc:
                errors.append(exc)
                raise

    container = make_async_container(provider, FastMCPProvider())
    server = FastMCP(
        'root',
        lifespan=dishka_lifespan(container),
        middleware=[Observer(), DishkaMiddleware()],
        mask_error_details=False,
    )
    setup_dishka(container, server)

    @server.tool
    @inject
    async def boom(marker: FromDishka[Marker]) -> str:
        raise ValueError(f'boom {marker.number}')

    async with Client(server) as client:
        with pytest.raises(ToolError, match='boom 1'):
            await client.call_tool('boom')

    assert provider.finalized == [1]
    assert len(errors) == 1
    assert not isinstance(errors[0], DishkaFastMCPError)
    assert 'boom 1' in str(errors[0])


@pytest.mark.asyncio
async def test_sync_handler_still_needs_a_sync_container() -> None:
    server = served(RequestProvider())

    @server.tool
    @inject
    def sync_work(marker: FromDishka[Marker]) -> int:
        return marker.number

    async with Client(server) as client:
        with pytest.raises(ToolError, match='Sync handler needs a Container'):
            await client.call_tool('sync_work')


@pytest.mark.asyncio
async def test_sync_handler_with_its_own_container_keeps_its_scope_in_the_worker_thread() -> None:
    created: list[int] = []
    finalized: list[int] = []

    class ThreadProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def marker(self) -> Iterator[Marker]:
            created.append(threading.get_ident())
            try:
                yield Marker(0)
            finally:
                finalized.append(threading.get_ident())

    sync_container = make_container(ThreadProvider())
    router = FastMCP('router')
    setup_dishka(sync_container, router)

    @router.tool
    @inject
    def sync_work(marker: FromDishka[Marker]) -> int:
        del marker
        return threading.get_ident()

    server = served(RequestProvider())
    server.mount(router, namespace='router')

    try:
        async with Client(server) as client:
            worker = int(await call_text(client, 'router_sync_work'))
    finally:
        sync_container.close()

    assert worker != threading.get_ident()
    assert created == finalized == [worker]


@pytest.mark.asyncio
async def test_handler_two_mounts_deep_shares_the_request_scope() -> None:
    provider = RequestProvider()
    recorder = Recorder()
    server = served(provider, recorder)
    leaf = FastMCP('leaf')

    @leaf.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        return marker.number

    middle = FastMCP('middle')
    middle.mount(leaf, namespace='leaf')
    server.mount(middle, namespace='middle')

    async with Client(server) as client:
        number = int(await call_text(client, 'middle_leaf_work'))

    assert number == recorder.markers['tools/call'].number
    assert provider.finalized == [number]


@pytest.mark.asyncio
async def test_mounted_servers_middleware_does_not_open_a_second_scope() -> None:
    provider = RequestProvider()
    recorder = Recorder()
    server = served(provider, recorder)
    router = FastMCP('router', middleware=[DishkaMiddleware()])

    @router.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        return marker.number

    server.mount(router, namespace='router')

    async with Client(server) as client:
        number = int(await call_text(client, 'router_work'))

    assert number == recorder.markers['tools/call'].number
    assert provider.finalized == [number]


@pytest.mark.asyncio
async def test_router_with_its_own_container_keeps_its_own_scope() -> None:
    root_provider = RequestProvider()
    recorder = Recorder()
    server = served(root_provider, recorder)
    router_provider = RequestProvider(first=100)
    router_container = make_async_container(router_provider, FastMCPProvider())
    router = FastMCP('router')
    setup_dishka(router_container, router)

    @router.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        return marker.number

    server.mount(router, namespace='router')

    try:
        async with Client(server) as client:
            number = int(await call_text(client, 'router_work'))
    finally:
        await router_container.close()

    assert number == 100
    assert router_provider.finalized == [100]
    assert root_provider.finalized == [recorder.markers['tools/call'].number]


@pytest.mark.asyncio
async def test_root_set_up_without_lifespan_shares_its_scope_with_mounted_routers() -> None:
    provider = RequestProvider()
    container = make_async_container(provider, FastMCPProvider())
    server = FastMCP('root', middleware=[DishkaMiddleware()])
    setup_dishka(container, server)
    router = FastMCP('router')

    @router.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        return marker.number

    server.mount(router)

    try:
        async with Client(server) as client:
            number = int(await call_text(client, 'work'))
    finally:
        await container.close()

    assert number == 1
    assert provider.finalized == [1]


@pytest.mark.asyncio
async def test_user_built_from_request_meta_by_a_context_provider() -> None:
    server = served(RequestProvider())

    @server.tool
    @inject
    async def whoami(user: FromDishka[User]) -> str:
        return user

    async with Client(server) as client:
        alice = await call_text(client, 'whoami', meta={'user': 'alice'})
        anonymous = await call_text(client, 'whoami')

    assert (alice, anonymous) == ('alice', 'anonymous')


@pytest.mark.asyncio
async def test_get_request_container_outside_a_scope_names_the_middleware() -> None:
    container = make_async_container(RequestProvider(), FastMCPProvider())
    server = FastMCP('root', lifespan=dishka_lifespan(container), mask_error_details=False)
    setup_dishka(container, server)

    @server.tool
    async def work() -> str:
        await get_request_container().get(Marker)
        return 'unreachable'

    with pytest.raises(DishkaFastMCPError, match='DishkaMiddleware'):
        get_request_container()
    async with Client(server) as client:
        with pytest.raises(ToolError, match='first middleware'):
            await client.call_tool('work')


@pytest.mark.asyncio
async def test_direct_call_outside_a_request_keeps_the_handler_scope() -> None:
    provider = RequestProvider()
    recorder = Recorder()
    container = make_async_container(provider, FastMCPProvider())
    server = FastMCP('root', middleware=[DishkaMiddleware(), recorder])
    setup_dishka(container, server)

    @server.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        return marker.number

    try:
        result = await server.call_tool('work')
    finally:
        await container.close()

    block = result.content[0]
    assert isinstance(block, TextContent)
    assert block.text == '1'
    assert recorder.unscoped == ['tools/call']
    assert provider.finalized == [1]


@pytest.mark.asyncio
async def test_middleware_with_a_sync_container_fails_the_request() -> None:
    container = make_container()
    server = FastMCP('root', middleware=[DishkaMiddleware()], mask_error_details=False)
    setup_dishka(container, server)

    @server.tool
    async def work() -> str:
        return 'unreachable'

    try:
        with pytest.raises(MCPError, match='AsyncContainer'):
            async with Client(server, mode='legacy') as client:
                await client.call_tool('work')
    finally:
        container.close()


@pytest.mark.asyncio
async def test_middleware_without_a_container_fails_the_request() -> None:
    server = FastMCP('root', middleware=[DishkaMiddleware()], mask_error_details=False)

    @server.tool
    async def work() -> str:
        return 'unreachable'

    with pytest.raises(MCPError, match='No dishka container'):
        async with Client(server, mode='legacy') as client:
            await client.call_tool('work')


@pytest.mark.asyncio
async def test_background_task_gets_no_request_scope() -> None:
    container = make_async_container(RequestProvider(), FastMCPProvider())
    server = FastMCP('root', middleware=[DishkaMiddleware()], mask_error_details=False)
    server.add_extension(TasksExtension())
    setup_dishka(container, server)

    @server.tool(task=True)
    async def background() -> str:
        await get_request_container().get(Marker)
        return 'unreachable'

    @server.tool(task=True)
    @inject
    async def injected(marker: FromDishka[Marker]) -> int:
        return marker.number

    try:
        async with Client(server) as client:
            with pytest.raises(ToolError, match='background tasks'):
                await client.call_tool('background')
            with pytest.raises(ToolError, match='background tasks'):
                await client.call_tool('injected')
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_ping_gets_a_scope_in_legacy_mode() -> None:
    recorder = Recorder()
    server = served(RequestProvider(), recorder)

    async with Client(server, mode='legacy') as client:
        await client.ping()

    assert 'ping' in recorder.scoped


@pytest.mark.asyncio
async def test_factory_resolving_through_its_container_does_not_deadlock() -> None:
    recorder = Recorder()
    server = served(RequestProvider(), recorder)

    @server.tool
    @inject
    async def locate(located: FromDishka[Located]) -> int:
        return located

    async with Client(server) as client:
        number = await asyncio.wait_for(call_text(client, 'locate'), timeout=5)

    assert int(number) == recorder.markers['tools/call'].number


@pytest.mark.asyncio
async def test_resource_prompt_and_generator_handlers_share_the_request_scope() -> None:
    recorder = Recorder()
    server = served(RequestProvider(), recorder)

    @server.resource('data://marker')
    @inject
    async def marker_resource(marker: FromDishka[Marker]) -> str:
        return str(marker.number)

    @server.prompt
    @inject
    async def marker_prompt(marker: FromDishka[Marker]) -> str:
        return str(marker.number)

    @server.tool
    @inject
    async def marker_stream(marker: FromDishka[Marker]) -> AsyncIterator[int]:
        yield marker.number

    async with Client(server) as client:
        resource = (await client.read_resource('data://marker'))[0]
        prompt = (await client.get_prompt('marker_prompt')).messages[0].content
        stream = await call_text(client, 'marker_stream')

    assert isinstance(resource, TextResourceContents)
    assert isinstance(prompt, TextContent)
    assert resource.text == str(recorder.markers['resources/read'].number)
    assert prompt.text == str(recorder.markers['prompts/get'].number)
    assert stream == f'[{recorder.markers["tools/call"].number}]'


@pytest.mark.asyncio
async def test_sync_handler_resolves_from_the_request_scope_through_the_event_loop() -> None:
    provider = RequestProvider()
    recorder = Recorder()
    server = served(provider, recorder)

    @server.tool
    def sync_work() -> int:
        container = get_request_container()
        return anyio.from_thread.run(container.get, Marker).number

    async with Client(server) as client:
        number = int(await call_text(client, 'sync_work'))

    assert number == recorder.markers['tools/call'].number
    assert provider.finalized == [number]


@pytest.mark.asyncio
async def test_mounted_handler_gets_the_server_that_opened_the_scope() -> None:
    server = served(RequestProvider())
    router = FastMCP('router')

    @router.tool
    @inject
    async def app_name(app: FromDishka[FastMCP]) -> str:
        return app.name

    server.mount(router, namespace='router')

    async with Client(server) as client:
        name = await call_text(client, 'router_app_name')

    assert name == 'root'


@pytest.mark.asyncio
async def test_router_with_its_own_container_gets_no_request_container() -> None:
    server = served(RequestProvider())
    router_container = make_async_container(RequestProvider(first=100), FastMCPProvider())
    router = FastMCP('router', middleware=[DishkaMiddleware()])
    setup_dishka(router_container, router)

    async def call(**arguments: object) -> int:
        del arguments
        return (await get_request_container().get(Marker)).number

    router.add_tool(FunctionTool(fn=call, name='runtime', parameters=NO_ARGUMENTS))
    server.mount(router, namespace='router')

    try:
        async with Client(server) as client:
            with pytest.raises(ToolError, match='container of its own'):
                await client.call_tool('router_runtime')
    finally:
        await router_container.close()


@pytest.mark.asyncio
async def test_scope_is_detached_once_the_request_is_served() -> None:
    server = served(RequestProvider())
    served_event = asyncio.Event()
    late: list[asyncio.Task[AsyncContainer]] = []

    async def after_the_request() -> AsyncContainer:
        await served_event.wait()
        return get_request_container()

    @server.tool
    async def spawn() -> str:
        late.append(asyncio.create_task(after_the_request()))
        return 'spawned'

    async with Client(server) as client:
        await client.call_tool('spawn')
        served_event.set()
        with pytest.raises(DishkaFastMCPError, match='No dishka request scope'):
            await late[0]


@pytest.mark.asyncio
async def test_completion_handler_runs_outside_the_request_scope() -> None:
    recorder = Recorder()
    server = served(RequestProvider(), recorder)
    seen: list[str] = []

    @server.prompt
    async def greet(name: str) -> str:
        return name

    @server.completion
    async def complete(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> Completion:
        del ref, argument, context
        try:
            get_request_container()
        except DishkaFastMCPError:
            seen.append('unscoped')
        return Completion(values=[])

    async with Client(server) as client:
        await client.complete(PromptReference(name='greet'), {'name': 'name', 'value': ''})

    assert 'completion/complete' in recorder.scoped
    assert seen == ['unscoped']


@pytest.mark.asyncio
async def test_finalizer_error_fails_the_request_after_the_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingFinalizer(Provider):
        @provide(scope=Scope.REQUEST)
        async def marker(self) -> AsyncIterator[Marker]:
            yield Marker(0)
            raise RuntimeError('finalizer failed')

    container = make_async_container(FailingFinalizer(), FastMCPProvider())
    server = FastMCP(
        'root',
        lifespan=dishka_lifespan(container),
        middleware=[DishkaMiddleware()],
        mask_error_details=False,
    )
    setup_dishka(container, server)
    handled: list[int] = []

    @server.tool
    @inject
    async def work(marker: FromDishka[Marker]) -> int:
        handled.append(marker.number)
        return marker.number

    async with Client(server) as client:
        with pytest.raises(MCPError, match='Internal server error'):
            await client.call_tool('work')

    assert handled == [0]
    assert 'finalizer failed' in caplog.text
