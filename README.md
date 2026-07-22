# dishka-fastmcp

[![CI](https://github.com/bagowix/dishka-fastmcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bagowix/dishka-fastmcp/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/bagowix/dishka-fastmcp/python-coverage-comment-action-data/badge.svg)](https://github.com/bagowix/dishka-fastmcp/tree/python-coverage-comment-action-data)
[![PyPI](https://img.shields.io/pypi/v/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![Downloads](https://img.shields.io/pypi/dm/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![llms.txt](https://img.shields.io/badge/-llms.txt-brightgreen)](docs/llms.txt)

[dishka](https://github.com/reagento/dishka) IoC container integration for
[FastMCP](https://github.com/jlowin/fastmcp). Declare dependencies as
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
- **`setup_dishka` registers a middleware** that publishes the root container
  and current FastMCP objects through `ContextVar`s. `@inject` then opens and
  finalizes `Scope.REQUEST` around the handler in the thread where it executes.
  This keeps sync dependency setup, use and cleanup in the same worker thread.

## Scopes

| Scope | Boundary | Lifetime |
|-------|----------|----------|
| `Scope.APP` | The whole server | Owned by the root container; **you** close it on shutdown (see below) |
| `Scope.REQUEST` | One tool call / resource read / prompt render | Opened and finalized by `@inject` around the handler |

`Scope.SESSION` is **intentionally not supported.** FastMCP's middleware exposes
a session-start hook (`on_initialize`) but no session-teardown hook, so a
session-lifetime container could never be finalized deterministically. Rather
than ship a SESSION scope that silently behaves like REQUEST, dishka-fastmcp
offers only the two scopes it can honor. If FastMCP adds a session-teardown
hook, SESSION support will follow.

### Closing the container

`setup_dishka` registers the middleware but does not own the server lifecycle, so
it does not close the root container. `dishka_lifespan(container)` — used in the
example above — closes it (async or sync) when the server stops, finalizing every
`Scope.APP` provider. If you already have your own lifespan, close the container
in its shutdown path instead (`await container.close()`, or `container.close()`
for a sync container).

FastMCP may execute sync handlers on different worker threads. Consequently,
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

## Sync tools

FastMCP runs sync tools in a worker thread (`run_in_thread=True` by default).
Use a **sync** container (`make_container`) for sync handlers; the request
container propagates into the worker thread correctly:

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

## Accessing FastMCP objects

Add `FastMCPProvider` to expose the current request's FastMCP objects to your
dependencies via dishka's `from_context`:

```python
from fastmcp.server.context import Context

from dishka_fastmcp import FastMCPProvider

container = make_async_container(AppProvider(), FastMCPProvider())


@mcp.tool
@inject
async def notify(message: str, ctx: FromDishka[Context]) -> None:
    await ctx.info(message)
```

`FastMCPProvider` also exposes the `FastMCP` server and the raw request params
(`CallToolRequestParams`, `ReadResourceRequestParams`, `GetPromptRequestParams`)
of the operation in flight.

## How this compares

### vs `fastmcp.dependencies.Depends`

FastMCP's own `Depends` injects a per-call value and hides it from the schema —
enough for simple cases. dishka adds what `Depends` does not have: **scopes with
finalization**, **modular providers**, and **one container shared with the rest
of your app** (the same graph that feeds your FastAPI or FastStream code can feed
your MCP server). Reach for dishka-fastmcp when your MCP server is part of a
larger dishka application, or when your dependencies own resources that must be
set up and torn down per request.

### vs [`fastmcp-dishka`](https://github.com/vfaddey/fastmcp-dishka)

`fastmcp-dishka` (Apache-2.0) is prior art covering the same surface, and both
packages build on dishka's public `wrap_injection` helper. Differences:

- **Scopes we can honor.** dishka-fastmcp offers `APP` and `REQUEST` only, for
  the reason above. `fastmcp-dishka` also exposes `SESSION`, which on current
  FastMCP resolves per request rather than per session.
- **No coupling to FastMCP internals.** The request container travels through a
  `ContextVar` this package owns, not a private FastMCP attribute.
- **Quality bar.** 100% test coverage, `mypy` and `pyright` in strict mode, and
  a CI matrix across Python 3.11–3.14.

## License

MIT — see [LICENSE](LICENSE).
