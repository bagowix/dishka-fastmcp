"""The @inject decorator: rewrites the signature and binds dependencies.

Must sit BELOW the FastMCP decorator. ``wrap_injection(remove_depends=True)``
strips ``FromDishka`` parameters from ``__signature__``, so it has to run first;
``@mcp.tool`` then builds the JSON schema from the already-cleaned signature.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing, suppress
from functools import WRAPPER_ASSIGNMENTS, wraps
from inspect import (
    isasyncgen,
    isasyncgenfunction,
    isawaitable,
    iscoroutine,
    iscoroutinefunction,
    isgenerator,
    isgeneratorfunction,
)
from typing import Any, ParamSpec, TypeVar, cast, overload

from dishka import Scope
from dishka.integrations.base import wrap_injection

from dishka_fastmcp._container import (
    get_async_container,
    get_shared_container,
    get_sync_container,
    provide_context,
    shared_request_container,
)
from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = ('inject',)

P = ParamSpec('P')
T = TypeVar('T')

# wrap_injection sets the cleaned ``__annotations__`` directly, and since Python
# 3.14 ``wraps`` copies ``__annotate__`` instead, which would drop them.
_INJECTED_ASSIGNMENTS = (*WRAPPER_ASSIGNMENTS, '__annotations__')


def _close_coroutine_like(value: object) -> None:
    """Close native and generator-based coroutines without awaiting them."""
    if iscoroutine(value) or isgenerator(value):
        value.close()


def _close_async_generator_from_sync(generator: AsyncGenerator[Any, Any]) -> None:
    """Finalize an async generator handed back by a synchronous handler.

    A sync handler cannot await, so it cannot have advanced the generator: the one
    it returns is always still unstarted, and ``aclose()`` on an unstarted async
    generator completes without suspending. That makes it safe to drive by hand,
    which the async path does not need — there ``aclose()`` is simply awaited.
    """
    closer = generator.aclose()
    with suppress(StopIteration):
        closer.send(None)


def _callable_name(func: object) -> str:
    """Return a useful name for functions, partials, and callable objects."""
    return getattr(func, '__name__', type(func).__name__)


def _wrap_async(func: Callable[P, T]) -> tuple[Callable[P, T], Callable[P, T]] | None:
    """Return ``func`` injected from a REQUEST scope of its own, and from the shared one.

    Returns ``None`` when ``func`` has no ``FromDishka`` parameters.
    """
    scoped = wrap_injection(
        func=func,
        container_getter=get_async_container,
        is_async=True,
        remove_depends=True,
        manage_scope=True,
        scope=Scope.REQUEST,
        provide_context=provide_context,
    )
    if scoped is func:
        return None
    shared = wrap_injection(
        func=func,
        container_getter=get_shared_container,
        is_async=True,
        remove_depends=True,
    )
    return scoped, shared


def _inject_async_generator(
    func: Callable[P, AsyncGenerator[T, None]],
) -> Callable[P, AsyncGenerator[T, None]]:
    wrappers = _wrap_async(func)
    if wrappers is None:
        return func
    scoped, shared = wrappers

    @wraps(scoped, assigned=_INJECTED_ASSIGNMENTS)
    async def handler(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[T, None]:
        injected = scoped if shared_request_container() is None else shared
        # Closing this generator early must close the injected one at once: that
        # is where its own REQUEST scope finalizes.
        async with aclosing(injected(*args, **kwargs)) as messages:
            async for message in messages:
                yield message

    return handler


def inject_async(
    func: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Inject dependencies into an async handler, opening the REQUEST scope.

    Under ``DishkaMiddleware`` the handler shares the scope the middleware opened
    for the whole MCP request instead, unless its server was set up with a
    different container.

    Deferred values returned by an ordinary ``async def`` handler are rejected:
    FastMCP would consume them after the handler returned, when its scope may
    already be finalized. A handler defined as an async generator is supported
    and keeps the scope open for the whole iteration.
    """
    if isasyncgenfunction(func):
        generator_func = cast('Callable[P, AsyncGenerator[T, None]]', func)
        return cast('Callable[P, Awaitable[T]]', _inject_async_generator(generator_func))

    @wraps(func)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        result = await func(*args, **kwargs)
        if isawaitable(result):
            if isinstance(result, asyncio.Future):
                result.cancel()
                await asyncio.gather(result, return_exceptions=True)
            else:
                _close_coroutine_like(result)
            result_kind = 'an awaitable'
        elif isgenerator(result):
            result.close()
            result_kind = 'a generator'
        elif isasyncgen(result):
            await result.aclose()
            result_kind = 'an async generator'
        else:
            return result
        del args, kwargs, result
        raise DishkaFastMCPError(
            f'Async handler {_callable_name(func)!r} returned {result_kind}, but '
            'FastMCP would run it after the handler returned, when its REQUEST scope '
            'may already be finalized. Define the handler itself as an async '
            'generator, or produce the value before returning.',
        )

    wrappers = _wrap_async(guarded)
    if wrappers is None:  # no FromDishka parameters — nothing to inject or guard
        return func
    scoped, shared = wrappers

    @wraps(scoped, assigned=_INJECTED_ASSIGNMENTS)
    async def handler(*args: P.args, **kwargs: P.kwargs) -> T:
        injected = scoped if shared_request_container() is None else shared
        return await injected(*args, **kwargs)

    return handler


def inject_sync(func: Callable[P, T]) -> Callable[P, T]:
    """Inject dependencies into a sync handler, opening the REQUEST scope.

    For an ordinary sync handler, ``manage_scope`` opens and finalizes the scope
    inside its worker-thread call. Deferred return values are rejected because
    FastMCP would consume them after that scope has closed. A handler defined as
    a generator is supported and keeps the scope open wherever FastMCP iterates
    it.
    """
    if isgeneratorfunction(func):
        generator_func = cast('Callable[P, T]', func)
        return wrap_injection(
            func=generator_func,
            container_getter=get_sync_container,
            is_async=False,
            remove_depends=True,
            manage_scope=True,
            scope=Scope.REQUEST,
            provide_context=provide_context,
        )

    @wraps(func)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        result = func(*args, **kwargs)
        if isawaitable(result):
            _close_coroutine_like(result)
            result_kind = 'an awaitable'
        elif isgenerator(result):
            result.close()
            result_kind = 'a generator'
        elif isasyncgen(result):
            _close_async_generator_from_sync(result)
            result_kind = 'an async generator'
        else:
            return result
        del args, kwargs, result
        raise DishkaFastMCPError(
            f'Sync handler {_callable_name(func)!r} returned {result_kind}, but its '
            'REQUEST scope closes when the handler returns, so deferred execution '
            'would use finalized dependencies. Define the handler itself as a '
            'generator, or declare it as `async def` and use an AsyncContainer.',
        )

    wrapped = wrap_injection(
        func=guarded,
        container_getter=get_sync_container,
        is_async=False,
        remove_depends=True,
        manage_scope=True,
        scope=Scope.REQUEST,
        provide_context=provide_context,
    )
    if wrapped is guarded:  # no FromDishka parameters — nothing to inject or guard
        return func
    return wrapped


@overload
def inject(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]: ...


@overload
def inject(func: Callable[P, T]) -> Callable[P, T]: ...


def inject(func: Callable[P, Any]) -> Callable[P, Any]:
    """Inject ``FromDishka`` dependencies, auto-detecting sync vs async."""
    if iscoroutinefunction(func) or isasyncgenfunction(func):
        return inject_async(func)
    return inject_sync(func)
