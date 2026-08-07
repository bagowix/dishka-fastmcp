"""Holds the version information for dishka-fastmcp.

Single source of truth for the package version: bumped manually on release
and read at build time by hatchling (see ``[tool.hatch.version]`` in
``pyproject.toml``).
"""

__all__ = ('VERSION',)

VERSION = '2.0.1'
"""The installed version of dishka-fastmcp.

Guaranteed to comply with PEP 440 version specifiers.
See https://peps.python.org/pep-0440/.
"""
