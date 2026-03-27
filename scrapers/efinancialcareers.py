"""
eFinancialCareers scraper — curl_cffi + BeautifulSoup, no browser needed.

eFinancialCareers (efinancialcareers.co.uk) serves Angular SSR HTML: the search
results page is fully server-side rendered and contains all job card data without
requiring JavaScript execution. curl_cffi with Chrome TLS impersonation is
sufficient — no Playwright, no proxy needed.

Search URL: https://www.efinancialcareers.co.uk/jobs/search?q={keyword}&location={location}&radius={radius}&page={page}
20 cards per page. Pagination via ?page= param.

Two-pass strategy (mirrors linkedin.py):
  Pass 1: collect card stubs (title, company, location, salary, date, URL) from
          search result pages.
  Pass 2: fetch each job's detail page to extract the full JD text from the
          <efc-job-description> element.

Selectors (verified 2026-03-23):
  Cards:    efc-job-card
  Title:    a.job-title  (href = full job URL)
  Company:  div[class*="company"]  (class "font-body-3 company col")
  Location: div[class*="location"] > span:first-child
  Salary:   span.last-job-criteria
  Date:     efc-job-meta → span[class*="dot-divider"]
  JD:       efc-job-description
"""

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import dateparser
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from utils.dom import html_to_markdown
from utils.rate_limit import short_delay, think_delay
from .base import BaseScraper, Job

_APPLY_INFO_BASE = 'https://job-application.efinancialcareers.com/v1/jobs'

log = logging.getLogger(__name__)

_SEARCH_BASE = 'https://www.efinancialcareers.co.uk/jobs/search'
_CARDS_PER_PAGE = 20
_IMPERSONATE = 'chrome124'

_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-GB,en;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


class EFinancialCareersScraper(BaseScraper):

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
        # e.g. max_results=150, 13 keywords → 11 per keyword (rounds down).
        max_per_keyword = max(1, max_results // len(keywords)) if keywords else max_results

        for keyword in keywords:
            try:
                batch = self._search_keyword(
                    session, keyword, location, days, cutoff,
                    limit=max_per_keyword,
                )
                all_jobs.extend(batch)
                log.info('eFinancialCareers: "%s" → %d listings', keyword, len(batch))
            except Exception as exc:
                log.warning('eFinancialCareers: keyword "%s" failed: %s', keyword, exc)
            think_delay()

        deduped = self._dedup(all_jobs)[:max_results]
        if not deduped:
            log.warning(
                'eFinancialCareers: 0 jobs returned for keywords=%s location=%s days=%d',
                keywords, location, days,
            )
        else:
            log.info('eFinancialCareers: %d total unique listings', len(deduped))
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
        cutoff: datetime,
        limit: int,
    ) -> list[Job]:

        stubs: list[dict] = []
        seen_urls: set[str] = set()
        page = 1

        while len(stubs) < limit:
            params = {
                'q': keyword,
                'location': location,
                'radius': '20',
                'page': str(page),
            }
            url = f'{_SEARCH_BASE}?{urlencode(params)}'

            try:
                resp = session.get(url, headers=_HEADERS, timeout=20)
            except Exception as exc:
                log.warning('eFinancialCareers: request failed (page %d): %s', page, exc)
                break

            if resp.status_code != 200:
                log.warning('eFinancialCareers: HTTP %d for "%s" page %d', resp.status_code, keyword, page)
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.find_all('efc-job-card')

            if not cards:
                log.debug('eFinancialCareers: no cards on page %d for "%s"', page, keyword)
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

            # Stop paging if no new stubs (all old or deduped) or fewer than a full page
            if new_on_page == 0 or len(cards) < _CARDS_PER_PAGE:
                break

            page += 1
            short_delay()

        # Pass 2: fetch JD + application URL for each stub
        jobs: list[Job] = []
        for stub in stubs:
            jd_text = self._fetch_jd(session, stub['url'])
            application_url, login_required = self._fetch_application_url(session, stub.get('gtm_id', ''))
            jobs.append(Job(
                company=stub['company'],
                role=stub['role'],
                url=stub['url'],
                source='eFinancialCareers',
                jd_text=jd_text,
                salary_raw=stub['salary_raw'],
                location_raw=stub['location_raw'],
                date_posted=stub['date_posted'],
                extra={
                    'application_url': application_url,
                    'login_required': login_required,
                },
            ))
            short_delay()

        return jobs

    # ------------------------------------------------------------------
    # Card stub parser
    # ------------------------------------------------------------------

    def _parse_card_stub(self, card, cutoff: datetime, seen_urls: set) -> dict | None:
        # GTM job ID (used for apply-information API)
        gtm_id = card.get('data-gtm-id', '') or card.get('gtm-id', '')

        # Title + URL
        title_el = card.find('a', class_='job-title')
        if not title_el:
            return None
        role = title_el.get_text(strip=True)
        href = title_el.get('href', '').strip()
        if not href:
            return None
        job_url = href if href.startswith('http') else f'https://www.efinancialcareers.co.uk{href}'
        if job_url in seen_urls:
            return None

        # Company
        company_el = card.find(class_='company')
        company = company_el.get_text(strip=True) if company_el else 'Unknown'

        # Location — first span inside .location div
        loc_el = card.find(class_='location')
        location_raw = ''
        if loc_el:
            first_span = loc_el.find('span')
            location_raw = first_span.get_text(strip=True) if first_span else loc_el.get_text(separator=' ', strip=True)

        # Salary — span with class containing "last-job-criteria"
        sal_el = card.find('span', class_='last-job-criteria')
        salary_raw = sal_el.get_text(strip=True) if sal_el else ''

        # Date — efc-job-meta contains "4 days ago", "Today", etc.
        date_posted = None
        meta_el = card.find('efc-job-meta')
        if meta_el:
            date_text = meta_el.get_text(strip=True)
            if date_text:
                parsed = dateparser.parse(date_text, settings={'RETURN_AS_TIMEZONE_AWARE': True})
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
            'gtm_id': gtm_id,
        }

    # ------------------------------------------------------------------
    # Pass 2: fetch application URL via apply-information API
    # ------------------------------------------------------------------

    def _fetch_application_url(self, session: cffi_requests.Session, gtm_id: str) -> tuple[str | None, bool]:
        """
        Call the eFinancialCareers apply-information API.
        Returns (external_ats_url, login_required).
          - external_ats_url: URL string for external jobs, None for internal / failures.
          - login_required: True when the job requires an eFC account to apply.
        """
        if not gtm_id:
            return None, False
        api_url = f'{_APPLY_INFO_BASE}/{gtm_id}/apply-information'
        try:
            resp = session.get(api_url, headers=_HEADERS, timeout=10)
            if resp.status_code != 200:
                log.debug('eFinancialCareers: apply-information API HTTP %d for gtm_id=%s', resp.status_code, gtm_id)
                return None, False
            data = resp.json()
            info = data.get('data', {})
            login_required = bool(info.get('login_required'))
            if info.get('is_external_job_application'):
                url = info.get('external_job_application_url') or ''
                if url:
                    log.debug('eFinancialCareers: external ATS → %s', url[:80])
                    return url, login_required
            return None, login_required
        except Exception as exc:
            log.debug('eFinancialCareers: apply-information failed for gtm_id=%s: %s', gtm_id, exc)
        return None, False

    # ------------------------------------------------------------------
    # Pass 2: fetch full JD
    # ------------------------------------------------------------------

    def _fetch_jd(self, session: cffi_requests.Session, job_url: str) -> str:
        try:
            resp = session.get(job_url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                return ''
            soup = BeautifulSoup(resp.text, 'html.parser')
            jd_el = soup.find('efc-job-description')
            if jd_el:
                return html_to_markdown(str(jd_el))
        except Exception as exc:
            log.debug('eFinancialCareers: JD fetch failed for %s: %s', job_url, exc)
        return ''
