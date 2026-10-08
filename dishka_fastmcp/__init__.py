"""dishka-fastmcp — dishka IoC integration for FastMCP."""

from dishka import FromDishka

from dishka_fastmcp._container import get_request_container
from dishka_fastmcp._inject import inject
from dishka_fastmcp.exceptions import DishkaFastMCPError
from dishka_fastmcp.lifespan import dishka_lifespan
from dishka_fastmcp.middleware import DishkaMiddleware
from dishka_fastmcp.providers import FastMCPProvider
from dishka_fastmcp.setup import setup_dishka
from dishka_fastmcp.version import VERSION

__version__ = VERSION

__all__ = (
    'DishkaFastMCPError',
    'DishkaMiddleware',
    'FastMCPProvider',
    'FromDishka',
    '__version__',
    'dishka_lifespan',
    'get_request_container',
    'inject',
    'setup_dishka',
)
