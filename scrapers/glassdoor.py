"""
Glassdoor scraper — camoufox browser routed through Decodo residential proxy.

Glassdoor uses reCAPTCHA v3 with aggressive bot scoring. Residential proxies
are required for reliable access. Without DECODO_PROXY_URL this scraper exits
immediately and logs a warning.

Config
------
.env:        DECODO_PROXY_URL=http://user:pass@gb.decodo.com:30001
config.yaml: sources.glassdoor: true

Selectors target Glassdoor's React DOM (2025-Q1). Class names change on deploy.
Set LOG_LEVEL=DEBUG to inspect raw HTML when selectors break.
"""

import logging
import os
import socket
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, quote_plus, urlparse

import dateparser

from utils.browser import new_browser, new_page
from utils.dom import html_to_markdown
from utils.rate_limit import page_delay, think_delay, short_delay
from .base import BaseScraper, Job

log = logging.getLogger(__name__)

_SEARCH_URL = 'https://www.glassdoor.co.uk/Job/jobs.htm'
_HOME_URL   = 'https://www.glassdoor.co.uk'

# Card selectors — update if Glassdoor rewrites class names
_CARD_SELECTORS  = [
    'li[data-jobid]',
    'li[data-test="jobListing"]',
    'li.JobsList_jobListItem__wjTHv',
    'li[class*="jobListItem"]',
]
_TITLE_SELECTORS = [
    'a[data-test="job-title"]',
    'a[class*="jobTitle"]',
    '[class*="jobTitle"] a',
    'a[class*="JobCard_seoLink"]',
]
_COMPANY_SELECTORS = [
    '[data-test="employer-name"]',
    '[class*="EmployerProfile_employerName"]',
    '[class*="jobEmployer"]',
    '[class*="companyName"]',
]
_LOCATION_SELECTORS = [
    '[data-test="emp-location"]',
    '[class*="jobLocation"]',
    '[class*="location"]',
]
_SALARY_SELECTORS = [
    '[data-test="detailSalary"]',
    '[class*="salary"]',
    '[class*="Salary"]',
]
_JD_SELECTORS = [
    '[class*="JobDetails_jobDescription"]',
    '[id*="JobDescriptionContainer"]',
    '[class*="jobDescription"]',
    '[class*="desc"]',
    '#JobDescriptionContainer',
]


def _check_proxy_alive(proxy_url: str, timeout: int = 5) -> bool:
    """TCP connect check to verify proxy host:port is reachable."""
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname
        port = parsed.port or 3001
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception as exc:
        log.warning('Glassdoor: proxy health check failed (%s:%s): %s', parsed.hostname if 'parsed' in dir() else '?', port if 'port' in dir() else '?', exc)
        return False


class GlassdoorScraper(BaseScraper):

    def scrape(
        self,
        keywords: list[str],
        location: str = 'London',
        days: int = 7,
        max_results: int = 50,
    ) -> list[Job]:
        proxy = os.getenv('DECODO_PROXY_URL', '')

        if not proxy:
            log.warning(
                'Glassdoor: DECODO_PROXY_URL not set. '
                'reCAPTCHA v3 will block non-proxy sessions. Skipping Glassdoor this run.'
            )
            return []

        if not _check_proxy_alive(proxy):
            log.warning(
                'Glassdoor: proxy %s is unreachable. Skipping Glassdoor this run.',
                proxy.split('@')[-1] if '@' in proxy else proxy,
            )
            return []

        camoufox, browser, context = new_browser(headless=True, proxy=proxy)
        try:
            page = new_page(context)

            # Land on homepage first — warmer cookie state
            page.goto(_HOME_URL, wait_until='domcontentloaded', timeout=30_000)
            think_delay()

            if self._is_captcha_page(page):
                log.warning(
                    'Glassdoor: CAPTCHA on homepage despite proxy. '
                    'Session may be flagged — skipping this run.'
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
                    log.info('Glassdoor: "%s" → %d listings', keyword, len(batch))
                except CaptchaBlock:
                    log.warning('Glassdoor: CAPTCHA mid-scrape — stopping Glassdoor this run.')
                    break
                except Exception as exc:
                    log.warning('Glassdoor: keyword "%s" failed: %s', keyword, exc)
                page_delay()

            deduped = self._dedup(jobs)[:max_results]
            log.info('Glassdoor: %d total unique listings', len(deduped))
            return deduped

        finally:
            context.close()
            camoufox.__exit__(None, None, None)

    def _is_captcha_page(self, page) -> bool:
        title = page.title().lower()
        url   = page.url.lower()
        return (
            'captcha' in title or 'robot' in title or 'blocked' in title
            or 'captcha' in url or '/challenge' in url
        )

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
            'sc.keyword': keyword,
            'locT': 'C',
            'locId': '2643743',    # London city ID on Glassdoor
            'jobType': '',
            'fromAge': str(days),
            'sortBy': 'date',
        }
        url = f'{_SEARCH_URL}?{urlencode(params)}'
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        think_delay()

        if self._is_captcha_page(page):
            raise CaptchaBlock()

        # Wait for React hydration
        try:
            page.wait_for_selector(
                ', '.join(_CARD_SELECTORS),
                timeout=8_000,
            )
        except Exception:
            title = page.title()
            html_snippet = page.content()[:500]
            log.warning('Glassdoor: job cards did not appear for "%s". Title: "%s" | HTML: %s', keyword, title, html_snippet)
            return []

        jobs: list[Job] = []
        processed_urls: set[str] = set()

        cards = self._find_cards(page)
        if not cards:
            title = page.title()
            html_snippet = page.content()[:500]
            log.warning('Glassdoor: no cards matched any selector for "%s". Title: "%s" | HTML: %s', keyword, title, html_snippet)
            return []

        for card in cards[:limit]:
            try:
                stub = self._parse_card_stub(card, cutoff)
                if not stub or stub['url'] in processed_urls:
                    continue
                processed_urls.add(stub['url'])

                jd_text = self._fetch_jd(page, stub['url'])
                short_delay()

                jobs.append(Job(
                    company      = stub['company'],
                    role         = stub['role'],
                    url          = stub['url'],
                    source       = 'Glassdoor',
                    jd_text      = jd_text,
                    salary_raw   = stub['salary_raw'],
                    location_raw = stub['location_raw'],
                    date_posted  = stub['date_posted'],
                ))
            except Exception as exc:
                log.debug('Glassdoor: card parse error: %s', exc)

        return jobs

    def _find_cards(self, page) -> list:
        for sel in _CARD_SELECTORS:
            cards = page.query_selector_all(sel)
            if cards:
                log.debug('Glassdoor: card selector "%s" matched %d cards', sel, len(cards))
                return cards
        return []

    def _parse_card_stub(self, card, cutoff: datetime) -> dict | None:
        # Job URL and title
        link_el = None
        for sel in _TITLE_SELECTORS:
            link_el = card.query_selector(sel)
            if link_el:
                break
        if not link_el:
            return None

        role = link_el.inner_text().strip()
        href = link_el.get_attribute('href') or ''
        if not href:
            return None
        job_url = href if href.startswith('http') else f'{_HOME_URL}{href}'

        # Remove tracking params from URL
        if '?' in job_url:
            job_url = job_url.split('?')[0]

        # Company
        company = ''
        for sel in _COMPANY_SELECTORS:
            el = card.query_selector(sel)
            if el:
                company = el.inner_text().strip()
                break
        company = company or 'Unknown'

        # Location
        location_raw = ''
        for sel in _LOCATION_SELECTORS:
            el = card.query_selector(sel)
            if el:
                location_raw = el.inner_text().strip()
                break

        # Salary
        salary_raw = ''
        for sel in _SALARY_SELECTORS:
            el = card.query_selector(sel)
            if el:
                salary_raw = el.inner_text().strip()
                break

        # Date — Glassdoor typically shows "2d ago", "Today", etc.
        date_posted = None
        for date_sel in ['[data-test="job-age"]', '[class*="jobAge"]', '[class*="listingAge"]']:
            el = card.query_selector(date_sel)
            if el:
                date_text = el.inner_text().strip()
                parsed = dateparser.parse(date_text, settings={'RETURN_AS_TIMEZONE_AWARE': True})
                if parsed:
                    if parsed < cutoff:
                        return None   # too old
                    date_posted = parsed.astimezone(timezone.utc).isoformat()
                break

        return {
            'company':      company,
            'role':         role,
            'url':          job_url,
            'salary_raw':   salary_raw,
            'location_raw': location_raw,
            'date_posted':  date_posted,
        }

    def _fetch_jd(self, page, job_url: str) -> str:
        """Navigate to job page and extract description text."""
        try:
            page.goto(job_url, wait_until='domcontentloaded', timeout=15_000)
            think_delay()

            if self._is_captcha_page(page):
                log.debug('Glassdoor: CAPTCHA on JD page %s', job_url)
                return ''

            for sel in _JD_SELECTORS:
                el = page.query_selector(sel)
                if el:
                    return html_to_markdown(el.inner_html())
        except Exception as exc:
            log.debug('Glassdoor: JD fetch failed for %s: %s', job_url, exc)
        return ''


class CaptchaBlock(Exception):
    """Raised when Glassdoor CAPTCHA is detected mid-scrape."""
