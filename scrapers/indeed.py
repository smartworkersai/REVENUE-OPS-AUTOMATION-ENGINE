"""
Indeed scraper — camoufox browser with Cloudflare detection.

Reality check
-------------
Indeed UK (indeed.co.uk → uk.indeed.com) is behind Cloudflare with aggressive
bot detection. Without residential proxies, most scraping sessions are challenged.

# TODO (backlog): spec preference was curl_cffi for Indeed (JA3/TLS spoof, no
# browser overhead). Switch back once INDEED_PROXY_URL is configured — curl_cffi
# + residential proxy bypasses Cloudflare without launching a full browser.
# See spec Section 4 "Anti-detection". Current camoufox approach is a working
# fallback but is slower and still challenged without proxies.

This scraper attempts a human-like navigation pattern (homepage first, then
search). If Cloudflare challenges the session, it logs a clear warning and
returns empty results — it does NOT crash the pipeline.

To reliably use Indeed, set INDEED_PROXY_URL in .env.

Selectors target Indeed's current React-rendered DOM (as of 2025-Q1).
Indeed rewrites class names frequently. On selector failure, set LOG_LEVEL=DEBUG
to inspect raw HTML and update the selector constants below.
"""

import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import dateparser
from bs4 import BeautifulSoup

from utils.browser import new_browser, new_page
from utils.dom import html_to_markdown
from utils.rate_limit import page_delay, think_delay, short_delay
from .base import BaseScraper, Job

log = logging.getLogger(__name__)

_HOME_URL   = 'https://uk.indeed.com'
_SEARCH_URL = 'https://uk.indeed.com/jobs'

# Update these if Indeed rewrites class names
_CARD_SELECTORS   = ['div.job_seen_beacon', 'div[data-jk]', 'div.resultContent', 'td.resultContent']
_TITLE_SELECTORS  = ['h2.jobTitle a', 'a[data-jk]', 'h2 a[id^="jobTitle"]']
_CO_SELECTORS     = ['[data-testid="company-name"]', '.companyName', 'span[class*="company"]']
_LOC_SELECTORS    = ['[data-testid="text-location"]', '.companyLocation', 'div[class*="location"]']
_SAL_SELECTORS    = ['[data-testid="attribute_snippet_testid"]', '.salary-snippet-container', 'div[class*="salary"]']
_DATE_SELECTORS   = ['[data-testid="myJobsStateDate"]', '.date', 'span[class*="date"]']


class IndeedScraper(BaseScraper):

    def scrape(
        self,
        keywords: list[str],
        location: str = 'London',
        days: int = 7,
        max_results: int = 50,
    ) -> list[Job]:

        proxy = os.getenv('INDEED_PROXY_URL') or None
        camoufox, browser, context = new_browser(headless=True, proxy=proxy)
        try:
            page = new_page(context)

            # Warm up: land on homepage first (more human-like)
            page.goto(_HOME_URL, wait_until='domcontentloaded', timeout=30_000)
            think_delay()

            if self._is_cloudflare_challenge(page):
                log.warning(
                    'Indeed: Cloudflare challenge detected on homepage. '
                    'Set INDEED_PROXY_URL in .env for reliable access. '
                    'Skipping Indeed this run.'
                )
                return []

            jobs: list[Job] = []
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            for keyword in keywords:
                if len(jobs) >= max_results:
                    break
                try:
                    batch = self._search_keyword(page, keyword, location, days, cutoff, max_results - len(jobs))
                    jobs.extend(batch)
                    log.info('Indeed: "%s" → %d listings', keyword, len(batch))
                except CloudflareBlock:
                    log.warning('Indeed: Cloudflare block mid-scrape. Stopping Indeed this run.')
                    break
                except Exception as exc:
                    log.warning('Indeed: keyword "%s" failed: %s', keyword, exc)
                page_delay()

            deduped = self._dedup(jobs)[:max_results]
            log.info('Indeed: %d total unique listings', len(deduped))
            return deduped

        finally:
            context.close()
            camoufox.__exit__(None, None, None)

    def _is_cloudflare_challenge(self, page) -> bool:
        title = page.title().lower()
        return 'just a moment' in title or 'cloudflare' in title

    def _search_keyword(
        self,
        page,
        keyword: str,
        location: str,
        days: int,
        cutoff: datetime,
        limit: int,
    ) -> list[Job]:

        params = {
            'q': keyword,
            'l': location,
            'fromage': str(days),
            'sort': 'date',
            'limit': '50',
        }
        url = f'{_SEARCH_URL}?{urlencode(params)}'
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        think_delay()

        if self._is_cloudflare_challenge(page):
            raise CloudflareBlock()

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')

        # Find job cards using any matching selector
        cards = []
        for sel in _CARD_SELECTORS:
            cards = soup.select(sel)
            if cards:
                log.debug('Indeed: card selector "%s" matched %d cards', sel, len(cards))
                break

        if not cards:
            log.debug('Indeed: no cards found. Page title: %s', page.title())
            log.debug('Indeed: HTML snippet: %s', html[500:1500])
            return []

        jobs: list[Job] = []
        for card in cards[:limit]:
            try:
                job = self._parse_card(page, card, cutoff, soup)
                if job:
                    jobs.append(job)
                    short_delay()
            except Exception as exc:
                log.debug('Indeed: card parse error: %s', exc)

        return jobs

    def _select_text(self, element, selectors: list[str]) -> str:
        for sel in selectors:
            el = element.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return ''

    def _parse_card(self, page, card, cutoff: datetime, soup) -> Job | None:
        # Title + URL
        title_el = None
        for sel in _TITLE_SELECTORS:
            title_el = card.select_one(sel)
            if title_el:
                break
        if not title_el:
            return None

        role = title_el.get_text(strip=True)
        href = title_el.get('href', '')
        if not href:
            return None

        job_url = href if href.startswith('http') else f'{_HOME_URL}{href}'

        company    = self._select_text(card, _CO_SELECTORS)  or 'Unknown'
        location_r = self._select_text(card, _LOC_SELECTORS)
        salary_r   = self._select_text(card, _SAL_SELECTORS)

        # Date
        date_posted = None
        date_text = self._select_text(card, _DATE_SELECTORS)
        if date_text:
            parsed = dateparser.parse(date_text, settings={'RETURN_AS_TIMEZONE_AWARE': True})
            if parsed:
                if parsed < cutoff:
                    return None
                date_posted = parsed.astimezone(timezone.utc).isoformat()

        # Fetch JD by clicking the card (loads detail pane)
        # Extract job key from href (e.g. ?jk=abc123) to build a safe CSS selector.
        # Never inject raw href into a selector — special chars break it.
        jd_text = ''
        try:
            jk_match = re.search(r'[?&]jk=([A-Za-z0-9]+)', href)
            if jk_match:
                card_link = page.query_selector(f'a[data-jk="{jk_match.group(1)}"]')
            else:
                card_link = page.query_selector('a[data-jk]')
            if card_link:
                card_link.click()
                page_delay()
                detail = page.query_selector('#jobDescriptionText, .jobsearch-JobComponent-description, div[class*="description"]')
                if detail:
                    jd_text = html_to_markdown(detail.inner_html())
        except Exception as exc:
            log.debug('Indeed: detail pane failed: %s', exc)

        return Job(
            company=company,
            role=role,
            url=job_url,
            source='Indeed',
            jd_text=jd_text,
            salary_raw=salary_r,
            location_raw=location_r,
            date_posted=date_posted,
        )


class CloudflareBlock(Exception):
    """Raised when Cloudflare challenge is detected mid-scrape."""
