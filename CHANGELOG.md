# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/bagowix/dishka-fastmcp/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/bagowix/dishka-fastmcp/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/bagowix/dishka-fastmcp/releases/tag/v1.0.0
