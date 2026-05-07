"""Runtime identity helpers for session owner resolution."""

from __future__ import annotations

import os


def default_owner_id(fallback: str = "local") -> str:
    """Return the configured long-term memory owner, falling back to the caller value."""
    configured = (os.getenv("LUCKBOT_OWNER_ID") or "").strip()
    if configured:
        return configured
    return (fallback or "local").strip() or "local"


__all__ = ["default_owner_id"]
