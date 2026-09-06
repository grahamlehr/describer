"""Backwards-compatible alias for :mod:`describer.rail.ldbws`.

The LDBWS client lived here when RDM was the only source. Nothing in the
project imports this module any more; it exists so an out-of-tree script
written against v1 keeps working, and can be deleted after one release.
"""

from __future__ import annotations

from .base import RailApiError
from .ldbws import LdbwsClient, api_key, parse_board

__all__ = ["LdbwsClient", "RailApiError", "api_key", "parse_board"]
