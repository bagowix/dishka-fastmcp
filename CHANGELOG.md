# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-07-21

First release. dishka dependency injection for FastMCP tools, resources and
prompts.

### Added

- `setup_dishka(container, mcp)` — registers the middleware that opens a
  `Scope.REQUEST` container around every tool call, resource read and prompt
  render, and finalizes it when the operation ends. Accepts an `AsyncContainer`
  for async handlers or a `Container` for sync handlers.
- `@inject` — resolves `FromDishka[...]` parameters and strips them from the
  signature, so they never leak into the tool schema. Auto-detects sync vs async
  handlers. Must be placed below the FastMCP decorator.
- `FastMCPProvider` — exposes the current request's FastMCP objects to
  dependencies via `from_context`: the `Context`, the `FastMCP` server, and the
  raw request params (`CallToolRequestParams`, `ReadResourceRequestParams`,
  `GetPromptRequestParams`).
- `FromDishka` re-exported for a single import site.
- Full typing surface (`py.typed`), checked by mypy and pyright in strict mode.

### Notes

- Only `Scope.APP` and `Scope.REQUEST` are supported. FastMCP has no
  session-teardown hook, so a `Scope.SESSION` container could not be finalized
  deterministically; it is deliberately omitted rather than shipped as a scope
  that silently behaves like `REQUEST`.

[Unreleased]: https://github.com/bagowix/dishka-fastmcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/bagowix/dishka-fastmcp/releases/tag/v1.0.0
