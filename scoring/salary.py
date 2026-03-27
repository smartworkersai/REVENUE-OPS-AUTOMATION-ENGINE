"""
SAL-1 through SAL-9: Dynamic salary expectation calculator.

Logic:
- If salary_raw has a stated range → use 80th percentile (low + 0.8 * range)
- If salary_viability score implies a band → use band midpoint
- Score-weighted: higher scores → ask slightly higher (within same band)
- Hard floor: £28,000 — never go below regardless of score
- Soft target: if clearly >= £32K role, ask £32K+
- Cap: don't ask more than the stated upper bound

Used by all submission handlers to fill salary expectation fields.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scoring.kpi import KPIScore
    from scrapers.base import Job

_SALARY_FLOOR = 28_000
_SALARY_DEFAULT = 32_000

_HOURLY_RE = re.compile(r'£?([\d,]+\.?\d*)\s*(?:per\s+hour|/\s*hr|p\.?h\.?)', re.IGNORECASE)
_RANGE_RE  = re.compile(r'£?([\d,]+)\s*(?:k\b)?[\s\-–]+£?([\d,]+)\s*(?:k\b)?', re.IGNORECASE)
_ANNUAL_RE = re.compile(r'£?([\d,]+)(?:k\b|,000)', re.IGNORECASE)
_ANNUAL_HOURS = 1_820


def _parse_stated_salary(salary_raw: str) -> tuple[float | None, float | None]:
    """
    Parse salary_raw into (low, high) annual GBP values.
    Returns (None, None) if unparseable or ambiguous (OTE, competitive, etc.).
    """
    if not salary_raw:
        return None, None

    s = salary_raw.lower()
    if any(x in s for x in ['ote', 'competitive', 'negotiable', 'market rate', 'doe', 'tbc', 'tba']):
        return None, None

    # Hourly → annual (single value)
    m = _HOURLY_RE.search(salary_raw)
    if m:
        hourly = float(m.group(1).replace(',', ''))
        annual = hourly * _ANNUAL_HOURS
        return annual, annual

    # Range: "£30,000 - £35,000" or "£30k - £35k"
    m = _RANGE_RE.search(salary_raw)
    if m:
        low  = float(m.group(1).replace(',', ''))
        high = float(m.group(2).replace(',', ''))
        if low < 1_000:
            low  *= 1_000
        if high < 1_000:
            high *= 1_000
        # Sanity check
        if low > high:
            low, high = high, low
        return low, high

    # Single annual
    m = _ANNUAL_RE.search(salary_raw)
    if m:
        val = float(m.group(1).replace(',', ''))
        if val < 1_000:
            val *= 1_000
        return val, val

    return None, None


def _salary_from_viability(viability_score: float, final_score: float) -> int:
    """
    Derive expected salary from the KPI salary_viability dimension score.
    Uses score-weighted adjustment within each band.

    Band mapping (mirrors kpi.py scoring rules):
      10  → >= £32K  → ask £32,000
      8   → £28-32K → ask midpoint £30,000, boosted for high final_score
      5   → uncertain → ask default £32,000 (do not under-ask)
      1-4 → likely below floor → still ask £28,000 (floor)
    """
    if viability_score >= 9.5:
        # Clearly >= £32K role — ask £32K
        return _SALARY_DEFAULT
    elif viability_score >= 7.5:
        # £28-32K band — ask £30K, boost to £31K for high-scoring roles
        boost = 1_000 if final_score >= 8.5 else 0
        return 30_000 + boost
    elif viability_score >= 4.5:
        # Uncertain — ask default
        return _SALARY_DEFAULT
    else:
        # Below floor band — still ask the floor
        return _SALARY_FLOOR


def calculate_expected_salary(job: 'Job', kpi: 'KPIScore | None') -> int:
    """
    SAL-1: Calculate the expected salary to state in application forms.

    Priority:
    1. Stated range → midpoint, capped to floor
    2. KPI salary_viability score → band midpoint
    3. Fallback → £32,000
    """
    from scrapers.base import Job as _Job  # avoid circular at module load

    salary_raw = getattr(job, 'salary_raw', '') or ''

    # Priority 1: stated range → 80th percentile (low + 0.8 * range)
    low, high = _parse_stated_salary(salary_raw)
    if low is not None and high is not None:
        p80 = int(low + 0.8 * (high - low))
        # Never ask below the floor, never ask more than stated high
        result = max(_SALARY_FLOOR, p80)
        if high > 0:
            result = min(result, int(high))
        return result

    # Priority 2: kpi salary_viability
    if kpi is not None:
        viability = kpi.salary_viability.score if hasattr(kpi, 'salary_viability') else 5.0
        final = kpi.final_score if hasattr(kpi, 'final_score') else 7.5
        return _salary_from_viability(viability, final)

    # Priority 3: default
    return _SALARY_DEFAULT
