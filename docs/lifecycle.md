# Lifecycle and scopes

dishka-fastmcp separates FastMCP registration from operation execution. The
decorator cleans the public signature during registration. At execution time,
`@inject` resolves the active FastMCP application and owns the request scope,
unless `DishkaMiddleware` opened one for the whole MCP request.

## Scope boundaries

| Scope | Boundary | Owner |
|---|---|---|
| `Scope.APP` | Server lifetime | Root container, closed by `dishka_lifespan` or by its owner |
| `Scope.REQUEST` | One tool call, resource read, or prompt render; with `DishkaMiddleware`, one MCP request | `@inject`, or `DishkaMiddleware` |

Pass `dishka_lifespan` to the FastMCP server you run. By default it closes the
container on shutdown, which fits a container the server owns; for a shared
container see [Sharing the container](#sharing-the-container):

```python
container = make_async_container(AppProvider())
mcp = FastMCP('app', lifespan=dishka_lifespan(container))
```

If the FastMCP server already has a lifespan, combine it with
`dishka_lifespan`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans


@asynccontextmanager
async def application_lifespan(
    app: FastMCP,
) -> AsyncIterator[dict[str, object]]:
    resource = await create_resource()
    try:
        yield {'resource': resource}
    finally:
        await resource.close()


mcp = FastMCP(
    'app',
    lifespan=combine_lifespans(
        application_lifespan,
        dishka_lifespan(container),
    ),
)
setup_dishka(container, mcp)
```

`combine_lifespans` enters lifespans in argument order and exits them in reverse
order. Placing `dishka_lifespan` last closes the Dishka container before the
other lifespan releases its resources, which suits providers that depend on
those resources. When the other lifespan uses the container instead, list
`dishka_lifespan` first; see [Sharing the container](#sharing-the-container).

`dishka_lifespan` returns a FastMCP `Lifespan`, so it also composes with
`@lifespan` functions through `|`. The order rules are the same: the left side
enters first and exits last. `|` accepts only `Lifespan` instances, so a
lifespan written with `@asynccontextmanager`, like `application_lifespan` above,
goes through `combine_lifespans` or FastMCP's `ContextManagerLifespan` wrapper.

```python
from fastmcp.server.lifespan import lifespan


@lifespan
async def database(server: FastMCP) -> AsyncIterator[dict[str, object]]:
    yield {'database': await connect()}


mcp = FastMCP('app', lifespan=database | dishka_lifespan(container))
```

The combined lifespan above belongs to a `FastMCP` instance. When composing
FastMCP with FastAPI or Starlette, keep `dishka_lifespan` on the FastMCP server
and combine the lifespan exposed by `mcp.http_app()` at the ASGI layer. This is
also the correct pattern for hosting multiple independent FastMCP servers:

```python
from fastapi import FastAPI


first = FastMCP('first', lifespan=dishka_lifespan(first_container))
second = FastMCP('second', lifespan=dishka_lifespan(second_container))
setup_dishka(first_container, first)
setup_dishka(second_container, second)

first_app = first.http_app()
second_app = second.http_app()
app = FastAPI(
    lifespan=combine_lifespans(
        first_app.lifespan,
        second_app.lifespan,
    ),
)
app.mount('/first', first_app)
app.mount('/second', second_app)
```

Do not combine `dishka_lifespan(first_container)` and
`dishka_lifespan(second_container)` directly at the ASGI layer:
`combine_lifespans` passes the same ASGI application to every lifespan. Each
`mcp.http_app().lifespan` adapter preserves the corresponding FastMCP instance.

## Sharing the container

`dishka_lifespan` closes the container when the FastMCP server stops. If the
same container also serves a FastAPI application, a FastStream broker, a
background worker or a test session, those components must be done with it by
then. Dishka raises no error on `get()` after `close()`: it creates the
`Scope.APP` dependencies again. Code that runs after the close silently opens
new resources, and they leak unless the container is closed once more.

When the other component's lifespan is combined with the FastMCP one, list the
FastMCP lifespan first. `combine_lifespans` exits lifespans in reverse order, so
the container closes after the other component has shut down:

```python
mcp_app = mcp.http_app(path='/')
app = FastAPI(lifespan=combine_lifespans(mcp_app.lifespan, app_lifespan))
app.mount('/mcp', mcp_app)
```

FastMCP's FastAPI guide lists `app_lifespan` first. With that order the
container is already closed while `app_lifespan` shuts down.

When the container belongs to something else, pass `finalize_container=False`.
The lifespan still registers the container and hands it to mounted routers,
and the owner closes it. The name follows `finalize_container` in dishka's own
integrations; here it defaults to `True`:

```python
mcp = FastMCP('app', lifespan=dishka_lifespan(container, finalize_container=False))
setup_dishka(container, mcp)
```

A registration that `setup_dishka` made before the server started survives the
shutdown, so direct `call_tool()` calls keep working between sessions. It still
points at the container after the owner closes it, so stop calling the server
at that point.

Ordering cannot help when the owner's lifecycle runs outside this
`combine_lifespans` call: a FastStream application, a background worker, or a
test session. Each `async with Client(server)` block starts and stops the
server, so with the default `finalize_container=True` two blocks in a row
close the container when the first one exits, and the second one silently gets
new `Scope.APP` dependencies. Use `finalize_container=False` there.

## Mounted servers

A server built from routers needs one container, on the server you serve. Give
that server `dishka_lifespan(container)` and `setup_dishka(container, server)`;
routers mounted into it at any depth use its container. This includes routers
mounted later and mounts with `namespace` or `tool_names`:

```python
container = make_async_container(AppProvider())
server = FastMCP('app', lifespan=dishka_lifespan(container))
setup_dishka(container, server)

users = FastMCP('users')


@users.tool
@inject
async def get_user(user_id: int, repo: FromDishka[UserRepository]) -> User:
    return await repo.get(user_id)


server.mount(users, namespace='users')
```

`dishka_lifespan` puts the container into the server's lifespan state. FastMCP
hands that state to every MCP request the server serves, mounted components
included, so a component whose own server has no container takes it from there.
The rules that follow from it:

- A mounted server with its own `setup_dishka` keeps its own container. Its own
  `dishka_lifespan` does the same only when the server is mounted before the
  parent starts: FastMCP runs a mounted server's lifespan together with the
  parent's, so a server mounted while the parent serves needs `setup_dishka`.
- Servers mounted under such a server without a container of their own use the
  serving root's container, never the nearest set-up server's. Nothing reports
  it when the root's container provides the same types, so call `setup_dishka`
  on a server that must use the middle server's container.
- A router mounted into several servers uses the container of the server that
  serves the request, so one router module can back several applications.
- A server without `dishka_lifespan` hands no container to its routers, even when
  the same router is mounted into another server that has one. The container
  must also reach the serving server's lifespan state: `combine_lifespans` keeps
  it next to lifespans that yield a mapping or `None`, and a lifespan of your own
  that enters `dishka_lifespan` must pass its state through.
- The container travels with MCP requests (a `Client`, HTTP, stdio). A direct
  `await server.call_tool(...)` outside a request reaches only the server's own
  components; test mounted routers through `Client(server)`.
- A server behind `create_proxy(...)` runs its own MCP session and needs its own
  `setup_dishka`.

`Context` and `FastMCP` from `FastMCPProvider` still describe the mounted server
that owns the executing component.

## Request scope for the whole MCP request

`@inject` opens `Scope.REQUEST` around one handler call, so code outside a
handler has no scope: a dynamic component provider that builds `tools/list` from
the user and feature flags, a tool assembled from `FunctionTool(fn=...)` whose
dependency type is only known at runtime, or middleware. Add `DishkaMiddleware`
to open one scope for the whole MCP request, and reach it through
`get_request_container()`:

```python
from collections.abc import Sequence

from fastmcp.server.providers import Provider
from fastmcp.tools import Tool

from dishka_fastmcp import DishkaFastMCPError, DishkaMiddleware, get_request_container


class FeatureTools(Provider):
    async def _list_tools(self) -> Sequence[Tool]:
        try:
            container = get_request_container()
        except DishkaFastMCPError:
            return []  # FastMCP also lists components outside any request
        flags = await container.get(FeatureFlags)
        return [tool for tool in ALL_TOOLS if flags.enabled(tool.name)]


container = make_async_container(AppProvider(), FastMCPProvider())
mcp = FastMCP(
    'app',
    lifespan=dishka_lifespan(container),
    middleware=[DishkaMiddleware(), AuditMiddleware()],
)
setup_dishka(container, mcp)
mcp.add_provider(FeatureTools())
```

Without the middleware nothing changes: `@inject` keeps its per-handler scope.
With it:

- Put `DishkaMiddleware` first. Middleware listed before it runs outside the
  scope.
- Every request gets a scope: tool calls, list requests, resource reads, prompt
  renders, the handshake (`initialize` or `server/discover`) and `ping`. The
  scope is lazy, so a request that resolves nothing creates nothing.
  Notifications get no scope.
- `@inject` handlers share the scope, so each REQUEST dependency has one instance
  per request, also when providers of one request resolve it concurrently. The
  scope is finalized once, when the rest of the request is done and before the
  response goes out, so a slow finalizer delays the response. An exception from
  a handler closes it and propagates unchanged.
- To keep one instance per request, the scope has a lock, like dishka's APP
  scope: lookups in one request run one at a time. A factory may resolve
  through the container it gets as a parameter, but only in its own task. Called
  from a task of its own, for example under `asyncio.gather`, that lookup waits
  for the lock its caller holds and never returns. Declare such dependencies as
  factory parameters instead.
- The middleware finalizes the scope after the handler has produced its result.
  A finalizer that raises therefore fails the whole request with a protocol
  error, which FastMCP may mask as `Internal server error`; the server log has
  the cause. Without the middleware the same error is raised inside the handler
  call and reaches the client as a tool error.
- `get_request_container()` raises `DishkaFastMCPError` where the request has no
  scope. FastMCP lists component providers outside any request, for example on
  startup to collect background-task components, so a provider returns no
  components there, as above. Completion and extension-method handlers run
  without the scope too: FastMCP gives them a fresh request context. A task a handler spawns loses the scope once the request is served.
- A tool built as `FunctionTool(fn=call, parameters=...)` resolves its runtime
  dependency with `await get_request_container().get(dependency_type)`. The
  container belongs to the event loop: a sync `call`, which FastMCP runs in a
  worker thread, resolves with
  `anyio.from_thread.run(container.get, dependency_type)`.
- The root container is found as for `@inject`, and it must be an
  `AsyncContainer`. The scope lives on the event loop, where a sync `Container`
  would block, so with one every request fails with a protocol error, and the
  server logs the `DishkaFastMCPError`.
- Sync handlers keep a scope of their own in their worker thread: sharing the
  event-loop scope would finalize thread-affine resources away from the thread
  that created them. Under an `AsyncContainer` they fail as without the
  middleware, because a sync handler needs a sync `Container`; a mounted router
  set up with its own sync `Container` keeps working.
- The outermost `DishkaMiddleware` of a request opens the scope; a mounted
  server's own `DishkaMiddleware` does not open a second one. Mounted routers
  without a container of their own share the scope. A router set up with a
  different container keeps per-handler scopes from it, and
  `get_request_container()` raises there, so its code never mixes instances of
  two containers.
- The scope's `Context` and `FastMCP` describe the request as the middleware
  sees it: `FastMCP` is the server whose `DishkaMiddleware` opened the scope,
  also for the handlers of mounted routers.
- A direct `await server.call_tool(...)` outside an MCP request gets no scope,
  and `@inject` opens one per handler. `task=True` handlers get none either:
  `get_request_container()` raises in the worker, and `@inject` keeps rejecting
  them.

Values built from the request need no extra hook. A REQUEST provider that takes
`Context` from `FastMCPProvider` reads the request, list requests included:

```python
class UserProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def user(self, ctx: Context) -> User:
        meta = ctx.request_context.meta if ctx.request_context else None
        return User((meta or {}).get('user', 'anonymous'))
```

## Sync request finalization

FastMCP offloads regular sync handlers to worker threads. `@inject` opens and
closes the Dishka request container inside the wrapped handler, so thread-affine
resources stay in one thread:

```python
import sqlite3
from collections.abc import Iterator

from dishka import Provider, Scope, provide


class DatabaseProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect('app.db')
        try:
            yield connection
        finally:
            connection.close()
```

Sync `Scope.APP` dependencies must still be thread-safe because calls may run on
different workers and APP cleanup happens during server shutdown.

## Limitations

`Scope.SESSION` is not supported because FastMCP does not provide a deterministic
teardown boundary for a Dishka session container.

Handlers registered with `task=True` are also unsupported. FastMCP 4 runs them
in a `fastmcp-tasks` worker, outside the request that queued them, and `@inject`
raises `DishkaFastMCPError` there. Resolve dependencies during the request and
pass plain values to background work.
