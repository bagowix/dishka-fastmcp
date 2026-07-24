# dishka-fastmcp

[![CI](https://github.com/bagowix/dishka-fastmcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bagowix/dishka-fastmcp/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/bagowix/dishka-fastmcp/python-coverage-comment-action-data/badge.svg)](https://github.com/bagowix/dishka-fastmcp/tree/python-coverage-comment-action-data)
[![PyPI](https://img.shields.io/pypi/v/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![Downloads](https://img.shields.io/pypi/dm/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![llms.txt](https://img.shields.io/badge/-llms.txt-brightgreen)](docs/llms.txt)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-2f6f55.svg)](https://bagowix.github.io/dishka-fastmcp/)
[![Context7](https://img.shields.io/badge/docs-Context7-1f6feb.svg)](https://context7.com/bagowix/dishka-fastmcp)

[dishka](https://github.com/reagento/dishka) IoC container integration for
[FastMCP](https://github.com/prefecthq/fastmcp). Declare dependencies as
`FromDishka[Service]` in MCP tools, resources and prompts and let dishka resolve
them per request — with real scopes, finalization and modular providers, sharing
one container with the rest of your application.

```python
from dishka import Provider, Scope, make_async_container, provide
from fastmcp import FastMCP

from dishka_fastmcp import FromDishka, dishka_lifespan, inject, setup_dishka


class Catalog:
    _prices: dict[str, int] = {'book': 12, 'pen': 2}

    def price(self, item: str) -> int:
        return self._prices.get(item, 0)


class AppProvider(Provider):
    catalog = provide(Catalog, scope=Scope.REQUEST)


container = make_async_container(AppProvider())
mcp = FastMCP('shop', lifespan=dishka_lifespan(container))
setup_dishka(container, mcp)


@mcp.tool
@inject
async def get_price(item: str, catalog: FromDishka[Catalog]) -> int:
    return catalog.price(item)


if __name__ == '__main__':
    mcp.run()
```

The client sees a tool that takes only `item` — `catalog` is injected at call
time and never appears in the schema.

## Install

```bash
uv add dishka-fastmcp        # or: pip install dishka-fastmcp
```

Requires Python 3.11+, `dishka>=1.10.1`, `fastmcp>=3.2.4,<4`.

## How it works

Registration time and execution time are separate concerns:

- **`@inject` sits below the FastMCP decorator.** `@mcp.tool` builds the JSON
  schema from the function signature. `@inject` rewrites that signature first,
  stripping every `FromDishka` parameter, so the schema the LLM sees contains
  only the real client-facing arguments. **Order matters** — `@inject` must be
  the inner decorator.
- **`setup_dishka` associates the root container with the FastMCP application.**
  `@inject` selects that container from the application handling the current
  operation, then opens and finalizes `Scope.REQUEST` around the handler. For a
  sync handler, dependency setup, use, and cleanup all happen in its worker
  thread.

## Scopes

| Scope | Boundary | Lifetime |
|-------|----------|----------|
| `Scope.APP` | The whole server | Owned by the root container; **you** close it on shutdown (see below) |
| `Scope.REQUEST` | One tool call / resource read / prompt render | Opened and finalized by `@inject` around the handler |

`Scope.SESSION` is **intentionally not supported.** FastMCP does not provide a
deterministic teardown boundary for a Dishka session container. Without that
boundary, session-scoped resources could not be finalized reliably.

### Closing the container

`setup_dishka` does not own the server lifecycle, so it does not close the root
container. `dishka_lifespan(container)` — used in the example above — closes it
(async or sync) and removes its application registration when the server stops,
finalizing every `Scope.APP` provider. If you already have a lifespan, compose
it with `dishka_lifespan` using FastMCP's `combine_lifespans`. For multiple
FastMCP servers hosted by one ASGI application, combine each
`mcp.http_app().lifespan`; see
[Lifecycle and scopes](https://bagowix.github.io/dishka-fastmcp/lifecycle/).

FastMCP may execute regular sync handlers on different worker threads. Consequently,
`Scope.APP` dependencies in a sync container must be thread-safe and their
cleanup must not require the thread that created them. Put thread-affine resources
such as `sqlite3.Connection` in `Scope.REQUEST`, where dishka-fastmcp guarantees
creation, use and finalization in one worker thread.

### Background tasks

FastMCP's `task=True` handlers are **not supported**. The tool call returns as
soon as the work is queued, so the request — and with it the REQUEST scope — is
already over by the time the worker runs the handler. Injection there fails with
`DishkaFastMCPError`. Keep `FromDishka` handlers request-bound; if you need
background work, resolve dependencies inside the request and pass plain values
to the task.

## Resources and prompts

`@inject` works the same on resources and prompts — dependencies are resolved
per operation:

```python
@mcp.resource('users://{user_id}')
@inject
async def user(user_id: str, repo: FromDishka[UserRepo]) -> dict:
    return await repo.get(user_id)


@mcp.prompt
@inject
async def summarize(text: str, summarizer: FromDishka[Summarizer]) -> str:
    return await summarizer.run(text)
```

## Sync handlers

FastMCP runs regular sync handlers in a worker thread. Use a **sync** container
(`make_container`) for them; the REQUEST scope is created and finalized in that
same thread:

```python
from dishka import make_container

container = make_container(AppProvider())
setup_dishka(container, mcp)


@mcp.tool
@inject
def compute(x: int, service: FromDishka[Calculator]) -> int:
    return service.square(x)
```

Async handlers need an async container (`make_async_container`); mixing the two
raises a clear error.

## Return values

An ordinary `def` or `async def` handler must return its completed value.
Returning an awaitable, generator, or async generator is rejected because that
work would outlive its REQUEST scope. FastMCP tool handlers defined directly as
sync or async generator functions are supported; their scope stays open for the
whole iteration.

## Accessing FastMCP objects

Add `FastMCPProvider` to expose the current request's FastMCP objects to your
dependencies via dishka's `from_context`:

```python
from fastmcp import Context

from dishka_fastmcp import FastMCPProvider

container = make_async_container(AppProvider(), FastMCPProvider())


@mcp.tool
@inject
async def notify(message: str, ctx: FromDishka[Context]) -> None:
    await ctx.info(message)
```

`FastMCPProvider` also exposes the active `FastMCP` server.

## How this compares

### vs `fastmcp.dependencies.Depends`

FastMCP's own `Depends` injects a per-call value and hides it from the schema —
enough for simple cases. dishka adds what `Depends` does not have: **scopes with
finalization**, **modular providers**, and **one container shared with the rest
of your app** (the same graph that feeds your FastAPI or FastStream code can feed
your MCP server). Reach for dishka-fastmcp when your MCP server is part of a
larger dishka application, or when your dependencies own resources that must be
set up and torn down per request.

### Relationship to [`fastmcp-dishka`](https://github.com/vfaddey/fastmcp-dishka)

`fastmcp-dishka` (Apache-2.0) is an earlier independent implementation of the
same core use case. This package uses a different lifecycle model:

- **Scope ownership.** `@inject` opens and closes the request scope where the
  handler runs, including FastMCP's sync worker thread. Thread-affine `REQUEST`
  dependencies are therefore created and finalized on the same thread.
- **Supported boundaries.** `APP` and `REQUEST` are supported. `SESSION` is not
  exposed because FastMCP does not provide a deterministic session teardown
  boundary. Background-task handlers are rejected because they outlive the
  originating request.
- **Container lookup.** The active container is associated with its owning
  FastMCP application and resolved through FastMCP's public operation context.

## License

MIT — see [LICENSE](LICENSE).
