# API reference

The public API is exported from `dishka_fastmcp`.

## `setup_dishka`

```python
setup_dishka(container: AsyncContainer | Container, app: FastMCP) -> None
```

Associates a root container with the FastMCP application. Call it once before
the server starts.

## `inject`

```python
@inject
async def handler(service: FromDishka[Service]) -> Result: ...
```

Resolves `FromDishka` parameters, removes them from the public signature, and
manages one `Scope.REQUEST` around the handler. Sync and async functions are
detected automatically.

FastMCP tools may be sync or async generator functions. Their REQUEST scope
stays open for the whole iteration. Resources and prompts must use their normal
FastMCP return types.

An ordinary `def` or `async def` must return its completed value directly.
Returning an awaitable, generator, or async generator would defer work until
after the function's REQUEST scope exits, so `@inject` raises
`DishkaFastMCPError`. Coroutine-like objects are closed during rejection;
returned `asyncio.Task` instances are cancelled and awaited before the scope is
finalized.

## `dishka_lifespan`

```python
dishka_lifespan(container) -> Callable[..., AbstractAsyncContextManager[None]]
```

Returns a FastMCP lifespan that closes an async or sync root container during
server shutdown and removes its `setup_dishka` registration. Compose this
lifespan with other FastMCP lifespans using
`fastmcp.utilities.lifespan.combine_lifespans`. On startup it raises
`DishkaFastMCPError` if `setup_dishka` registered a different container for the
app.

## `FastMCPProvider`

A Dishka provider for the current `fastmcp.Context` and `fastmcp.FastMCP`
objects. Add an instance when constructing the container. Operation arguments
remain regular tool, resource, or prompt parameters.

## `FromDishka`

Re-export of Dishka's dependency marker. It is provided here so handlers can
import their entire integration surface from one package.

## `DishkaFastMCPError`

Raised for integration misuse, including a missing container registration or a
container type that does not match the handler's sync or async execution model.
