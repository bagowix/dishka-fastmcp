# dishka-fastmcp

[![PyPI](https://img.shields.io/pypi/v/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![CI](https://github.com/bagowix/dishka-fastmcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bagowix/dishka-fastmcp/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/bagowix/dishka-fastmcp/python-coverage-comment-action-data/badge.svg)](https://github.com/bagowix/dishka-fastmcp/tree/python-coverage-comment-action-data)
[![Downloads](https://img.shields.io/pypi/dm/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/dishka-fastmcp.svg)](https://pypi.org/project/dishka-fastmcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/bagowix/dishka-fastmcp/blob/main/LICENSE)
[![llms.txt](https://img.shields.io/badge/-llms.txt-brightgreen)](https://bagowix.github.io/dishka-fastmcp/llms.txt)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-2f6f55.svg)](https://bagowix.github.io/dishka-fastmcp/)
[![Context7](https://img.shields.io/badge/docs-Context7-1f6feb.svg)](https://context7.com/bagowix/dishka-fastmcp)
[![skills.sh](https://skills.sh/b/bagowix/dishka-fastmcp)](https://skills.sh/bagowix/dishka-fastmcp)

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

Requires Python 3.11+, `dishka>=1.10.1`, `fastmcp>=4.0.0,<5` and `mcp>=2.0.0,<3`.
Servers still on FastMCP 3.x should stay on dishka-fastmcp 2.0.x.

## With a coding agent

The repository ships an [Agent Skill](https://agentskills.io/) that walks a
coding agent through adding dishka-fastmcp to a FastMCP server: the container
and lifespan, decorator order, sync and async handlers, mounted routers, the
request-wide scope, and tests that assert the tool schema. It installs into
Claude Code, Cursor, Codex, GitHub Copilot, Gemini CLI and the other agents
that read the open skills format:

```bash
npx skills add bagowix/dishka-fastmcp
```

Agents that read documentation directly can use
[llms.txt](https://bagowix.github.io/dishka-fastmcp/llms.txt) or
[Context7](https://context7.com/bagowix/dishka-fastmcp).

## How it works

Registration time and execution time are separate concerns:

- **`@inject` sits below the FastMCP decorator.** `@mcp.tool` builds the JSON
  schema from the function signature. `@inject` rewrites that signature first,
  stripping every `FromDishka` parameter, so the schema the LLM sees contains
  only the real client-facing arguments. **Order matters** — `@inject` must be
  the inner decorator. Placed above, it fails at import: FastMCP has
  registered the bare function by then, and the client would fill in the
  dependency itself.
- **`setup_dishka` associates the root container with the FastMCP application.**
  `@inject` selects that container from the application handling the current
  operation, then opens and finalizes `Scope.REQUEST` around the handler, or
  shares the scope `DishkaMiddleware` opened for the whole request. For a sync
  handler, dependency setup, use, and cleanup all happen in its worker thread.
- **Mounted routers use the serving server's container.** `dishka_lifespan`
  puts the container into the lifespan state of the server you serve, and routers
  mounted into it at any depth read it from there during MCP requests. A router
  mounted into several servers uses the container of the one serving the
  request. A router with its own `setup_dishka` keeps its own container, and so
  does one with its own `dishka_lifespan` if it is mounted before the server
  starts. Routers below such a router still take the serving root's. Direct
  `server.call_tool()` calls outside an MCP request and servers behind
  `create_proxy` are not covered; see
  [Mounted servers](https://bagowix.github.io/dishka-fastmcp/lifecycle/#mounted-servers).

## Scopes

| Scope | Boundary | Lifetime |
|-------|----------|----------|
| `Scope.APP` | The whole server | Owned by the root container; `dishka_lifespan` closes it on shutdown, or its owner does (see below) |
| `Scope.SESSION` | One MCP connection | Opt-in: opened by `DishkaMiddleware(session_scope=True)`, finalized when the connection closes |
| `Scope.REQUEST` | One tool call / resource read / prompt render | Opened and finalized by `@inject` around the handler, or by `DishkaMiddleware` around the whole MCP request |

`Scope.SESSION` follows the MCP connection, so it spans several requests only
where a connection does: 2025 handshake clients over stdio, stateful Streamable
HTTP, SSE or an in-memory `Client(server, mode='legacy')`. The 2026-07-28 protocol, which FastMCP 4's own
`Client` speaks by default, and stateless HTTP build a fresh connection for every
request, and there a SESSION dependency lives for one request. See
[Session scope](https://bagowix.github.io/dishka-fastmcp/lifecycle/#session-scope-for-the-mcp-connection).

### Closing the container

`setup_dishka` does not own the server lifecycle, so it does not close the root
container. `dishka_lifespan(container)` — used in the example above — closes it
(async or sync) and removes its application registration when the server stops,
finalizing every `Scope.APP` provider. If you already have a lifespan, compose
it with `dishka_lifespan` using FastMCP's `combine_lifespans`, or through `|`
when it is a FastMCP `@lifespan` function. For multiple
FastMCP servers hosted by one ASGI application, combine each
`mcp.http_app().lifespan`; see
[Lifecycle and scopes](https://bagowix.github.io/dishka-fastmcp/lifecycle/).

If the same container also serves FastAPI, FastStream, a worker or a test
session, the MCP server must not close it while they still use it. List
`mcp.http_app().lifespan` first in `combine_lifespans`, or pass
`dishka_lifespan(container, finalize_container=False)` and close the container
where it is owned; see
[Sharing the container](https://bagowix.github.io/dishka-fastmcp/lifecycle/#sharing-the-container).

FastMCP may execute regular sync handlers on different worker threads. Consequently,
`Scope.APP` dependencies in a sync container must be thread-safe and their
cleanup must not require the thread that created them. Put thread-affine resources
such as `sqlite3.Connection` in `Scope.REQUEST`, where dishka-fastmcp guarantees
creation, use and finalization in one worker thread.

### Background tasks

FastMCP's `task=True` handlers are **not supported**. In FastMCP 4 they come from
the optional `fastmcp-tasks` extension and run in a task worker, detached from the
request that queued them and possibly in another process. `@inject` detects the
worker and raises `DishkaFastMCPError` there. Keep `FromDishka` handlers
request-bound; if you need background work, resolve dependencies inside the
request and pass plain values to the task.

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
mcp = FastMCP('sync', lifespan=dishka_lifespan(container))
setup_dishka(container, mcp)


@mcp.tool
@inject
def get_price_sync(item: str, catalog: FromDishka[Catalog]) -> int:
    return catalog.price(item)
```

Async handlers need an async container (`make_async_container`); mixing the two
raises a clear error.

## Return values

An ordinary `def` or `async def` handler must return its completed value.
Returning an awaitable, generator, or async generator is rejected: FastMCP would
run that work after the handler returns, when its REQUEST scope may already be
finalized. FastMCP tool handlers defined directly as sync or async generator
functions are supported; their scope stays open for the whole iteration.

## Accessing FastMCP objects

Add `FastMCPProvider` to expose the current request's FastMCP objects to your
dependencies via dishka's `from_context`:

```python
from fastmcp import Context

from dishka_fastmcp import FastMCPProvider

container = make_async_container(AppProvider(), FastMCPProvider())


@mcp.tool
@inject
async def request_id(ctx: FromDishka[Context]) -> str:
    return ctx.request_id
```

`FastMCPProvider` also exposes the active `FastMCP` server.

## Request scope for the whole request

`@inject` scopes one handler call. To give dynamic component providers,
`FunctionTool` callables and middleware the same REQUEST dependencies, add
`DishkaMiddleware` first in the middleware list and resolve through
`get_request_container()`:

```python
from dishka_fastmcp import DishkaMiddleware, get_request_container

mcp = FastMCP(
    'shop',
    lifespan=dishka_lifespan(container),
    middleware=[DishkaMiddleware()],
)


async def current_user() -> User:
    return await get_request_container().get(User)
```

Every request then gets one scope, list requests included, shared by async
`@inject` handlers and finalized once before the response goes out. It needs an
`AsyncContainer`. Without the middleware nothing changes; see
[Request scope for the whole MCP request](https://bagowix.github.io/dishka-fastmcp/lifecycle/#request-scope-for-the-whole-mcp-request)
for the rules.

## How this compares

### vs `fastmcp.dependencies.Depends`

FastMCP's own `Depends` injects a per-call value and hides it from the schema —
enough for simple cases. dishka adds what `Depends` does not have: **scopes with
finalization**, **modular providers**, and **one container shared with the rest
of your app** (the same graph that feeds your FastAPI or FastStream code can feed
your MCP server). Reach for dishka-fastmcp when your MCP server is part of a
larger dishka application, or when your dependencies own resources that must be
set up and torn down per request.

### `fastmcp-dishka`

[`fastmcp-dishka`](https://github.com/vfaddey/fastmcp-dishka) is an earlier,
independent package with a similar name and the same goal. The two are not
interchangeable: install one, and import `dishka_fastmcp` for this one. When
moving over, add `dishka_lifespan(container)` to the server and check its
`Scope.SESSION` providers: here a SESSION dependency is rebuilt for every request
unless you add
[`DishkaMiddleware(session_scope=True)`](https://bagowix.github.io/dishka-fastmcp/lifecycle/#session-scope-for-the-mcp-connection),
and even then it spans requests only on 2025 handshake connections.

## License

MIT — see [LICENSE](https://github.com/bagowix/dishka-fastmcp/blob/main/LICENSE).
