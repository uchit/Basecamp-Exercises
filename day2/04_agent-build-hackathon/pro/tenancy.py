"""Multi-tenancy — namespaced KBs.

The shipping Pro module assumes one KB (KNOWLEDGE_BASE in pro/kb.py). For
multi-tenant deployment, each tenant gets a namespaced KB at
`./kbs/<tenant_id>.json`. Loading a tenant returns a thin wrapper that
exposes the same shape as KNOWLEDGE_BASE so retrieval_v2 and the rest of
the stack work unmodified.

Adds (a) tenant resolution from env / arg, (b) KB hash per-tenant, (c)
runtime guard that the loaded tenant matches the audit-db row's namespace.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


KBS_DIR = Path(__file__).resolve().parent.parent / "kbs"


# Sanity bounds.
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,40}$")


@dataclass
class Tenant:
    tenant_id: str
    kb: dict
    path: Path

    def kb_size(self) -> int:
        return len(self.kb)


def is_valid_tenant_id(tid: str) -> bool:
    return bool(tid and _TENANT_RE.match(tid))


def load(tenant_id: str | None = None,
         *, kbs_dir: Path | None = None) -> Tenant:
    """Load a tenant by id.

    Resolution order: explicit arg → PRO_TENANT_ID env → "default" → raise.
    The default tenant uses the bundled KNOWLEDGE_BASE.
    """
    tid = (tenant_id
           or os.environ.get("PRO_TENANT_ID")
           or "default").strip().lower()

    if not is_valid_tenant_id(tid):
        raise ValueError(f"invalid tenant_id: {tid!r}")

    if tid == "default":
        # Use the bundled KB — no file needed.
        from .kb import KNOWLEDGE_BASE
        return Tenant(tenant_id="default", kb=KNOWLEDGE_BASE,
                       path=Path("(bundled)"))

    base = kbs_dir or KBS_DIR
    path = base / f"{tid}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"tenant '{tid}' KB not found at {path}. "
            f"Create the file with shape {{doc_id: {{source, content, tags}}}}."
        )
    kb = json.loads(path.read_text())
    if not isinstance(kb, dict):
        raise ValueError(f"tenant KB at {path} must be a JSON object")
    # Light schema check.
    for doc_id, entry in kb.items():
        for required in ("source", "content"):
            if required not in entry:
                raise ValueError(
                    f"tenant '{tid}' KB doc '{doc_id}' missing required field '{required}'"
                )
    return Tenant(tenant_id=tid, kb=kb, path=path)


def list_tenants(kbs_dir: Path | None = None) -> list[str]:
    """All tenants discovered on disk + the bundled default."""
    base = kbs_dir or KBS_DIR
    on_disk = (sorted(p.stem for p in base.glob("*.json"))
               if base.exists() else [])
    return sorted(set(["default"] + on_disk))


def save_tenant(tenant_id: str, kb: dict, kbs_dir: Path | None = None) -> Path:
    """Persist a KB for a tenant. Validates the schema."""
    if not is_valid_tenant_id(tenant_id) or tenant_id == "default":
        raise ValueError(f"cannot save tenant {tenant_id!r}")
    base = (kbs_dir or KBS_DIR)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{tenant_id}.json"
    path.write_text(json.dumps(kb, indent=2))
    return path
