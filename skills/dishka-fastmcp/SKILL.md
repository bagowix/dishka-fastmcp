---
name: dishka-fastmcp
description: Inject dishka dependencies into FastMCP 4 tools, resources and prompts with dishka-fastmcp (import dishka_fastmcp), with per-request scopes, finalization and one container shared with FastAPI or FastStream. Covers container and lifespan wiring, decorator order, sync and async handlers, mounted routers, the FastMCP Context, a request scope for the whole MCP request and a session scope per MCP connection with DishkaMiddleware, tests through an in-memory Client, and moving from fastmcp.dependencies.Depends or the fastmcp-dishka package. Use when a FastMCP server needs dependency injection, when a project already on dishka adds an MCP server, or when the user mentions dishka-fastmcp, FromDishka in an MCP tool, or dishka with FastMCP.
license: MIT
metadata:
  author: bagowix
  docs: https://bagowix.github.io/dishka-fastmcp/
---

# dishka-fastmcp: dishka dependencies in FastMCP handlers

dishka-fastmcp connects the [dishka](https://dishka.readthedocs.io/) IoC container to [FastMCP](https://gofastmcp.com/). A handler declares `svc: FromDishka[Service]`; dishka resolves it in a `Scope.REQUEST` container that is finalized when the tool call, resource read or prompt render ends, and the MCP client never sees `svc` in the schema.

The library is recent, so your training data most likely predates it. Do not guess the API: every symbol you need is in this file, and the documentation links at the end cover the rest.

## Ground rules

- Package `dishka-fastmcp`, import `dishka_fastmcp`. It is a different project from `fastmcp-dishka` (import `fastmcp_dishka`), which has similar names and a different lifecycle; never install or mix both.
- Python 3.11+, `dishka>=1.10.1`, `fastmcp>=4.0.0,<5`, `mcp>=2.0.0,<3` (installed by FastMCP). A server on FastMCP 3.x needs `dishka-fastmcp<3` (the 2.0.x line); say so and stop if the project cannot move.
- Scopes: `Scope.APP` (server lifetime) and `Scope.REQUEST` (one operation). `Scope.SESSION` (one MCP connection) needs `DishkaMiddleware(session_scope=True)`, and spans several requests only for 2025 handshake clients over stdio, stateful HTTP, SSE or `Client(server, mode='legacy')`. The 2026-07-28 protocol (FastMCP 4's default client) and stateless HTTP get one connection per request, so SESSION lives one request there. State that must survive between calls goes into FastMCP's session state (`session_id: SessionId`, `UserSession`), not into a cache keyed by session id.
- `@inject` goes **below** `@mcp.tool` / `@mcp.resource` / `@mcp.prompt`. It strips `FromDishka` parameters from the signature before FastMCP builds the JSON schema. The other order raises `DishkaFastMCPError` at import: FastMCP has already registered the bare function, so the client would fill in the service.
- Show the inventory and the plan to the user before editing code.

## Workflow

### 1. Inventory

Find and list:

- every `FastMCP(...)` server and every `mount(...)` call, with which server is actually served (`mcp.run()`, `http_app()`, an ASGI mount);
- the handlers that need services, and whether each is `def` or `async def`;
- an existing dishka container (FastAPI, FastStream, workers) and who closes it;
- existing lifespans, `fastmcp.dependencies.Depends`, `fastmcp_dishka` imports, module-level singletons the handlers reach for;
- handlers registered with `task=True` (see anti-patterns).

### 2. Pick the container kind

One server gets one root container, and its kind must match its injected handlers:

- async handlers need `make_async_container(...)`;
- sync handlers need `make_container(...)`. FastMCP runs them in a worker thread, and dishka-fastmcp opens, uses and finalizes their REQUEST scope in that thread.

A sync handler on an async container (or the reverse) raises `DishkaFastMCPError` at call time. If one server has both kinds, convert the injected handlers to one kind; async is the usual choice.

### 3. Wire the server

```python
from dishka import Provider, Scope, make_async_container, provide
from fastmcp import FastMCP

from dishka_fastmcp import FromDishka, dishka_lifespan, inject, setup_dishka


class AppProvider(Provider):
    catalog = provide(Catalog, scope=Scope.REQUEST)


container = make_async_container(AppProvider())
mcp = FastMCP('shop', lifespan=dishka_lifespan(container))
setup_dishka(container, mcp)


@mcp.tool
@inject
async def get_price(item: str, catalog: FromDishka[Catalog]) -> int:
    return catalog.price(item)  # the client sees only `item`
```

- `setup_dishka(container, mcp)` registers the container on the app. Call it once per server that owns a container.
- `dishka_lifespan(container)` closes the container on shutdown (finalizing `Scope.APP`) and hands it to mounted routers. It returns a FastMCP `Lifespan`: compose it with another `@lifespan` function through `|`, or with `combine_lifespans`.
- **Shared container.** If the same container serves FastAPI, FastStream, a worker or a test session, the MCP server must not close it first. Either list `mcp_app.lifespan` **first** in `combine_lifespans(mcp_app.lifespan, app_lifespan)` (FastMCP's own FastAPI guide lists it last, which closes the container too early), or pass `dishka_lifespan(container, finalize_container=False)` and close it where it is owned. dishka re-creates APP dependencies on `get()` after `close()` without an error, so a wrong order leaks resources silently.
- **Sync APP dependencies** must be thread-safe with thread-independent cleanup: FastMCP may use several worker threads. Thread-affine resources (a `sqlite3` connection) belong in `Scope.REQUEST`.

### 4. Convert the handlers

- Replace module-level singletons and per-handler construction with `FromDishka[T]` parameters and providers.
- Resources and prompts take `@inject` the same way: `@mcp.resource('users://{user_id}')` then `@inject`.
- Return a completed value. A handler that *returns* an awaitable, generator or async generator is rejected with `DishkaFastMCPError`, because FastMCP would consume it after the REQUEST scope closed. A handler *defined* as a generator is supported and keeps its scope open for the whole iteration.
- Tool and prompt arguments stay ordinary parameters; resource parameters come from the URI template.

### 5. FastMCP objects in dependencies

Add `FastMCPProvider()` to the container to resolve the current `fastmcp.Context` and `fastmcp.FastMCP`, in handlers or in providers:

```python
from typing import NewType

from fastmcp import Context

from dishka_fastmcp import FastMCPProvider

RequestId = NewType('RequestId', str)


class RequestProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def request_id(self, context: Context) -> RequestId:
        return RequestId(context.request_id)


container = make_async_container(AppProvider(), FastMCPProvider(), RequestProvider())
```

`Context` and `FastMCP` are REQUEST-scoped, so a `Scope.SESSION` or `Scope.APP` factory cannot take them; dishka rejects that graph when the container is built.

### 6. Mounted routers

Routers mounted into a served server whose lifespan includes `dishka_lifespan(container)` need no setup: during MCP requests they use the serving server's container, at any depth, with or without `namespace`. A router with its own `setup_dishka` keeps its own container. A router with its own `dishka_lifespan` keeps it only if it is mounted before the parent starts. A direct `server.call_tool()` outside an MCP request does not reach mounted routers; go through a `Client`.

### 7. A scope for the whole MCP request (optional)

`@inject` scopes one handler call. When code outside a handler needs REQUEST dependencies (a dynamic component provider that filters `tools/list` per user, a `FunctionTool(fn=...)` built at runtime, middleware), add `DishkaMiddleware()` **first** and resolve through `get_request_container()`:

```python
from dishka_fastmcp import DishkaFastMCPError, DishkaMiddleware, get_request_container

mcp = FastMCP('shop', lifespan=dishka_lifespan(container), middleware=[DishkaMiddleware()])


async def current_user() -> User:
    return await get_request_container().get(User)
```

- It needs an `AsyncContainer`. Async `@inject` handlers then share the request's scope, so a REQUEST dependency has one instance per MCP request. Sync handlers keep their own worker-thread scope.
- FastMCP also lists component providers outside any request (on startup), and runs completion and extension-method handlers outside the scope. `get_request_container()` raises `DishkaFastMCPError` there; a provider catches it and returns no components.
- The scope closes before the response is sent, so a slow finalizer adds latency, and a finalizer that raises fails the request as a protocol error instead of a tool error.
- `DishkaMiddleware(session_scope=True)` opens each REQUEST scope from a `Scope.SESSION` scope per MCP connection. It outlives a request only for 2025 handshake clients; test it with `Client(mcp, mode='legacy')`, and expect one SESSION per request under the default client. The SDK finalizes it when the connection closes and logs finalizer errors instead of raising them. Over stateful HTTP, set `mcp.http_app(session_idle_timeout=...)` or `FASTMCP_HTTP_SESSION_IDLE_TIMEOUT`: FastMCP 4.0, and 4.1 with `mcp` below 2.2, have no idle timeout, so a client that disappears without `DELETE` keeps its SESSION dependencies until the server restarts.

Without a concrete need from that list, or a `Scope.SESSION` dependency, skip the middleware.

### 8. Tests

- Build the container in the test from the same providers, overriding the ones that touch the outside world.
- Assert the schema for every converted handler: `(await mcp.list_tools())[i].parameters['properties']` holds only client-facing arguments.
- Call through `async with Client(mcp) as client: await client.call_tool(...)`, which covers mounted routers and the lifespan. Assert that REQUEST finalizers ran after the call.
- Each `async with Client(mcp)` block starts and stops the server, so with a container shared across blocks use `finalize_container=False` and close it in the fixture.

### 9. Moving from other approaches

- **`fastmcp.dependencies.Depends`**: keep it for trivial per-call values. Move to dishka where a dependency owns a resource that needs teardown, where providers should be modular, or where the container is shared with the rest of the application.
- **`fastmcp-dishka`**: uninstall it, change imports from `fastmcp_dishka` to `dishka_fastmcp`, add `dishka_lifespan(container)` to the server. Its `Scope.SESSION` providers were created per call; keep them in `Scope.SESSION` only with `DishkaMiddleware(session_scope=True)` and after checking which clients connect, else move them to `Scope.REQUEST` or `Scope.APP`.

## Anti-patterns to refuse

- `@inject` above the FastMCP decorator.
- A sync handler on an async container, or the reverse.
- `Scope.SESSION` without `DishkaMiddleware(session_scope=True)`, or a session cache that imitates it.
- A `Scope.SESSION` dependency that holds state the next call relies on, on a server that 2026-07-28 clients reach.
- `FromDishka` in a `task=True` handler. It runs in a `fastmcp-tasks` worker outside the request and raises; resolve dependencies in the request and pass plain values to the task.
- Returning a coroutine or generator object from an ordinary handler.
- A container shared with FastAPI or FastStream that the MCP lifespan closes first.
- Reading FastMCP private attributes or keeping the container in a ContextVar of your own.
- `get_request_container()` without `DishkaMiddleware`, or in a provider's startup listing without catching `DishkaFastMCPError`.

## Report back

End with the inventory, the container kind and lifespan chosen per server and why, the handlers converted, the schema assertions and tests added, and anything left out (sync handlers kept as they were, `task=True` handlers, `Depends` left in place).

## Documentation

- Index: <https://bagowix.github.io/dishka-fastmcp/llms.txt>
- Getting started: <https://bagowix.github.io/dishka-fastmcp/getting-started/>
- Lifecycle and scopes: <https://bagowix.github.io/dishka-fastmcp/lifecycle/>
- FastMCP context: <https://bagowix.github.io/dishka-fastmcp/context/>
- API reference: <https://bagowix.github.io/dishka-fastmcp/reference/>
