from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountUser:
    id: int
    email: str
    display_name: str
    role: str


@dataclass(frozen=True)
class SessionToken:
    token: str
    csrf_token: str
