# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Corrected the documentation examples to close the root container through the
  FastMCP lifespan and clarified that `Scope.APP` is supported by both async and
  sync root containers.

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

[Unreleased]: https://github.com/bagowix/dishka-fastmcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/bagowix/dishka-fastmcp/releases/tag/v1.0.0
