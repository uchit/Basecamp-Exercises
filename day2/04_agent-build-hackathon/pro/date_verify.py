"""Date / version verifier.

Extends the regex-based citation verifier with two checks that string
matching alone can't do:

  1. Date freshness — a cited audit/certification date should be within an
     acceptable staleness window. >12 months for SOC 2, >36 months for
     ISO 27001, etc.
  2. Version sanity — a cited product/standard version should match the
     latest version mentioned in the cited source. If the source says
     "PCI DSS v4.0" and the answer cites "PCI DSS v3.2", flag it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .kb import by_source


# Months → numeric (case-insensitive lookup).
_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "December 2024" / "Dec 2024" / "2024-03" / "March 2024" → (year, month)
_DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{1,2})(?:-\d{1,2})?\b"),                  # 2024-03 or 2024-03-15
    re.compile(r"\b([A-Z][a-z]{2,9})\s+(\d{4})\b"),                       # March 2024
    re.compile(r"\b(\d{1,2})\s+([A-Z][a-z]{2,9})\s+(\d{4})\b"),           # 15 March 2024
]

# Per-framework staleness windows (months).
_STALENESS_BUDGET_MONTHS = {
    "SOC 2": 13, "SOC2": 13,
    "ISO 27001": 36, "ISO27001": 36,
    "FedRAMP": 12,
    "HIPAA": 24,
    "PCI DSS": 12, "PCI": 12,
    "StateRAMP": 12,
}

# Acceptable version mention per framework — sourced from the KB.
_VERSION_EXPECTED = {
    "PCI DSS": "v4.0",
    "ISO 27001": "27001:2022",
}


def _parse_first_date(text: str) -> date | None:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        g = m.groups()
        try:
            if len(g) == 2 and g[0].isdigit():
                return date(int(g[0]), int(g[1]), 1)
            if len(g) == 2:
                month = _MONTH_NAMES.get(g[0].lower())
                if month:
                    return date(int(g[1]), month, 1)
            if len(g) == 3:
                month = _MONTH_NAMES.get(g[1].lower())
                if month:
                    return date(int(g[2]), month, int(g[0]) or 1)
        except (ValueError, KeyError):
            continue
    return None


@dataclass
class DateFinding:
    framework: str
    cited_date: str | None
    months_old: int | None
    budget_months: int
    is_stale: bool


@dataclass
class VersionFinding:
    framework: str
    cited_version: str
    expected_version: str
    is_outdated: bool


@dataclass
class DateVerifyResult:
    date_findings: list[DateFinding]
    version_findings: list[VersionFinding]
    is_clean: bool

    def as_dict(self) -> dict:
        return {
            "is_clean": self.is_clean,
            "date_findings": [f.__dict__ for f in self.date_findings],
            "version_findings": [f.__dict__ for f in self.version_findings],
        }


def verify_dates_and_versions(answer_text: str, cited_sources: list[str],
                                *, today: date | None = None) -> DateVerifyResult:
    """Look for framework + date pairs in the answer and the cited sources.
    Flag stale + version-mismatched citations."""
    today = today or date.today()
    date_findings: list[DateFinding] = []
    version_findings: list[VersionFinding] = []

    # Pull cited source content.
    cited_content = " ".join(
        (by_source(s) or {"content": ""})["content"] for s in cited_sources
    )

    # For each known framework, find dates near the framework mention in the
    # answer text (within ±60 char window).
    for framework, budget in _STALENESS_BUDGET_MONTHS.items():
        for m in re.finditer(re.escape(framework), answer_text, re.IGNORECASE):
            window = answer_text[max(0, m.start() - 60):m.end() + 60]
            d = _parse_first_date(window)
            cited = window if not d else d.isoformat()
            if d:
                months_old = (today.year - d.year) * 12 + (today.month - d.month)
                date_findings.append(DateFinding(
                    framework=framework,
                    cited_date=d.isoformat(),
                    months_old=months_old,
                    budget_months=budget,
                    is_stale=months_old > budget,
                ))
            # only record once per framework per answer
            break

    # Version checks
    for framework, expected in _VERSION_EXPECTED.items():
        ans_match = re.search(re.escape(framework) + r"\s*([vV]?\d[\d\.\-]*)", answer_text)
        if not ans_match:
            continue
        cited_ver = ans_match.group(1)
        # Compare loose-numeric: 4.0 vs v4.0 vs 27001:2022 etc.
        if expected.lstrip("v").lower() not in cited_ver.lower():
            version_findings.append(VersionFinding(
                framework=framework,
                cited_version=cited_ver,
                expected_version=expected,
                is_outdated=True,
            ))

    is_clean = (not any(f.is_stale for f in date_findings)
                and not any(f.is_outdated for f in version_findings))
    return DateVerifyResult(date_findings=date_findings,
                            version_findings=version_findings,
                            is_clean=is_clean)
