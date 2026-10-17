"""
temproven/config.py
───────────────────
Loads all configuration from environment variables (via .env).
No defaults for credentials — if the DB host is not set, we fail loudly.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)


def _require(name: str) -> str:
    """Return the env var value, or raise a clear error if it is missing."""
    val = os.environ.get(name, "").strip()
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            f"Copy .env.example to .env and fill in the values."
        )
    return val


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ── Connection settings ───────────────────────────────────────────────────────
FALKORDB_HOST: str = _require("FALKORDB_HOST")
FALKORDB_PORT: int = int(_optional("FALKORDB_PORT", "6379"))
FALKORDB_USERNAME: str = _optional("FALKORDB_USERNAME", "")
FALKORDB_PASSWORD: str = _optional("FALKORDB_PASSWORD", "")
FALKORDB_SSL: bool = _optional("FALKORDB_SSL", "false").lower() in ("true", "1", "yes")

# ── Graph names ───────────────────────────────────────────────────────────────
GRAPH_NAME: str = _optional("FALKORDB_GRAPH", "temproven")

# ── Misc ──────────────────────────────────────────────────────────────────────
# Maximum depth for dependency traversal (impact tracing)
MAX_TRAVERSAL_DEPTH: int = 6
