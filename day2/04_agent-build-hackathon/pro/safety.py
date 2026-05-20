"""Safety layer — prompt-injection sanitization + PII / secrets scanner.

Two passes:
  - INPUT (RFP question text) is sanitized before the agent sees it.
    Defends against "ignore previous instructions" style injection planted
    by the RFP author.
  - OUTPUT (drafted answer) is scanned for leaked PII or secret-shaped
    strings before it lands in a customer-facing field.

Both are conservative: surface findings + optionally redact, never silently
ship a tainted answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# --------------------------------------------------------------------------
# Input — prompt injection defense
# --------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    re.compile(r"\bignore (?:all |any )?(?:previous |prior |above )?instructions?\b", re.IGNORECASE),
    re.compile(r"\bdisregard (?:the |all |your )?(?:previous |above |system )?prompt\b", re.IGNORECASE),
    re.compile(r"\byou\s+(?:are|will|must|should)\s+now\s+(?:act|behave|respond|pretend|roleplay)\b", re.IGNORECASE),
    re.compile(r"</?\s*(?:system|user|assistant)\s*>", re.IGNORECASE),  # fake delimiter injection
    re.compile(r"\bnew (?:role|task|persona|system prompt)\b", re.IGNORECASE),
    re.compile(r"\b(?:reveal|print|show|output)\s+(?:your |the )?(?:system )?(?:prompt|instructions?)\b", re.IGNORECASE),
]


@dataclass
class InjectionScan:
    detected: bool
    matched_patterns: list[str]
    sanitized_text: str

    def as_dict(self) -> dict:
        return {
            "detected": self.detected,
            "matched_patterns": self.matched_patterns,
            "sanitized_text": self.sanitized_text,
        }


def sanitize_input(text: str, *, replace_with: str = "[redacted by safety]") -> InjectionScan:
    """Detect injection patterns and replace them with a benign marker."""
    matched: list[str] = []
    sanitized = text
    for pat in _INJECTION_PATTERNS:
        if pat.search(sanitized):
            matched.append(pat.pattern[:80])
            sanitized = pat.sub(replace_with, sanitized)
    return InjectionScan(detected=bool(matched),
                         matched_patterns=matched,
                         sanitized_text=sanitized)


# --------------------------------------------------------------------------
# Output — PII + secrets scanner
# --------------------------------------------------------------------------

_PII_PATTERNS = {
    "email":      re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone":      re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn":        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card":re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "ipv4":       re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "aws_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "gcp_key":    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    "github_pat": re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),
    "jwt":        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
}

# Allowlist: things that LOOK like PII but are legitimate Helios examples.
# Tune per deployment; here we permit Helios's own example email + domains.
_ALLOWED_EMAILS = {
    "billing@meridian.io",   # appears in baseline coordinator examples
    "support@helios.io",
    "info@helios.io",
}


@dataclass
class PIIFinding:
    kind: str
    value: str
    redacted: str   # the safe replacement


@dataclass
class PIIScan:
    findings: list[PIIFinding]
    clean: bool
    redacted_text: str

    def as_dict(self) -> dict:
        return {
            "clean": self.clean,
            "findings": [{"kind": f.kind, "value": f.value, "redacted": f.redacted}
                          for f in self.findings],
            "redacted_text": self.redacted_text,
        }


def _redact_value(kind: str, val: str) -> str:
    if kind in ("email",):
        local, _, domain = val.partition("@")
        if len(local) <= 2:
            local = "*" * len(local)
        else:
            local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{local}@{domain}"
    if kind in ("anthropic_key", "openai_key", "github_pat", "gcp_key", "jwt", "aws_key_id"):
        return f"[redacted {kind}]"
    if kind == "ipv4":
        return ".".join(["xxx" if i < 3 else val.split(".")[3] for i in range(4)])
    # default
    return "*" * len(val)


def scan_output(text: str) -> PIIScan:
    """Return findings + a redacted copy of the text."""
    findings: list[PIIFinding] = []
    redacted = text
    for kind, pat in _PII_PATTERNS.items():
        for m in pat.finditer(text):
            val = m.group(0)
            if kind == "email" and val.lower() in _ALLOWED_EMAILS:
                continue
            if kind == "credit_card":
                # crude Luhn-ish filter: must contain digit-clusters
                digits = re.sub(r"\D", "", val)
                if len(digits) < 13 or len(digits) > 19:
                    continue
            red = _redact_value(kind, val)
            findings.append(PIIFinding(kind=kind, value=val, redacted=red))
            redacted = redacted.replace(val, red)
    return PIIScan(findings=findings, clean=(len(findings) == 0), redacted_text=redacted)
