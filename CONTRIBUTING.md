# Contributing to dishka-fastmcp

Thanks for your interest in improving dishka-fastmcp. This guide covers the local
setup and the checks your change must pass.

## Development setup

dishka-fastmcp uses [uv](https://docs.astral.sh/uv/) for environment and
dependency management. With uv installed:

```bash
git clone https://github.com/bagowix/dishka-fastmcp
cd dishka-fastmcp
uv sync            # creates the venv and installs dev dependencies
uv run prek install  # one-time: installs the git hooks
```

## Running the checks

CI runs exactly these on Python 3.11–3.14. Run them locally before opening a PR:

```bash
uv run ruff format --check    # formatting
uv run ruff check             # linting
uv run mypy                   # type checking (strict)
uv run pyright                # type checking (strict, second checker)
uv run pytest --cov           # tests with coverage (threshold: 100%)
```

Run a single test:

```bash
uv run pytest tests/test_tools.py::test_async_tool_injects_and_hides_deps_from_schema
```

## Expectations

- **Tests first.** New behaviour and bug fixes come with tests; the suite keeps
  100% coverage. Any test touching registration asserts the generated tool schema.
- **Types are part of the API.** The public surface stays fully typed; mypy and
  pyright must pass in strict mode.
- **Conventional commits.** Use `feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
  `chore:`, `perf:`, `ci:` prefixes. Enforced by a `commit-msg` hook.
- **Update the CHANGELOG.** User-facing changes update the `[Unreleased]` section
  of `CHANGELOG.md` in prose — explain what changed and why.

## Scope

dishka-fastmcp is deliberately focused: a thin, correct bridge between dishka
and FastMCP's supported extension points together with Dishka's
`wrap_injection`.
It supports the scopes FastMCP can actually honor (`APP` and `REQUEST`) — see the
README for why `SESSION` is omitted. For anything beyond a small fix, open an
issue first so we can agree on the approach.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you agree to uphold it.
