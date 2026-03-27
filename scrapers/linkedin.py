"""
LinkedIn scraper — camoufox browser + session auth.

Strategy
--------
1. Check for existing persistent profile in LINKEDIN_PROFILE_DIR.
   If present, restore context and verify auth with a lightweight check.
2. If no session, raise LinkedInAuthRequired — the human must log in
   manually once and save cookies (see instructions in README).
3. Search jobs via LinkedIn's job search page using sync Playwright.
4. For each listing, open the detail panel and extract JD text.
5. Respect log-normal delays throughout.

LinkedIn blocks headless browsers at multiple layers:
- navigator.webdriver detection (patched by camoufox)
- TLS/JA3 fingerprinting (handled by camoufox's Firefox base)
- Canvas/WebGL fingerprinting (patched by camoufox)
- Behavioural analysis — log-normal delays mitigate this

Glassdoor is a separate scraper (disabled by default — requires proxies).
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

import dateparser

from utils.browser import new_browser, new_page, LINKEDIN_PROFILE_DIR
from utils.dom import html_to_markdown
from utils.rate_limit import page_delay, think_delay, short_delay
from .base import BaseScraper, Job

log = logging.getLogger(__name__)

_JOBS_URL = 'https://www.linkedin.com/jobs/search/'
_LOGIN_CHECK_URL = 'https://www.linkedin.com/feed/'


class LinkedInAuthRequired(Exception):
    """Raised when no valid LinkedIn session is available."""


class LinkedInScraper(BaseScraper):

    def __init__(self, profile_dir: str | None = None):
        self.profile_dir = profile_dir or os.getenv(
            'LINKEDIN_PROFILE_DIR', str(LINKEDIN_PROFILE_DIR)
        )

    def scrape(
        self,
        keywords: list[str],
        location: str = 'London',
        days: int = 7,
        max_results: int = 50,
    ) -> list[Job]:

        if not Path(self.profile_dir).exists():
            raise LinkedInAuthRequired(
                f'No LinkedIn profile found at {self.profile_dir}. '
                'Run: PYTHONPATH="." python test_browser.py to log in.'
            )

        camoufox, browser, context = new_browser(
            headless=True,
            user_data_dir=self.profile_dir,
        )
        try:
            page = new_page(context)
            self._verify_auth(page)

            jobs: list[Job] = []
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            for keyword in keywords:
                if len(jobs) >= max_results:
                    break
                try:
                    batch = self._search_keyword(page, keyword, location, days, cutoff, max_results - len(jobs))
                    jobs.extend(batch)
                    log.info('LinkedIn: "%s" → %d listings', keyword, len(batch))
                except Exception as exc:
                    log.warning('LinkedIn: keyword "%s" failed: %s', keyword, exc)
                page_delay()

            # Profile is written to disk automatically — no explicit save needed.
            result = self._dedup(jobs)[:max_results]
            if not result:
                log.warning(
                    'LinkedIn: 0 jobs found for keywords=%s location=%s days=%d. '
                    'Check session validity and broaden search terms if needed.',
                    keywords, location, days,
                )
            return result

        finally:
            context.close()
            camoufox.__exit__(None, None, None)

    def _verify_auth(self, page) -> None:
        """Check we're actually logged in before spending time scraping."""
        page.goto(_LOGIN_CHECK_URL, wait_until='domcontentloaded')
        page_delay()
        url = page.url or ''
        # Broader auth check: catch all login/authwall/checkpoint/uas redirects
        if any(s in url for s in [
            'linkedin.com/login',
            'linkedin.com/authwall',
            'linkedin.com/checkpoint',
            'linkedin.com/uas/',
            'linkedin.com/signup',
        ]):
            raise LinkedInAuthRequired(
                f'LinkedIn session expired or invalid (redirected to {url}). '
                f'Run: PYTHONPATH="." python test_browser.py to re-authenticate. '
                f'Profile dir: {self.profile_dir}'
            )
        # Also check if we landed on a non-feed page (sign-in prompt injected)
        try:
            if page.query_selector('[data-test-id="sign-in-form"], form[action*="/login"]'):
                raise LinkedInAuthRequired(
                    'LinkedIn sign-in form detected on feed page — session invalid. '
                    'Re-authenticate with: PYTHONPATH="." python test_browser.py'
                )
        except LinkedInAuthRequired:
            raise
        except Exception:
            pass
        log.info('LinkedIn: session valid')

    def _search_keyword(
        self,
        page,
        keyword: str,
        location: str,
        days: int,
        cutoff: datetime,
        limit: int,
    ) -> list[Job]:

        # LinkedIn time filter: 1=24h, 2=1 week, 3=1 month
        time_filter = 'r86400' if days <= 1 else 'r604800' if days <= 7 else 'r2592000'
        params = {
            'keywords': keyword,
            'location': location,
            'f_TPR': time_filter,
            'sortBy': 'DD',   # most recent
        }
        url = f'{_JOBS_URL}?{urlencode(params)}'
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        page.wait_for_timeout(3_000)   # extra render buffer for React hydration
        log.debug('LinkedIn: page title after goto: %s', page.title())

        # Pass 1: collect metadata from search result cards (no navigation)
        stubs: list[dict] = []
        seen_urls: set[str] = set()
        scroll_attempts = 0

        # Try multiple card selectors in case LinkedIn renamed classes
        _CARD_SELECTORS = [
            'div.job-search-card',
            'li.jobs-search-results__list-item',
            'div[data-job-id]',
            'li[data-occludable-job-id]',
        ]

        def _find_cards(pg):
            for sel in _CARD_SELECTORS:
                found = pg.query_selector_all(sel)
                if found:
                    log.debug('LinkedIn: card selector "%s" matched %d cards', sel, len(found))
                    return found
            return []

        while len(stubs) < limit and scroll_attempts < 8:
            cards = _find_cards(page)
            if not cards and scroll_attempts == 0:
                title = page.title()
                log.warning('LinkedIn: 0 cards found for "%s". Page title: "%s"', keyword, title)
                try:
                    import os as _os
                    _shot_dir = _os.getenv('OUTPUT_DIR', './output')
                    _os.makedirs(_shot_dir, exist_ok=True)
                    _shot_path = f'{_shot_dir}/linkedin_debug_{keyword[:20].replace(" ", "_")}.png'
                    page.screenshot(path=_shot_path, full_page=False)
                    log.warning('LinkedIn: screenshot saved to %s', _shot_path)
                except Exception as _e:
                    log.debug('LinkedIn: screenshot failed: %s', _e)
                break
            for card in cards:
                if len(stubs) >= limit:
                    break
                try:
                    stub = self._parse_card_stub(card, cutoff, seen_urls)
                    if stub:
                        stubs.append(stub)
                        seen_urls.add(stub['url'])
                except Exception as exc:
                    log.debug('LinkedIn: card stub error: %s', exc)

            prev = len(stubs)
            page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            page_delay()
            scroll_attempts += 1
            if len(stubs) == prev:
                break

        # Pass 2: fetch JD text by navigating to each job URL
        jobs: list[Job] = []
        for stub in stubs:
            jd_text = self._fetch_jd(page, stub['url'])
            jobs.append(Job(
                company=stub['company'],
                role=stub['role'],
                url=stub['url'],
                source='LinkedIn',
                jd_text=jd_text,
                salary_raw=stub.get('salary_raw', ''),
                location_raw=stub.get('location_raw', ''),
                date_posted=stub.get('date_posted'),
            ))
            short_delay()

        return jobs

    def _parse_card_stub(self, card, cutoff: datetime, seen_urls: set) -> dict | None:
        """Extract metadata from a search-result card without any page navigation."""
        # Job URL — LinkedIn March 2025 HTML uses base-card__full-link
        link = card.query_selector(
            'a.base-card__full-link, a.job-card-container__link, a.job-card-list__title--link'
        )
        if not link:
            return None
        href = link.get_attribute('href') or ''
        if not href:
            return None
        # href is already absolute (https://uk.linkedin.com/jobs/view/...) or relative
        if href.startswith('/'):
            job_url = f'https://www.linkedin.com{href.split("?")[0]}'
        else:
            job_url = href.split('?')[0]
        if job_url in seen_urls:
            return None

        role = (link.inner_text() or '').strip()
        if not role:
            # Some cards put the role in the h3 title span
            title_el = card.query_selector('h3.base-search-card__title, h3')
            role = (title_el.inner_text() if title_el else '').strip()

        company_el = card.query_selector(
            '.base-search-card__subtitle, .job-search-card__subtitle, '
            '.job-card-container__company-name, .artdeco-entity-lockup__subtitle'
        )
        company = (company_el.inner_text() if company_el else 'Unknown').strip()

        loc_el = card.query_selector(
            '.job-search-card__location, .base-search-card__metadata, '
            '.job-card-container__metadata-item, .artdeco-entity-lockup__caption'
        )
        location_raw = (loc_el.inner_text() if loc_el else '').strip()

        # Date
        time_el = card.query_selector('time, .job-card-container__listed-time')
        date_posted = None
        if time_el:
            dt_str = time_el.get_attribute('datetime') or time_el.inner_text()
            parsed = dateparser.parse(dt_str, settings={'RETURN_AS_TIMEZONE_AWARE': True})
            if parsed:
                if parsed < cutoff:
                    return None
                date_posted = parsed.astimezone(timezone.utc).isoformat()

        # Salary badge (on the card, no navigation needed)
        sal_el = card.query_selector('.job-card-container__salary-info, [data-test-id="job-insight-salary"]')
        salary_raw = (sal_el.inner_text() if sal_el else '').strip()

        return {
            'company': company,
            'role': role,
            'url': job_url,
            'salary_raw': salary_raw,
            'location_raw': location_raw,
            'date_posted': date_posted,
        }

    def _fetch_jd(self, page, job_url: str) -> str:
        """Navigate to a job detail page and extract the JD text."""
        try:
            page.goto(job_url, wait_until='domcontentloaded', timeout=15_000)
            page.wait_for_timeout(1_500)
            detail = page.query_selector(
                '.show-more-less-html__markup, .show-more-less-html, '
                '.description__text, #job-details, .jobs-description__content'
            )
            if detail:
                return html_to_markdown(detail.inner_html())
        except Exception as exc:
            log.debug('LinkedIn: JD fetch failed for %s: %s', job_url, exc)
        return ''
