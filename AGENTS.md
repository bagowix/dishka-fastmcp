# AGENTS.md

dishka IoC container integration for FastMCP: `FromDishka[Service]` in MCP tools,
resources and prompts, with real scopes, finalization and modular providers. This
is a thin, correct bridge between two libraries — the bar for simplicity, typing
and not coupling to either library's internals is high.

## Decision-making principles

- Start from the problem: understand it first, pick tools second.
- Occam's razor: no new entities or abstractions without necessity.
- Never break existing functionality while making a change.
- A solution must be simple, reliable and readable without extra explanation.
- Build on public extension points, not private internals of dishka or FastMCP.
  The one exception is the MCP SDK's `ServerSession._connection` (see Scopes).
- Write code, docstrings and docs in English.

## Technology

- **Python 3.11+** — the minimum supported version; CI runs 3.11–3.15.
- **dishka** (`>=1.10.1`), **FastMCP** (`>=4.0.0,<5`) and the MCP SDK **mcp**
  (`>=2.0.0,<3`, imported directly by `middleware.py`) — the three runtime
  dependencies. FastMCP moves fast; the `deps-min` CI job pins all three floors
  (`mcp==2.0.0` with `mcp-types==2.0.0`) and runs the suite on them.
- **uv** — package manager. **hatchling** — build backend; the version is
  **static** in `dishka_fastmcp/version.py` (`[tool.hatch.version] path`), bumped
  manually on release and exposed as `dishka_fastmcp.__version__`.
- **ruff** — formatting and linting (`select = ["ALL"]`, targeted ignores).
- **mypy + pyright** in strict mode — static analysis, scoped to the package.
- **pytest** + `pytest-asyncio`, `pytest-cov`. Coverage threshold is 100%.

## Architecture

Registration and execution are separate concerns.

- **Registration:** `@inject` goes *below* the FastMCP decorator. `wrap_injection`
  (dishka's public helper) strips `FromDishka` params from `__signature__`, so it
  must run before `@mcp.tool` builds the JSON schema. The other order raises
  `DishkaFastMCPError` at import: `inject` finds FastMCP's `__fastmcp__` marker
  on a function the decorator has already registered bare.
- **Setup:** `setup_dishka(container, app)` stores the root container on the
  FastMCP application itself, so the two share one lifetime and are collected
  together. `dishka_lifespan(container)` closes it on shutdown and drops the
  registration; `finalize_container=False` leaves closing to the container's
  owner (a shared container) and keeps a registration `setup_dishka` made
  before startup. It returns a FastMCP `Lifespan`, so it composes with `|`.
- **Execution:** `@inject` resolves the active application through FastMCP's
  public `get_server()`, takes its registered container, and supplies the
  operation's FastMCP objects to the scope via `provide_context`.
- **Mounted servers:** an app without its own container takes the one
  `dishka_lifespan` put into the lifespan state of the server serving the MCP
  request (`get_context().request_context.lifespan_context`), so a router shared
  by several servers resolves per request. Direct `call_tool` outside an MCP
  request does not reach mounted routers. No library-owned ContextVars, no
  transport middleware, no FastMCP internals.
- **Request scope middleware (opt-in):** `DishkaMiddleware` opens one REQUEST
  scope per MCP request and keeps it as an attribute on FastMCP's per-request
  context object (`get_context().request_context`), never in a ContextVar.
  `get_request_container()` returns it; async `@inject` handlers share it unless
  their server was set up with a different container; sync handlers keep their
  own worker-thread scope; `get_request_container()` raises on a server with a
  different container of its own. AsyncContainer only, opened with a lock the
  holding task may re-enter. Completion, logging-level and extension handlers
  get a fresh FastMCP request context and see no scope.
- **Scope ownership:** by default `@inject` opens and finalizes `Scope.REQUEST` via
  `wrap_injection(manage_scope=True)`. This is deliberate: FastMCP runs sync tools
  in a worker thread, so the scope must live entirely in the thread where the
  handler runs — otherwise a thread-affine resource (a `sqlite3` connection)
  created in the worker is torn down on the event loop and errors.
- **Deferred results:** a handler that *returns* an awaitable, generator or async
  generator is rejected with `DishkaFastMCPError` — FastMCP would consume it after
  the REQUEST scope finalized its dependencies. The rejected object is closed
  first. A handler *defined* as a generator keeps the scope open while it is
  iterated and is supported.
- **Sync APP scope:** APP dependencies used by sync handlers must be thread-safe
  and have thread-independent cleanup. FastMCP may use multiple worker threads,
  while root-container cleanup runs from the server lifespan. Thread-affine
  resources belong in REQUEST scope.
- **Container transport:** an attribute on the FastMCP application, reached through
  FastMCP's public request context — never a function argument (tool args are bound
  to the client schema) and never a private FastMCP attribute. FastMCP propagates
  its request context into worker threads, so the sync path resolves it too.
- **Async-generator cleanup is asymmetric on purpose:** the async path awaits
  `aclose()`, the sync path drives it by hand. A sync handler cannot await, so the
  async generator it returns is always not-yet-started and its `aclose()` finishes
  without suspending — the async path has no such guarantee. Do not merge the two.
- **Background tasks:** `task=True` handlers are unsupported — they run in a
  `fastmcp-tasks` worker outside the request. The worker is detected by looking up
  `fastmcp_tasks.context` in `sys.modules`; never import `fastmcp_tasks` for it —
  the extension is optional and its package registers a client extension on import.
- **Scopes:** `APP` (server lifetime), `REQUEST` (one operation), and opt-in
  `SESSION` (one MCP connection, `DishkaMiddleware(session_scope=True)`). SESSION
  containers live in the SDK `Connection.state`, one per root container (mounted
  servers with containers of their own get their own), and close through
  `Connection.exit_stack`, which the SDK unwinds when the connection closes. The
  request context FastMCP receives reaches the `Connection` only through the
  private `ServerSession._connection` (FastMCP reads it the same way); keep that
  access in `middleware._connection`.
  2026-07-28 and stateless HTTP connections live one request, and so does
  SESSION there: it follows the connection by design, never fake it with a cache.

## Conventions

- **Conventional commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
  `chore:`, `perf:`, `ci:`), enforced by a `commit-msg` hook.
- **Tests first**, 100% coverage. Registration tests assert the generated schema.
  Sync-tool tests must prove `ContextVar` propagation through `run_in_thread`.
- **CHANGELOG** in Keep a Changelog format, written by hand in prose.
- **Agent-facing docs follow the API.** A change to the public surface or its
  rules also updates `docs/llms.txt`, `skills/dishka-fastmcp/SKILL.md` and the
  `rules` in `context7.json`.
- Type checkers cover the package and a dedicated typing-surface test, not the
  whole suite — strict-checking decorator-registered handlers only fights
  `reportUnusedFunction`.

## Environment and commands

```bash
uv sync                       # create venv, install dev deps
uv run pytest --cov           # tests + coverage (100%)
uv run ruff format --check && uv run ruff check
uv run mypy && uv run pyright
uv run zizmor .github/        # workflow audit (offline without GH_TOKEN)
uv run prek install           # one-time: git hooks
uv run prek run --all-files   # every hook, incl. pymarkdown (not run in CI)
```
