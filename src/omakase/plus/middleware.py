"""FastAPI dependencies for Omakase Plus authentication.

Provides ``get_current_user`` (returns ``User | None``) and
``require_user`` (raises 401 on missing / invalid session).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from omakase.plus.auth import validate_session
from omakase.plus.deps import get_db
from omakase.plus.models import User


async def get_current_user(
    request: Request,
    db=Depends(get_db),
) -> User | None:
    """Read ``omakase_session`` cookie and return the corresponding User.

    Returns ``None`` when the cookie is missing, expired, or invalid.
    """
    session_id = request.cookies.get("omakase_session")
    if not session_id:
        return None
    user_id = validate_session(db, session_id)
    if user_id is None:
        return None
    row = db.execute(
        "SELECT id, email, password_hash, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
    )


async def require_user(
    request: Request,
    db=Depends(get_db),
) -> User:
    """Like ``get_current_user`` but raises 401 when unauthenticated.

    The 401 response includes a ``Location`` header pointing at the
    login page so a browser or test client can follow the redirect.
    """
    user = await get_current_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"Location": "/plus/login"},
        )
    return user
