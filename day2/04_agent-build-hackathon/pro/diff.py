"""Tracked-changes diff between draft revisions.

When critique-then-revise produces a v2 of an answer, this module renders
the difference word-by-word with insertions and deletions tagged so a
human reviewer can see exactly what changed. Output works in three formats:
- plain text (for terminal logs)
- HTML (for the per-RFP viewer's audit drawer)
- markdown (for git diffs and PR comments)
"""
from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass


@dataclass
class DiffPiece:
    """A single token (word or whitespace) tagged by operation."""
    op: str   # 'equal' / 'insert' / 'delete'
    text: str


def _tokenize_words(s: str) -> list[str]:
    """Split keeping whitespace as its own tokens so reconstruction is exact."""
    return re.findall(r"\S+|\s+", s or "")


def compute_word_diff(before: str, after: str) -> list[DiffPiece]:
    """SequenceMatcher word-by-word op codes → DiffPiece list."""
    a = _tokenize_words(before)
    b = _tokenize_words(after)
    sm = difflib.SequenceMatcher(None, a, b)
    out: list[DiffPiece] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(DiffPiece("equal", "".join(a[i1:i2])))
        elif tag == "replace":
            out.append(DiffPiece("delete", "".join(a[i1:i2])))
            out.append(DiffPiece("insert", "".join(b[j1:j2])))
        elif tag == "delete":
            out.append(DiffPiece("delete", "".join(a[i1:i2])))
        elif tag == "insert":
            out.append(DiffPiece("insert", "".join(b[j1:j2])))
    return out


def render_text(pieces: list[DiffPiece]) -> str:
    out: list[str] = []
    for p in pieces:
        if p.op == "equal":  out.append(p.text)
        elif p.op == "insert": out.append(f"{{+{p.text}+}}")
        elif p.op == "delete": out.append(f"[-{p.text}-]")
    return "".join(out)


def render_html(pieces: list[DiffPiece]) -> str:
    parts: list[str] = []
    for p in pieces:
        if p.op == "equal":
            parts.append(html.escape(p.text))
        elif p.op == "insert":
            parts.append(f'<ins style="background:rgba(48,176,74,.18);text-decoration:none">{html.escape(p.text)}</ins>')
        elif p.op == "delete":
            parts.append(f'<del style="background:rgba(255,69,58,.18);text-decoration:line-through">{html.escape(p.text)}</del>')
    return "".join(parts)


def render_markdown(pieces: list[DiffPiece]) -> str:
    """Same convention as `render_text` but suitable for GitHub markdown."""
    out: list[str] = []
    for p in pieces:
        if p.op == "equal":  out.append(p.text)
        elif p.op == "insert": out.append(f"**+{p.text}+**")
        elif p.op == "delete": out.append(f"~~{p.text}~~")
    return "".join(out)


def diff_summary(pieces: list[DiffPiece]) -> dict:
    """Quick stats for the audit drawer."""
    ins_chars = sum(len(p.text) for p in pieces if p.op == "insert")
    del_chars = sum(len(p.text) for p in pieces if p.op == "delete")
    eq_chars = sum(len(p.text) for p in pieces if p.op == "equal")
    total_after = ins_chars + eq_chars
    change_ratio = (ins_chars + del_chars) / max(total_after + del_chars, 1)
    return {
        "chars_inserted": ins_chars,
        "chars_deleted": del_chars,
        "chars_unchanged": eq_chars,
        "change_ratio": round(change_ratio, 3),
    }
