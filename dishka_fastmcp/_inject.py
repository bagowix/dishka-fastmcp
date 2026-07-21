"""The @inject decorator: rewrites the signature and binds dependencies.

Must sit BELOW the FastMCP decorator. ``wrap_injection(remove_depends=True)``
strips ``FromDishka`` parameters from ``__signature__``, so it has to run first;
``@mcp.tool`` then builds the JSON schema from the already-cleaned signature.
"""

from collections.abc import Awaitable, Callable
from inspect import isasyncgenfunction, iscoroutinefunction
from typing import Any, ParamSpec, TypeVar, overload

from dishka import Scope
from dishka.integrations.base import wrap_injection

from dishka_fastmcp._container import (
    get_async_container,
    get_sync_container,
    provide_context,
)

__all__ = ('inject',)

P = ParamSpec('P')
T = TypeVar('T')


def inject_async(
    func: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Inject dependencies into an async handler, opening the REQUEST scope."""
    return wrap_injection(
        func=func,
        container_getter=get_async_container,
        is_async=True,
        remove_depends=True,
        manage_scope=True,
        scope=Scope.REQUEST,
        provide_context=provide_context,
    )


def inject_sync(func: Callable[P, T]) -> Callable[P, T]:
    """Inject dependencies into a sync handler, opening the REQUEST scope.

    ``manage_scope`` makes the scope open and finalize inside the call, so a sync
    handler run in a FastMCP worker thread creates and closes its REQUEST-scoped
    dependencies in that same thread.
    """
    return wrap_injection(
        func=func,
        container_getter=get_sync_container,
        is_async=False,
        remove_depends=True,
        manage_scope=True,
        scope=Scope.REQUEST,
        provide_context=provide_context,
    )


@overload
def inject(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]: ...


@overload
def inject(func: Callable[P, T]) -> Callable[P, T]: ...


def inject(func: Callable[P, Any]) -> Callable[P, Any]:
    """Inject ``FromDishka`` dependencies, auto-detecting sync vs async."""
    if iscoroutinefunction(func) or isasyncgenfunction(func):
        return inject_async(func)
    return inject_sync(func)
