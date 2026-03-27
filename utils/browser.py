"""
Browser and HTTP session factory.

- camoufox for Playwright-based automation (patches webdriver/WebGL/Canvas)
- curl_cffi for lightweight HTTP scraping (spoofs JA3/TLS fingerprint)
- browserforge for consistent header generation
- sync_playwright ONLY — never async_playwright

Session strategies
------------------
LinkedIn: persistent user_data_dir (secrets/linkedin_profile/).
  The profile directory IS the session — all cookies, localStorage, and
  JS-managed state survive between runs exactly as a real browser would.
  No serialisation/deserialisation; avoids LinkedIn's sparse-localStorage
  rejection of storage_state-restored sessions.

Other sites (Reed etc.): storage_state JSON file, as before.
"""

import os
from pathlib import Path
from typing import Optional

from browserforge.headers import HeaderGenerator
from camoufox.sync_api import Camoufox
from curl_cffi.requests import Session as CurlSession
from playwright.sync_api import Browser, BrowserContext, Page


_header_gen = HeaderGenerator(
    browser=('chrome', 'firefox'),
    os=('windows', 'macos'),
    device='desktop',
    locale='en-GB',
)

# Default persistent profile path for LinkedIn
LINKEDIN_PROFILE_DIR = Path(__file__).parent.parent / 'secrets' / 'linkedin_profile'


def new_curl_session(impersonate: str = 'chrome120') -> CurlSession:
    """
    Return a curl_cffi Session that spoofs JA3/TLS fingerprint.
    Use for scraping LinkedIn/Indeed job listing pages before browser login.
    """
    headers = _header_gen.generate()
    session = CurlSession(impersonate=impersonate)
    session.headers.update(headers)
    return session


def new_browser(
    headless: bool = True,
    session_file: Optional[str] = None,
    user_data_dir: Optional[str] = None,
    proxy: Optional[str] = None,
) -> tuple[Camoufox, Optional[Browser], BrowserContext]:
    """
    Launch a camoufox browser with anti-detection patches applied.

    Returns (camoufox_instance, browser, context). When user_data_dir is
    used (persistent profile mode), browser is None — the context is created
    directly by launch_persistent_context. Always call camoufox.__exit__() for
    cleanup; it closes whatever was opened.

    Parameters
    ----------
    session_file : str, optional
        Path to a Playwright storage_state JSON file. Used for non-LinkedIn
        sites (Reed, eFC, etc.) where cookie serialisation is sufficient.
    user_data_dir : str, optional
        Path to a persistent browser profile directory. When provided,
        session_file is ignored. The profile persists ALL browser state
        (cookies, localStorage, IndexedDB) between runs — required for
        LinkedIn which validates JS-managed session state.

    navigator.webdriver is patched to false by camoufox automatically.
    WebGL, Canvas, and audio fingerprints are also randomised.
    """
    headers = _header_gen.generate()

    common_kwargs: dict = {
        'headless': headless,
        'geoip': True,
        'locale': 'en-GB',
        'extra_http_headers': {k: v for k, v in headers.items()},
        'timezone_id': 'Europe/London',
        'viewport': {'width': 1280, 'height': 800},
    }

    if proxy:
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(proxy)
        _proxy_dict: dict = {'server': f'{_parsed.scheme}://{_parsed.hostname}:{_parsed.port}'}
        if _parsed.username:
            _proxy_dict['username'] = _parsed.username
        if _parsed.password:
            _proxy_dict['password'] = _parsed.password
        common_kwargs['proxy'] = _proxy_dict

    if user_data_dir:
        # Persistent profile mode — camoufox calls launch_persistent_context()
        # which writes all state (cookies, localStorage, IndexedDB) to disk.
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        camoufox = Camoufox(
            persistent_context=True,
            user_data_dir=str(user_data_dir),
            **common_kwargs,
        )
        # __enter__ returns BrowserContext directly, not Browser
        context: BrowserContext = camoufox.__enter__()
        return camoufox, None, context

    # Standard mode — create context with optional storage_state
    if session_file and Path(session_file).exists():
        common_kwargs['storage_state'] = session_file

    camoufox = Camoufox(
        headless=headless,
        geoip=True,
        locale='en-GB',
    )
    browser: Browser = camoufox.__enter__()
    context = browser.new_context(
        extra_http_headers={k: v for k, v in headers.items()},
        locale='en-GB',
        timezone_id='Europe/London',
        viewport={'width': 1280, 'height': 800},
        **({'storage_state': session_file} if session_file and Path(session_file).exists() else {}),
        **({'proxy': common_kwargs['proxy']} if proxy else {}),
    )
    return camoufox, browser, context


def new_page(context: BrowserContext) -> Page:
    """Open a new page and set default navigation timeout."""
    page = context.new_page()
    page.set_default_navigation_timeout(30_000)
    page.set_default_timeout(15_000)
    return page


def save_session(context: BrowserContext, session_file: str) -> None:
    """Persist cookies/localStorage to a storage_state JSON (non-LinkedIn sites)."""
    Path(session_file).parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=session_file)
