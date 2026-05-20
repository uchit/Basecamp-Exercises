"""Knowledge base — re-exports the baseline KB and adds a few accessors."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Re-use the baseline KNOWLEDGE_BASE so this Pro build operates on identical
# data — fair A/B comparison.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hackathon  # type: ignore

KNOWLEDGE_BASE: dict[str, dict] = hackathon.KNOWLEDGE_BASE


def all_entries() -> list[dict]:
    """Returns every KB doc as a flat list of {id, source, content, tags}."""
    return [{"id": k, **v} for k, v in KNOWLEDGE_BASE.items()]


def by_source(source_name: str) -> dict | None:
    """Look up an entry by its 'source' label (used by the citation verifier)."""
    for entry in KNOWLEDGE_BASE.values():
        if entry["source"] == source_name:
            return entry
    return None
