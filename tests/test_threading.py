"""Sync handlers run in a FastMCP worker thread.

The REQUEST scope must be entered, used and finalized in that same thread —
otherwise a thread-affine resource created in the worker is torn down on the
event loop and errors (e.g. sqlite3 raises ProgrammingError).
"""

import sqlite3
import threading
from collections.abc import Iterator
from typing import NewType

import pytest
from dishka import Provider, Scope, make_container, provide
from fastmcp import FastMCP
from mcp.types import TextContent

from dishka_fastmcp import FromDishka, inject, setup_dishka

Connection = NewType('Connection', sqlite3.Connection)


class ConnectionProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.threads: dict[str, int] = {}

    @provide(scope=Scope.REQUEST)
    def connection(self) -> Iterator[Connection]:
        connection = sqlite3.connect(':memory:')
        self.threads['created'] = threading.get_ident()
        try:
            yield Connection(connection)
        finally:
            self.threads['closed'] = threading.get_ident()
            connection.close()


@pytest.mark.asyncio
async def test_sync_request_scope_lives_entirely_in_worker_thread() -> None:
    provider = ConnectionProvider()
    container = make_container(provider)
    mcp = FastMCP('test', mask_error_details=False)
    setup_dishka(container, mcp)

    @mcp.tool
    @inject
    def query(connection: FromDishka[Connection]) -> int:
        provider.threads['used'] = threading.get_ident()
        row: tuple[int] = connection.execute('select 1').fetchone()
        return row[0]

    try:
        result = await mcp.call_tool('query')
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == '1'
    finally:
        container.close()

    worker_threads = set(provider.threads.values())
    assert len(worker_threads) == 1
    assert threading.get_ident() not in worker_threads
