"""Source connectors. One interface, many backing stores (ARCHITECTURE.md §4)."""

from .base import Connector, Entry, classify
from .local import LocalConnector

__all__ = ["Connector", "Entry", "LocalConnector", "classify"]
