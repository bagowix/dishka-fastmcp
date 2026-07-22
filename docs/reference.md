# API reference

The public API is exported from `dishka_fastmcp`.

## `setup_dishka`

```python
setup_dishka(container: AsyncContainer | Container, app: FastMCP) -> None
```

Registers `DishkaMiddleware` on the FastMCP application. Call it once before
the server starts.

## `inject`

```python
@inject
async def handler(service: FromDishka[Service]) -> Result: ...
```

Resolves `FromDishka` parameters, removes them from the public signature, and
manages one `Scope.REQUEST` around the handler. Sync and async functions are
detected automatically.

## `dishka_lifespan`

```python
dishka_lifespan(container) -> Callable[..., AbstractAsyncContextManager[None]]
```

Returns a FastMCP lifespan that closes an async or sync root container during
server shutdown.

## `FastMCPProvider`

A Dishka provider for the current `Context`, `FastMCP` server, and raw MCP
request parameter objects. Add an instance when constructing the container.

## `FromDishka`

Re-export of Dishka's dependency marker. It is provided here so handlers can
import their entire integration surface from one package.

## `DishkaMiddleware`

The middleware registered by `setup_dishka`. Direct construction is available
for applications that need explicit middleware ordering.

## `DishkaFastMCPError`

Raised for integration misuse, including a missing middleware context or a
container type that does not match the handler's sync or async execution model.
