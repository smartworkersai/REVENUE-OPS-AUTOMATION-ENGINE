"""
scrapers/totaljobs.py
Purpose: TotalJobs.com scraper — curl_cffi + BeautifulSoup, no browser needed.
Created: 2026-03-25
Last Modified: 2026-03-25

TotalJobs serves server-side rendered HTML. curl_cffi with Chrome TLS
impersonation is sufficient — no Playwright, no proxy needed.

Search URL:
  https://www.totaljobs.com/jobs/{keyword-slug}-jobs?postedWithin={days}&distance=15&location=London&page={n}

  Keywords are slugified: "marketing analyst" → "marketing-analyst".
  25 cards per page. Pagination via &page=N param.

Two-pass strategy:
  Pass 1: collect card stubs (title, company, location, salary, date, URL)
          from search result pages.
  Pass 2: fetch each job's detail page to extract the full JD text.

Selectors verified 2026-03-25:
  Search page:
    Cards:    article[data-genesis-element="CARD"] containing a[data-at="job-item-title"]
    Title:    a[data-at="job-item-title"]
    URL:      a[data-at="job-item-title"][href]  → prepend https://www.totaljobs.com
    Company:  span[data-at="job-item-company-name"]
    Location: span[data-at="job-item-location"]
    Salary:   span[data-at="job-item-salary-info"]
    Date:     span[data-at="job-item-timeago"] (text: "2 weeks ago", "5 days ago")

  Detail page:
    Title:    h1
    Company:  .at-listing__list-icons_company-name
    Location: .at-listing__list-icons_location
    Salary:   .at-listing__list-icons_salary
    Date:     .at-listing__list-icons_date
    JD body:  .at-section-text-jobDescription-content
"""

# --- Imports ---

import logging
import re
from datetime import datetime, timezone, timedelta

import dateparser
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from utils.dom import html_to_markdown
from utils.rate_limit import short_delay, think_delay
from .base import BaseScraper, Job

# --- Constants ---

log = logging.getLogger(__name__)

_BASE_URL       = 'https://www.totaljobs.com'
_SEARCH_TPL     = 'https://www.totaljobs.com/jobs/{slug}-jobs'
_CARDS_PER_PAGE = 25
_IMPERSONATE    = 'chrome124'

_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-GB,en;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

_SLUG_RE = re.compile(r'[^a-z0-9]+')

# "2 weeks ago" → subtract from now; dateparser handles this with RELATIVE_BASE
_RELATIVE_DATE_RE = re.compile(
    r'(\d+)\s+(day|week|month|hour)s?\s+ago',
    re.IGNORECASE,
)


# --- Functions ---

def _slugify(keyword: str) -> str:
    """'Marketing Analyst' → 'marketing-analyst'"""
    return _SLUG_RE.sub('-', keyword.lower()).strip('-')


def _parse_relative_date(text: str, now: datetime) -> datetime | None:
    """Parse 'N days ago', 'N weeks ago' etc. relative to now (timezone-aware)."""
    m = _RELATIVE_DATE_RE.search(text)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    deltas = {'hour': timedelta(hours=n), 'day': timedelta(days=n),
              'week': timedelta(weeks=n), 'month': timedelta(days=n * 30)}
    return now - deltas.get(unit, timedelta(0))


# --- Classes ---

class TotalJobsScraper(BaseScraper):

    def scrape(
        self,
        keywords: list[str],
        location: str = 'London',
        days: int = 7,
        max_results: int = 50,
    ) -> list[Job]:

        session = cffi_requests.Session(impersonate=_IMPERSONATE)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        all_jobs: list[Job] = []

        max_per_keyword = max(1, max_results // len(keywords)) if keywords else max_results

        for keyword in keywords:
            try:
                batch = self._search_keyword(
                    session, keyword, location, days, now, cutoff,
                    limit=max_per_keyword,
                )
                all_jobs.extend(batch)
                log.info('TotalJobs: "%s" → %d listings', keyword, len(batch))
            except Exception as exc:
                log.warning('TotalJobs: keyword "%s" failed: %s', keyword, exc)
            think_delay()

        deduped = self._dedup(all_jobs)[:max_results]
        if not deduped:
            log.warning(
                'TotalJobs: 0 jobs returned for keywords=%s location=%s days=%d',
                keywords, location, days,
            )
        else:
            log.info('TotalJobs: %d total unique listings', len(deduped))
        return deduped

    # ------------------------------------------------------------------
    # Pass 1: collect stubs from search result pages
    # ------------------------------------------------------------------

    def _search_keyword(
        self,
        session: cffi_requests.Session,
        keyword: str,
        location: str,
        days: int,
        now: datetime,
        cutoff: datetime,
        limit: int,
    ) -> list[Job]:

        slug = _slugify(keyword)
        base_url = _SEARCH_TPL.format(slug=slug)
        stubs: list[dict] = []
        seen_urls: set[str] = set()
        page = 1

        while len(stubs) < limit:
            params = f'postedWithin={days}&distance=15&location={location}&page={page}'
            url = f'{base_url}?{params}'

            try:
                resp = session.get(url, headers=_HEADERS, timeout=20)
            except Exception as exc:
                log.warning('TotalJobs: request failed (page %d): %s', page, exc)
                break

            if resp.status_code != 200:
                log.warning('TotalJobs: HTTP %d for "%s" page %d', resp.status_code, keyword, page)
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            cards = self._extract_job_cards(soup)

            if not cards:
                log.debug('TotalJobs: no job cards on page %d for "%s"', page, keyword)
                break

            new_on_page = 0
            for card in cards:
                if len(stubs) >= limit:
                    break
                stub = self._parse_card_stub(card, now, cutoff, seen_urls)
                if stub:
                    stubs.append(stub)
                    seen_urls.add(stub['url'])
                    new_on_page += 1

            if new_on_page == 0 or len(cards) < _CARDS_PER_PAGE:
                break

            page += 1
            short_delay()

        # Pass 2: fetch JD for each stub
        jobs: list[Job] = []
        for stub in stubs:
            jd_text = self._fetch_jd(session, stub['url'])
            jobs.append(Job(
                company=stub['company'],
                role=stub['role'],
                url=stub['url'],
                source='TotalJobs',
                jd_text=jd_text,
                salary_raw=stub['salary_raw'],
                location_raw=stub['location_raw'],
                date_posted=stub['date_posted'],
            ))
            short_delay()

        return jobs

    # ------------------------------------------------------------------
    # Card helpers
    # ------------------------------------------------------------------

    def _extract_job_cards(self, soup: BeautifulSoup) -> list:
        """Return only article cards that contain a job-item-title link."""
        cards = soup.select('article[data-genesis-element="CARD"]')
        return [c for c in cards if c.find('a', attrs={'data-at': 'job-item-title'})]

    def _parse_card_stub(
        self,
        card,
        now: datetime,
        cutoff: datetime,
        seen_urls: set,
    ) -> dict | None:

        # Title + URL — use stable data-at attribute
        title_el = card.find('a', attrs={'data-at': 'job-item-title'})
        if not title_el:
            return None
        role = title_el.get_text(strip=True)
        href = title_el.get('href', '').strip()
        if not href:
            return None
        job_url = _BASE_URL + href.split('?')[0]  # strip tracking params
        if job_url in seen_urls:
            return None

        # Company
        company_el = card.find('span', attrs={'data-at': 'job-item-company-name'})
        company = company_el.get_text(strip=True) if company_el else 'Unknown'

        # Location
        loc_el = card.find('span', attrs={'data-at': 'job-item-location'})
        location_raw = loc_el.get_text(strip=True) if loc_el else ''

        # Salary
        sal_el = card.find('span', attrs={'data-at': 'job-item-salary-info'})
        salary_raw = sal_el.get_text(strip=True) if sal_el else ''

        # Date — relative text e.g. "2 weeks ago"
        date_posted = None
        date_el = card.find('span', attrs={'data-at': 'job-item-timeago'})
        if not date_el:
            date_el = card.find('time')
        if date_el:
            date_text = date_el.get_text(strip=True)
            parsed = _parse_relative_date(date_text, now)
            if parsed:
                if parsed < cutoff:
                    return None  # too old — skip
                date_posted = parsed.isoformat()

        return {
            'role': role,
            'url': job_url,
            'company': company,
            'location_raw': location_raw,
            'salary_raw': salary_raw,
            'date_posted': date_posted,
        }

    # ------------------------------------------------------------------
    # Pass 2: fetch full JD from detail page
    # ------------------------------------------------------------------

    def _fetch_jd(self, session: cffi_requests.Session, job_url: str) -> str:
        try:
            resp = session.get(job_url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                return ''
            soup = BeautifulSoup(resp.text, 'html.parser')
            jd_el = soup.select_one('.at-section-text-jobDescription-content')
            if jd_el:
                return html_to_markdown(str(jd_el))
        except Exception as exc:
            log.debug('TotalJobs: JD fetch failed for %s: %s', job_url, exc)
        return ''
