# API reference

The public API is exported from `dishka_fastmcp`.

## `setup_dishka`

```python
setup_dishka(container: AsyncContainer | Container, app: FastMCP) -> None
```

Associates a root container with the FastMCP application. Call it once before
the server starts. Routers mounted into the application get the container
through `dishka_lifespan`; see [Mounted servers](lifecycle.md#mounted-servers).

## `inject`

```python
@inject
async def handler(service: FromDishka[Service]) -> Result: ...
```

Resolves `FromDishka` parameters, removes them from the public signature, and
manages one `Scope.REQUEST` around the handler. Sync and async functions are
detected automatically. Under `DishkaMiddleware`, async handlers share the scope
the middleware opened instead.

Put `@inject` below the FastMCP decorator (`@mcp.tool`, `@mcp.resource`,
`@mcp.prompt`, or a standalone one such as `@tool`). Applied to a function a
FastMCP decorator has already processed, it raises `DishkaFastMCPError` at
import: FastMCP would serve the bare function and let the client fill in the
`FromDishka` parameters.

FastMCP tools may be sync or async generator functions. Their REQUEST scope
stays open for the whole iteration. Resources and prompts must use their normal
FastMCP return types.

An ordinary `def` or `async def` must return its completed value directly.
Returning an awaitable, generator, or async generator would defer work until
after the function returns, when its REQUEST scope may already be finalized, so
`@inject` raises
`DishkaFastMCPError`. Coroutine-like objects are closed during rejection; an
`asyncio.Task` returned by an async handler is cancelled and awaited before the
scope is finalized.

## `dishka_lifespan`

```python
dishka_lifespan(
    container: AsyncContainer | Container,
    *,
    finalize_container: bool = True,
) -> fastmcp.server.lifespan.Lifespan
```

Returns a FastMCP lifespan that registers the container for the application on
startup, as `setup_dishka` does. By default it also closes the async or sync
container and removes the registration on shutdown. Its lifespan state is
`{'dishka_fastmcp.container': container}`, which is how servers mounted into the
application find the container during the MCP requests it serves. Compose this
lifespan with `@lifespan` functions through `|`, or with other lifespans using
`fastmcp.utilities.lifespan.combine_lifespans`. On startup it raises
`DishkaFastMCPError` if a different container is already registered for the
app, by `setup_dishka` or by another `dishka_lifespan`.

With `finalize_container=False` the lifespan registers the container and
publishes it in the same way, but leaves closing it to the container's owner,
and a registration that `setup_dishka` made before startup survives the
shutdown. See
[Sharing the container](lifecycle.md#sharing-the-container).

## `DishkaMiddleware`

```python
FastMCP('app', middleware=[DishkaMiddleware(), ...])
DishkaMiddleware(*, session_scope: bool = False)
```

A FastMCP middleware that opens `Scope.REQUEST` around every MCP request,
list requests, reads, prompt renders and the handshake included, and finalizes
it once before the response goes out. Async `@inject` handlers share the scope.
Put it first in the middleware list. It finds the root container as `@inject`
does, and the container must be an `AsyncContainer`. See
[Request scope for the whole MCP request](lifecycle.md#request-scope-for-the-whole-mcp-request).

With `session_scope=True` it opens the REQUEST scope from a `Scope.SESSION`
scope per MCP connection, which the MCP SDK finalizes when the connection
closes. A 2026-07-28 connection and a stateless HTTP request live one request,
and so does their SESSION scope. See
[Session scope for the MCP connection](lifecycle.md#session-scope-for-the-mcp-connection).

## `get_request_container`

```python
get_request_container() -> AsyncContainer
```

Returns the REQUEST container `DishkaMiddleware` opened for the current MCP
request, the one async `@inject` handlers share. It raises `DishkaFastMCPError`
where there is none: without the middleware, outside an MCP request, in
completion and extension-method handlers, in a `task=True` worker, or on a
mounted server set up with a container of its own.

## `FastMCPProvider`

A Dishka provider for the current `fastmcp.Context` and `fastmcp.FastMCP`
objects, both at `Scope.REQUEST`. Add an instance when constructing the
container. Operation arguments
remain regular tool, resource, or prompt parameters.

## `FromDishka`

Re-export of Dishka's dependency marker. It is provided here so handlers can
import their entire integration surface from one package.

## `DishkaFastMCPError`

Raised for integration misuse: a missing container registration, a container
type that does not match the handler's sync or async execution model, a
different container registered for the same application, `@inject` applied
above a FastMCP decorator, a deferred result returned by an ordinary handler,
injection inside a `task=True` background worker, `get_request_container()`
outside a request scope, or `DishkaMiddleware` with a sync container.

## `__version__`

The installed package version, as a string.
