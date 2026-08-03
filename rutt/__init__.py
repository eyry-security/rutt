"""Rutt — a Postgres-backed store for recon data.

The logbook of the Eyry recon suite: it keeps the accumulated record of a
target — every host seen, every probe result, every finding — in Postgres, and
gives you a CLI and a library to add to it and query it.
"""

from .store import Rutt

__version__ = "0.1.0"
__all__ = ["Rutt", "__version__"]
