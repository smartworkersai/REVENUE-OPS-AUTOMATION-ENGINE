"""
Job dataclass and BaseScraper abstract base class.

All scrapers must return a list of Job objects. The pipeline only
interacts with scrapers through this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Job:
    company: str
    role: str
    url: str
    source: str                         # 'LinkedIn' | 'Indeed' | 'Glassdoor' | 'Direct'
    jd_text: str = ''                   # full cleaned job description (markdown)
    salary_raw: str = ''                # as-scraped salary string
    location_raw: str = ''              # as-scraped location string
    date_posted: Optional[str] = None   # UTC ISO-8601 or None
    extra: dict = field(default_factory=dict)  # source-specific metadata


class BaseScraper(ABC):
    """
    Abstract scraper. Subclasses implement `scrape()` and return Job objects.
    The pipeline calls scrape() and expects a list — empty is acceptable.
    """

    @abstractmethod
    def scrape(self, keywords: list[str], location: str, days: int, max_results: int) -> list[Job]:
        """
        Scrape job listings.

        Parameters
        ----------
        keywords    : search terms to cycle through
        location    : target location string
        days        : only return jobs posted within this many days
        max_results : hard cap on returned jobs (across all keywords)

        Returns a deduplicated list of Job objects.
        """
        ...

    def _dedup(self, jobs: list[Job]) -> list[Job]:
        """Remove duplicate URLs within a scrape batch."""
        seen: set[str] = set()
        out: list[Job] = []
        for j in jobs:
            if j.url not in seen:
                seen.add(j.url)
                out.append(j)
        return out
