"""Encrypted provider credentials for signed-in Omakase Lite members."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from omakase.lite import db

ALLOWED_PROVIDERS = frozenset(
    {"openai", "openwebui", "anthropic", "gemini", "deepseek", "openrouter"}
)
PROVIDER_LABELS = {
    "openai": "OpenAI",
    "openwebui": "OpenWebUI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
}
_MAX_KEY_LENGTH = 4096


class CredentialError(ValueError):
    """A provider credential cannot be stored or resolved safely."""


class KeyringUnavailable(CredentialError):
    """The server-side credential keyring is absent or invalid."""


class SavedCredentialInvalid(CredentialError):
    """A stored credential cannot be decrypted with the active keyring."""


def validate_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in ALLOWED_PROVIDERS:
        raise CredentialError("Choose a supported model provider.")
    return normalized


def _fernet() -> Fernet:
    keyring_file = os.getenv("OMAKASE_LITE_KEYRING_FILE", "").strip()
    if not keyring_file:
        raise KeyringUnavailable("Saved provider keys are temporarily unavailable.")
    try:
        key = Path(keyring_file).read_bytes().strip()
        return Fernet(key)
    except (OSError, ValueError) as exc:
        raise KeyringUnavailable("Saved provider keys are temporarily unavailable.") from exc


def save_provider_key(
    conn,
    *,
    user_id: int,
    provider: str,
    plaintext_key: str,
) -> dict[str, object]:
    normalized_provider = validate_provider(provider)
    key = plaintext_key.strip()
    if not key:
        raise CredentialError("Paste a provider key before saving.")
    if len(key) > _MAX_KEY_LENGTH:
        raise CredentialError("The provider key is too long.")
    encrypted_key = _fernet().encrypt(key.encode("utf-8")).decode("ascii")
    hint = key[-4:]
    db.upsert_provider_key(
        conn,
        user_id=user_id,
        provider=normalized_provider,
        encrypted_key=encrypted_key,
        key_hint=hint,
    )
    return {"provider": normalized_provider, "saved": True, "hint": hint}


def load_provider_key(conn, *, user_id: int, provider: str) -> str | None:
    normalized_provider = validate_provider(provider)
    row = db.get_provider_key_record(
        conn,
        user_id=user_id,
        provider=normalized_provider,
    )
    if row is None:
        return None
    try:
        return _fernet().decrypt(row["encrypted_key"].encode("ascii")).decode("utf-8")
    except KeyringUnavailable:
        raise
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SavedCredentialInvalid(
            "The saved provider key cannot be used. Replace it and try again."
        ) from exc


def provider_key_summaries(conn, *, user_id: int) -> dict[str, dict[str, object]]:
    return {
        row["provider"]: {"saved": True, "hint": row["key_hint"]}
        for row in db.provider_key_records(conn, user_id=user_id)
    }


def forget_provider_key(conn, *, user_id: int, provider: str) -> bool:
    return db.delete_provider_key(
        conn,
        user_id=user_id,
        provider=validate_provider(provider),
    )
