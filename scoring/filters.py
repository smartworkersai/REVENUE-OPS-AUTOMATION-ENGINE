"""
Cheap pre-filters — no API calls, no LLM cost.

Run these before KPI scoring. Any job that fails here gets state=SKIPPED
immediately. Only jobs that pass all filters proceed to kpi.py.

Filters are intentionally conservative — when in doubt, pass the job through
to KPI scoring. The 7.5 threshold is the authoritative gate.
"""

import logging
import re
from dataclasses import dataclass

from scrapers.base import Job

log = logging.getLogger(__name__)

# Roles that are structurally incompatible with the candidate's profile.
# These are seniority/specialism mismatches too severe for KPI scoring to recover.
_HARD_EXCLUDE_PATTERNS = re.compile(
    r'\b('
    r'director|vp\b|vice president|chief\b|cmo\b|ceo\b|cfo\b|cto\b|'
    r'head of\b|partner\b|principal\b|'
    r'machine learning|'
    r'lawyer|solicitor|barrister|accountant|auditor\b|'
    r'nurse|doctor|physician|surgeon|dentist|pharmacist|'
    r'driver|delivery|warehouse|cleaner|security guard'
    r')\b',
    re.IGNORECASE,
)

# Roles that are clearly too junior even for candidate
_TOO_JUNIOR_PATTERNS = re.compile(
    r'\b(intern(?!ship)|work experience|work placement|apprentice(?!ship))\b',
    re.IGNORECASE,
)

# Salary hard floor — reject only if salary is explicitly stated AND clearly below
_SALARY_FLOOR = 24_000  # £24K — well below the £28K floor; gives buffer for uncertainty

_HOURLY_RE   = re.compile(r'£?([\d,]+\.?\d*)\s*(?:per\s+hour|/\s*hr|p\.?h\.?|ph\b|an?\s+hour)', re.IGNORECASE)
_ANNUAL_RE   = re.compile(r'£?([\d,]+(?:\.\d+)?)\s*(?:k\b|,000\b|pa\b|per\s+annum\b)', re.IGNORECASE)
_RANGE_RE    = re.compile(r'£?([\d,]+)\s*(?:k\b)?[\s\-–]+£?([\d,]+)\s*(?:k\b)?', re.IGNORECASE)

_ANNUAL_HOURS = 1_820  # standard working year for hourly → annual conversion


@dataclass
class FilterResult:
    passed: bool
    reason: str = ''


def pre_filter(job: Job) -> FilterResult:
    """
    Run all pre-filters against a Job. Returns FilterResult.
    Call this before KPI scoring to avoid unnecessary API calls.
    """

    # 1. Role title hard excludes
    if _HARD_EXCLUDE_PATTERNS.search(job.role):
        return FilterResult(False, f'role title excluded: {job.role!r}')

    # 2. Too junior
    if _TOO_JUNIOR_PATTERNS.search(job.role):
        return FilterResult(False, f'role too junior: {job.role!r}')

    # 3. Outside UK (location hard signal only — not a soft score)
    if _is_outside_uk(job.location_raw):
        return FilterResult(False, f'outside UK: {job.location_raw!r}')

    # 4. Salary hard floor — only reject if salary is explicit and clearly below floor
    salary_check = _check_salary(job.salary_raw)
    if salary_check is not None and salary_check < _SALARY_FLOOR:
        return FilterResult(False, f'salary below floor: {job.salary_raw!r} → ~£{salary_check:,.0f}')

    return FilterResult(True)


def _is_outside_uk(location: str) -> bool:
    """Return True only if location is clearly and explicitly outside the UK."""
    if not location:
        return False
    loc = location.lower()
    # Explicit non-UK signals
    outside_signals = [
        'united states', 'usa', 'u.s.a', ' us ', '(us)', 'new york', 'san francisco',
        'germany', 'france', 'paris', 'berlin', 'amsterdam', 'dubai', 'singapore',
        'australia', 'canada', 'india', 'remote - us', 'remote - eu',
        # PF3: Ireland/Dublin — not UK
        'dublin', 'ireland', 'republic of ireland', ' ie ', '(ie)',
    ]
    return any(s in loc for s in outside_signals)


def _check_salary(salary_raw: str) -> float | None:
    """
    Parse salary_raw and return an annual GBP figure, or None if unparseable.
    Returns None (not a rejection) for 'competitive', empty strings, OTE-only, etc.
    """
    if not salary_raw:
        return None

    s = salary_raw.lower()

    # OTE anywhere in salary string → uncertain (we can't isolate base salary)
    # e.g. "£50K OTE", "OTE £50K", "£30K base + £50K OTE" — all treated as uncertain
    # The KPI scorer gets salary_viability=5 for these; it never rejects on uncertainty
    if 'ote' in s:
        return None

    # 'Competitive', 'negotiable', etc. — uncertain, don't reject
    if any(x in s for x in ['competitive', 'negotiable', 'market rate', 'doe', 'tbc', 'tba']):
        return None

    # Hourly rate → annual
    m = _HOURLY_RE.search(salary_raw)
    if m:
        hourly = float(m.group(1).replace(',', ''))
        return hourly * _ANNUAL_HOURS

    # Range — use lower bound (conservative)
    m = _RANGE_RE.search(salary_raw)
    if m:
        low = float(m.group(1).replace(',', ''))
        # Handle 'k' shorthand: 30k → 30000
        if low < 1_000:
            low *= 1_000
        return low

    # Single annual figure
    m = _ANNUAL_RE.search(salary_raw)
    if m:
        val = float(m.group(1).replace(',', ''))
        if val < 1_000:
            val *= 1_000
        return val

    return None
