"""Re-identification: body embeddings, SQLite+FAISS gallery, fusion coordinator."""

from __future__ import annotations

from .config import ReidConfig
from .coordinator import ReIDCoordinator

__all__ = ["ReidConfig", "ReIDCoordinator"]
