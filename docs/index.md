# dishka-fastmcp

Use Dishka dependencies in FastMCP tools, resources, and prompts without adding
service parameters to the MCP client schema.

```bash
uv add dishka-fastmcp
```

```python
from dishka import Provider, Scope, make_async_container, provide
from fastmcp import FastMCP

from dishka_fastmcp import FromDishka, dishka_lifespan, inject, setup_dishka


class Catalog:
    def price(self, item: str) -> int:
        return {'book': 12, 'pen': 2}.get(item, 0)


class AppProvider(Provider):
    catalog = provide(Catalog, scope=Scope.REQUEST)


container = make_async_container(AppProvider())
mcp = FastMCP('shop', lifespan=dishka_lifespan(container))
setup_dishka(container, mcp)


@mcp.tool
@inject
async def get_price(item: str, catalog: FromDishka[Catalog]) -> int:
    return catalog.price(item)
```

The MCP client sees only the `item` argument. `catalog` is resolved inside a
Dishka `Scope.REQUEST` container and finalized when the tool call ends.

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

## Supported surface

| | Support |
|---|---|
| Python | 3.11-3.15 |
| Dishka | 1.10.1 and newer |
| FastMCP | 4.0.0 through 4.x |
| Handlers | Tools, resources, and prompts |
| Execution | Async and sync handlers |
| Scopes | `APP` and `REQUEST` |

[Get started](getting-started.md){ .md-button .md-button--primary }
[Ask Context7](https://context7.com/bagowix/dishka-fastmcp){ .md-button }
