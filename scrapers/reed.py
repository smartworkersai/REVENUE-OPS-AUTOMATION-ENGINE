"""
Reed.co.uk scraper — curl_cffi + BeautifulSoup, no browser needed.

Reed.co.uk serves server-side rendered HTML. curl_cffi with Chrome TLS
impersonation is sufficient — no Playwright, no proxy needed.

Search URL:
  https://www.reed.co.uk/jobs/{keyword-slug}-jobs-in-london?datecreatedoffset={days}&pageno={page}

  Keywords are slugified: "marketing analyst" → "marketing-analyst".
  25 cards per page. Pagination via &pageno= param.

Two-pass strategy:
  Pass 1: collect card stubs (title, company, location, salary, date, URL)
          from search result pages.
  Pass 2: fetch each job's detail page to extract the full JD text.

Selectors (verified 2026-03-23):
  Cards:    article[data-qa="job-card"]
  Title:    a[data-qa="job-card-title"]  (href = /jobs/title/ID?...)
  Company:  a.gtmJobListingPostedBy
  Date:     div[data-qa="job-posted-by"]  (text before " by ")
  Salary:   li[data-qa="job-metadata-salary"]
  Location: li[data-qa="job-metadata-location"]
  JD:       div[data-qa="job-description"]
"""

import logging
import re
from datetime import datetime, timezone, timedelta

import dateparser
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from utils.dom import html_to_markdown
from utils.rate_limit import short_delay, think_delay
from .base import BaseScraper, Job

log = logging.getLogger(__name__)

_BASE_URL      = 'https://www.reed.co.uk'
_SEARCH_TPL    = 'https://www.reed.co.uk/jobs/{slug}-jobs-in-london'
_CARDS_PER_PAGE = 25
_IMPERSONATE   = 'chrome124'

_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-GB,en;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

_SLUG_RE = re.compile(r'[^a-z0-9]+')


def _slugify(keyword: str) -> str:
    """'Marketing Analyst' → 'marketing-analyst'"""
    return _SLUG_RE.sub('-', keyword.lower()).strip('-')


class ReedScraper(BaseScraper):

    def scrape(
        self,
        keywords: list[str],
        location: str = 'London',
        days: int = 7,
        max_results: int = 50,
    ) -> list[Job]:

        session = cffi_requests.Session(impersonate=_IMPERSONATE)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        all_jobs: list[Job] = []

        # Per-keyword cap: divide the global budget evenly so every keyword runs.
        max_per_keyword = max(1, max_results // len(keywords)) if keywords else max_results

        for keyword in keywords:
            try:
                batch = self._search_keyword(
                    session, keyword, days, cutoff,
                    limit=max_per_keyword,
                )
                all_jobs.extend(batch)
                log.info('Reed: "%s" → %d listings', keyword, len(batch))
            except Exception as exc:
                log.warning('Reed: keyword "%s" failed: %s', keyword, exc)
            think_delay()

        deduped = self._dedup(all_jobs)[:max_results]
        if not deduped:
            log.warning(
                'Reed: 0 jobs returned for keywords=%s location=%s days=%d',
                keywords, location, days,
            )
        else:
            log.info('Reed: %d total unique listings', len(deduped))
        return deduped

    # ------------------------------------------------------------------
    # Pass 1: collect stubs from search result pages
    # ------------------------------------------------------------------

    def _search_keyword(
        self,
        session: cffi_requests.Session,
        keyword: str,
        days: int,
        cutoff: datetime,
        limit: int,
    ) -> list[Job]:

        slug = _slugify(keyword)
        base_url = _SEARCH_TPL.format(slug=slug)
        stubs: list[dict] = []
        seen_urls: set[str] = set()
        page = 1

        while len(stubs) < limit:
            params = f'datecreatedoffset={days}&pageno={page}'
            url = f'{base_url}?{params}'

            try:
                resp = session.get(url, headers=_HEADERS, timeout=20)
            except Exception as exc:
                log.warning('Reed: request failed (page %d): %s', page, exc)
                break

            if resp.status_code != 200:
                log.warning('Reed: HTTP %d for "%s" page %d', resp.status_code, keyword, page)
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.find_all('article', attrs={'data-qa': 'job-card'})

            if not cards:
                log.debug('Reed: no cards on page %d for "%s"', page, keyword)
                break

            new_on_page = 0
            for card in cards:
                if len(stubs) >= limit:
                    break
                stub = self._parse_card_stub(card, cutoff, seen_urls)
                if stub:
                    stubs.append(stub)
                    seen_urls.add(stub['url'])
                    new_on_page += 1

            # Stop paging if no new stubs or fewer than a full page
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
                source='Reed',
                jd_text=jd_text,
                salary_raw=stub['salary_raw'],
                location_raw=stub['location_raw'],
                date_posted=stub['date_posted'],
                extra={'requires_account': True},
            ))
            short_delay()

        return jobs

    # ------------------------------------------------------------------
    # Card stub parser
    # ------------------------------------------------------------------

    def _parse_card_stub(self, card, cutoff: datetime, seen_urls: set) -> dict | None:
        # Title + URL
        title_el = card.find('a', attrs={'data-qa': 'job-card-title'})
        if not title_el:
            return None
        role = title_el.get_text(strip=True)
        href = title_el.get('href', '').strip()
        if not href:
            return None
        # Strip tracking params — keep only /jobs/title/ID
        job_url = _BASE_URL + href.split('?')[0]
        if job_url in seen_urls:
            return None

        # Company — anchor with gtmJobListingPostedBy class
        company_el = card.find('a', class_='gtmJobListingPostedBy')
        company = company_el.get_text(strip=True) if company_el else 'Unknown'

        # Salary
        sal_el = card.find('li', attrs={'data-qa': 'job-metadata-salary'})
        salary_raw = sal_el.get_text(strip=True) if sal_el else ''

        # Location
        loc_el = card.find('li', attrs={'data-qa': 'job-metadata-location'})
        location_raw = loc_el.get_text(strip=True) if loc_el else ''

        # Date — text in "posted-by" div before " by [company]"
        date_posted = None
        posted_el = card.find('div', attrs={'data-qa': 'job-posted-by'})
        if posted_el:
            # Get the raw text of the element, which starts with the date e.g. "11 March by ..."
            full_text = posted_el.get_text(separator=' ', strip=True)
            # Extract everything before " by "
            date_text = full_text.split(' by ')[0].strip()
            if date_text:
                parsed = dateparser.parse(
                    date_text,
                    settings={
                        'RETURN_AS_TIMEZONE_AWARE': True,
                        'PREFER_DAY_OF_MONTH': 'first',
                    },
                )
                if parsed:
                    if parsed < cutoff:
                        return None  # too old — skip
                    date_posted = parsed.astimezone(timezone.utc).isoformat()

        return {
            'role': role,
            'url': job_url,
            'company': company,
            'location_raw': location_raw,
            'salary_raw': salary_raw,
            'date_posted': date_posted,
        }

    # ------------------------------------------------------------------
    # Pass 2: fetch full JD
    # ------------------------------------------------------------------

    def _fetch_jd(self, session: cffi_requests.Session, job_url: str) -> str:
        try:
            resp = session.get(job_url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                return ''
            soup = BeautifulSoup(resp.text, 'html.parser')
            jd_el = soup.find('div', attrs={'data-qa': 'job-description'})
            if jd_el:
                return html_to_markdown(str(jd_el))
        except Exception as exc:
            log.debug('Reed: JD fetch failed for %s: %s', job_url, exc)
        return ''
