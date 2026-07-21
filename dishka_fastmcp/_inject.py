"""The @inject decorator: rewrites the signature and binds dependencies.

Must sit BELOW the FastMCP decorator. ``wrap_injection(remove_depends=True)``
strips ``FromDishka`` parameters from ``__signature__``, so it has to run first;
``@mcp.tool`` then builds the JSON schema from the already-cleaned signature.
"""

from collections.abc import Awaitable, Callable
from inspect import isasyncgenfunction, iscoroutinefunction
from typing import Any, ParamSpec, TypeVar, overload

from dishka.integrations.base import wrap_injection

from dishka_fastmcp._container import get_async_container, get_sync_container

__all__ = ('inject',)

P = ParamSpec('P')
T = TypeVar('T')


def inject_async(
    func: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Inject dependencies into an async handler."""
    return wrap_injection(
        func=func,
        container_getter=get_async_container,
        is_async=True,
        remove_depends=True,
    )


def inject_sync(func: Callable[P, T]) -> Callable[P, T]:
    """Inject dependencies into a sync handler."""
    return wrap_injection(
        func=func,
        container_getter=get_sync_container,
        is_async=False,
        remove_depends=True,
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
