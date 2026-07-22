# Getting started

## Install

=== "uv"

    ```bash
    uv add dishka-fastmcp
    ```

=== "pip"

    ```bash
    pip install dishka-fastmcp
    ```

=== "poetry"

    ```bash
    poetry add dishka-fastmcp
    ```

The package requires Python 3.11+, Dishka 1.10.1+, and FastMCP 3.2.4 through
the latest 3.x release.

## Configure the server

Create the container before the FastMCP server so the lifespan can close it on
shutdown:

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
mcp = FastMCP('example', lifespan=dishka_lifespan(container))
setup_dishka(container, mcp)


@mcp.tool
@inject
async def get_price(item: str, catalog: FromDishka[Catalog]) -> int:
    return catalog.price(item)
```

!!! warning "Decorator order matters"

    Put `@inject` below `@mcp.tool`, `@mcp.resource`, or `@mcp.prompt`. FastMCP
    must inspect the signature after Dishka parameters have been removed.

## Resources and prompts

The same decorator works for every supported FastMCP component:

```python
@mcp.resource('users://{user_id}')
@inject
async def user(user_id: str, repo: FromDishka[UserRepo]) -> dict:
    return await repo.get(user_id)


@mcp.prompt
@inject
async def summarize(text: str, service: FromDishka[Summarizer]) -> str:
    return await service.run(text)
```

## Sync handlers

Use a sync Dishka container for sync handlers. FastMCP runs them in a worker
thread, and dishka-fastmcp keeps dependency creation, usage, and cleanup in that
thread.

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
