"""FastAPI dependency helpers for Omakase Plus.

Provides a ``get_db`` dependency that wraps ``omakase.plus.db.get_db()``
in a zero-parameter callable so FastAPI does not attempt to inject
``data_dir`` from the request.
"""

from __future__ import annotations

from omakase.plus.db import get_db as _get_db_impl


def get_db():
    """FastAPI dependency that provides a database connection.

    Wraps ``omakase.plus.db.get_db()`` with the default ``data_dir``.
    """
    return _get_db_impl()
