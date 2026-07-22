# Lifecycle and scopes

dishka-fastmcp separates FastMCP registration from operation execution. The
decorator cleans the public signature during registration. At execution time,
middleware publishes the root container and `@inject` owns the request scope.

## Scope boundaries

| Scope | Boundary | Owner |
|---|---|---|
| `Scope.APP` | Server lifetime | Root container, closed by the FastMCP lifespan |
| `Scope.REQUEST` | One tool call, resource read, or prompt render | `@inject` |

Use `dishka_lifespan` when the FastMCP server owns the container:

```python
container = make_async_container(AppProvider())
mcp = FastMCP('app', lifespan=dishka_lifespan(container))
```

If an application already has a lifespan, close the root container in its
shutdown path instead.

## Sync request finalization

FastMCP offloads sync handlers to worker threads. `@inject` opens and closes the
Dishka request container inside the wrapped handler, so thread-affine resources
stay in one thread:

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

## Deliberate limitations

`Scope.SESSION` is not supported. FastMCP has an initialization hook but no
deterministic session teardown hook, so a real session scope cannot be finalized
correctly.

Handlers registered with `task=True` are also unsupported. Background execution
starts after the originating request context has ended. Resolve dependencies
during the request and pass plain values to background work.
