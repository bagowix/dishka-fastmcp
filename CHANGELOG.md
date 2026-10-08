# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `DishkaMiddleware` opens one `Scope.REQUEST` for the whole MCP request, and
  `get_request_container()` returns it. Until now the scope existed only inside
  an `@inject` handler, so a dynamic component provider that builds `tools/list`
  per user or feature flag, a tool assembled from `FunctionTool(fn=...)` with a
  dependency type known only at runtime, and middleware had no REQUEST
  dependencies. Put `DishkaMiddleware()` first in `FastMCP(middleware=[...])`:
  every request, list requests and the handshake included, gets a scope, and
  async `@inject` handlers share it, so each REQUEST dependency has one instance
  per request, finalized once before the response goes out. Mounted routers at
  any depth share it too. If you open the scope in a middleware of your own and
  keep the request container in a ContextVar, replace that middleware with
  `DishkaMiddleware`. Without the middleware nothing changes.
- The middleware needs an `AsyncContainer`. Sync handlers keep their own
  worker-thread scope, a router set up with a different container keeps its own
  scopes and gets no request container, and `task=True` workers get no scope.
  FastMCP also lists component providers outside any request, on startup, and
  runs completion and extension-method handlers outside the scope;
  `get_request_container()` raises `DishkaFastMCPError` there, so a provider
  returns no components on startup. The name `DishkaMiddleware`
  belonged to a different class removed in 2.0.0, which only carried the root
  container; the new one owns the request scope.
- Three things behave differently once you add the middleware. The scope
  closes after the handler produced its result, so a finalizer that raises
  fails the whole request with a protocol error, which FastMCP may mask as
  `Internal server error`; without the middleware it is a tool error.
  `FromDishka[FastMCP]` in a mounted router's handler resolves to the server
  whose middleware opened the scope; without the middleware it stays the
  router. And the shared scope has a lock, as dishka's APP scope does: lookups
  in one request run one at a time, and a factory that resolves through its
  container from a task of its own, for example under `asyncio.gather`, never
  returns.

## [3.0.0] - 2026-10-08

### Added

- Routers mounted into a server now use that server's container. Before, every
  mounted router needed its own `setup_dishka` call: with setup only on the root,
  a mounted component failed with "No dishka container for the active FastMCP
  application", because FastMCP makes the mounted server the active application
  while its component runs. Give the root `dishka_lifespan(container)` and
  `setup_dishka(container, root)`, and routers mounted at any depth, later
  mounts and mounts with `namespace` or `tool_names` resolve from the root's
  container. A router mounted into several servers uses the container of the
  server serving the request.
- The container reaches mounted routers through MCP requests. A direct
  `server.call_tool()` outside a request still reaches only the server's own
  components, so test mounted routers through `Client(server)`. A server behind
  `create_proxy` needs its own `setup_dishka`.
- `dishka_lifespan(container, finalize_container=False)` leaves closing the
  container to its owner. The name follows `finalize_container` in dishka's own
  integrations, and it defaults to `True`, so the lifespan keeps closing the
  container unless told otherwise. Use it when the container is shared with
  FastAPI, FastStream, a worker or a test session. Routers without a container
  of their own get the root's container only through `dishka_lifespan`, which
  otherwise closes it when the MCP server stops. With `finalize_container=False`
  the lifespan still registers the container and hands it to mounted routers,
  and a registration that `setup_dishka` made before startup survives the
  shutdown.
- The lifecycle guide now covers the lifespan order for a shared container.
  FastMCP's FastAPI guide combines `app_lifespan` before `mcp_app.lifespan`, so
  the container closes first and `app_lifespan` shuts down against a closed
  container. Dishka raises no error there: it creates the `Scope.APP`
  dependencies again, and they leak unless the container is closed once more.
  List `mcp_app.lifespan` first, or pass `finalize_container=False` and close the
  container where it is owned.

### Changed

- dishka-fastmcp now requires FastMCP 4 (`fastmcp>=4.0.0,<5`); FastMCP 3.x is no
  longer supported. If your server still runs on FastMCP 3, stay on
  dishka-fastmcp 2.0.x and migrate the server first with FastMCP's
  [upgrade guide](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3).
  The functions and classes this package exports keep their names and
  arguments; the return type of `dishka_lifespan` changed (see below). Injection
  works on both protocol eras FastMCP 4 speaks: the sessionless 2026-07-28 era
  its `Client` negotiates by default, and the legacy handshake.
- `dishka_lifespan` now returns a FastMCP `Lifespan`
  (`fastmcp.server.lifespan.Lifespan`) whose lifespan state is
  `{'dishka_fastmcp.container': container}`. It composes with `@lifespan`
  functions through `|`, and through `combine_lifespans` with lifespans that
  yield a mapping or `None`. To use `|` with an `@asynccontextmanager` lifespan,
  wrap that lifespan in FastMCP's `ContextManagerLifespan`. A `Lifespan` is
  still called with the server and returns an async context manager, so an
  annotation such as
  `Callable[[FastMCP], AbstractAsyncContextManager[dict[str, Any]]]` accepts it.
  Code that annotated it with `AbstractAsyncContextManager[None]` should
  annotate it as `Lifespan` or as that `Callable` instead.
- `dishka_lifespan` now also registers its container for the application on
  startup, as `setup_dishka` does. A server whose lifespan includes it resolves
  its own components during MCP requests even without `setup_dishka`; keep
  calling `setup_dishka` for direct `call_tool()` calls outside the lifespan.
- `task=True` handlers are still rejected with `DishkaFastMCPError`. FastMCP 4
  moved background tasks into the optional `fastmcp-tasks` extension, and
  dishka-fastmcp recognizes its workers without importing it, so `fastmcp[tasks]`
  stays an opt-in for your server. The error message no longer claims the request
  scope has already ended: `@inject` owns its REQUEST scope, and the actual reason
  is that the handler runs in a worker outside the request that queued it.

## [2.0.1] - 2026-08-08

### Fixed

- A sync handler that returns an async generator now has that generator closed
  before `DishkaFastMCPError` is raised. Every other rejected deferred result was
  already closed on both the sync and the async path; this one case was left
  dangling, so the generator stayed open until the garbage collector reached it.
  Nothing observable broke — an unstarted async generator holds no resources —
  but rejection now finalizes what it rejects, without exception.

## [2.0.0] - 2026-07-24

### Changed

- `setup_dishka` now associates the container directly with its FastMCP
  application. The active application selects its own container for each
  operation, and an application-container reference cycle can be collected
  normally.
- `dishka_lifespan` now fails fast on startup if it was given a different
  container than the one registered via `setup_dishka` — previously the
  registered container would silently stay open after shutdown — and drops the
  registration on shutdown, so calls after shutdown report a missing setup
  instead of resolving a closed container.

### Removed

- Removed the public `DishkaMiddleware` class. Use
  `setup_dishka(container, app)` for registration.
- `FastMCPProvider` no longer provides `CallToolRequestParams`,
  `ReadResourceRequestParams`, or `GetPromptRequestParams`. Receive operation
  arguments through the component signature and use `fastmcp.Context` for
  request metadata.

### Fixed

- A handler — sync or async — that returns an awaitable, generator, or async
  generator is now rejected with `DishkaFastMCPError`. FastMCP consumes such
  deferred results after the handler returns, which is after `@inject` has
  finalized the REQUEST scope. Tool handlers defined as generators (sync or
  async) remain supported and keep the scope open for the whole iteration.
  Rejected coroutine-like objects are closed, and returned `asyncio.Task`
  instances are cancelled and awaited, before the scope is finalized.
- Corrected the documentation examples to close the root container through the
  FastMCP lifespan and clarified that `Scope.APP` is supported by both async and
  sync root containers.

### Added

- Added a GitHub Pages documentation site with a Context7 chat widget and links
  to both documentation sources from the project README.

## [1.0.0] - 2026-07-21

First release. dishka dependency injection for FastMCP tools, resources and
prompts.

### Added

- `setup_dishka(container, mcp)` — registers the middleware that publishes the
  root container and current FastMCP objects for every tool call, resource read
  and prompt render. Accepts an `AsyncContainer` for async handlers or a
  `Container` for sync handlers; `@inject` owns the REQUEST scope.
- `@inject` — resolves `FromDishka[...]` parameters and strips them from the
  signature, so they never leak into the tool schema. Auto-detects sync vs async
  handlers. Must be placed below the FastMCP decorator.
- `FastMCPProvider` — exposes the current request's FastMCP objects to
  dependencies via `from_context`: the `Context`, the `FastMCP` server, and the
  raw request params (`CallToolRequestParams`, `ReadResourceRequestParams`,
  `GetPromptRequestParams`).
- `dishka_lifespan(container)` — builds a FastMCP lifespan that closes the root
  container on shutdown, finalizing every `Scope.APP` provider. Works with async
  and sync containers.
- `FromDishka` re-exported for a single import site.
- Full typing surface (`py.typed`), checked by mypy and pyright in strict mode.

### Notes

- Only `Scope.APP` and `Scope.REQUEST` are supported. FastMCP has no
  session-teardown hook, so a `Scope.SESSION` container could not be finalized
  deterministically; it is deliberately omitted rather than shipped as a scope
  that silently behaves like `REQUEST`.
- The REQUEST scope is entered and finalized by `@inject`, inside the thread that
  runs the handler. FastMCP executes sync tools in a worker thread, so a scope
  managed on the event loop would create a thread-affine dependency (such as a
  `sqlite3` connection) in the worker and finalize it on the loop, raising on
  cleanup. Sync handlers therefore create, use and release their REQUEST-scoped
  dependencies in one thread.
- APP-scoped dependencies in a sync container must be thread-safe and have
  thread-independent cleanup because FastMCP may run handlers on different worker
  threads and the root container is closed from the server lifespan. Thread-affine
  resources belong in `Scope.REQUEST`.
- Handlers registered with FastMCP's `task=True` are not supported: they run after
  the request has finished, so no container is in scope for them.

[Unreleased]: https://github.com/bagowix/dishka-fastmcp/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/bagowix/dishka-fastmcp/compare/v2.0.1...v3.0.0
[2.0.1]: https://github.com/bagowix/dishka-fastmcp/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/bagowix/dishka-fastmcp/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/bagowix/dishka-fastmcp/releases/tag/v1.0.0
