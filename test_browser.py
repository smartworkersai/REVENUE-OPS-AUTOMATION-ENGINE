"""
LinkedIn session setup — persistent profile approach.

Launches camoufox with secrets/linkedin_profile/ as the browser profile
directory. Log in manually; the profile is written to disk automatically.
On subsequent runs, the profile is reused — no cookie serialisation needed.

Usage:
    PYTHONPATH="." python test_browser.py

Wait until your LinkedIn feed is fully loaded (profile picture visible in
the top navigation bar) before pressing Enter.
"""
import sqlite3
import time
import traceback
from pathlib import Path

try:
    from utils.browser import new_browser, new_page, LINKEDIN_PROFILE_DIR

    profile_dir = str(LINKEDIN_PROFILE_DIR)
    print(f'Profile directory: {profile_dir}')
    if Path(profile_dir).exists():
        print('Existing profile found — will reuse (you may already be logged in).')
    else:
        print('No existing profile — fresh login required.')

    camoufox, browser, context = new_browser(
        headless=False,
        user_data_dir=profile_dir,
    )
    page = new_page(context)
    page.goto('https://www.linkedin.com/feed/', wait_until='domcontentloaded', timeout=30_000)

    print()
    print('LinkedIn is open.')
    print('Log in if prompted, then wait until your feed loads fully')
    print('(your profile picture should be visible in the top navigation bar).')
    input('Press Enter once you can see your LinkedIn feed...')

    # Verify the session looks valid before closing
    url = page.url
    if any(s in url for s in ['login', 'authwall', 'checkpoint', 'signup']):
        print(f'WARNING: still on auth page ({url[:60]}). Profile may not be saved correctly.')
    else:
        print(f'Session appears valid (URL: {url[:60]}).')

    # Poll for li_at in cookies.sqlite — Firefox flushes asynchronously.
    # Check every 2 s, up to 30 s total; proceed as soon as it appears.
    cookies_db = Path(profile_dir) / 'cookies.sqlite'
    li_at_found = False
    elapsed = 0
    max_wait = 30
    interval = 2

    print(f'Waiting for Firefox to flush li_at to cookies.sqlite (up to {max_wait} s)...')
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        if cookies_db.exists():
            try:
                con = sqlite3.connect(str(cookies_db))
                row = con.execute(
                    "SELECT value FROM moz_cookies WHERE name='li_at' LIMIT 1"
                ).fetchone()
                con.close()
                if row is not None:
                    li_at_found = True
                    print(f'li_at found after {elapsed} s.')
                    break
            except Exception:
                pass  # cookies.sqlite may be locked mid-write; retry next cycle
        print(f'  {elapsed} s elapsed — li_at not yet flushed, retrying...')

    # Profile is written to disk automatically when the context closes.
    # browser is None in persistent_context mode — camoufox.__exit__ handles cleanup.
    context.close()
    camoufox.__exit__(None, None, None)
    print(f'Profile saved to: {profile_dir}')

    if li_at_found:
        print('li_at confirmed in cookies.sqlite — session is ready.')
        print('LinkedIn session ready. Run main.py to start the pipeline.')
    else:
        print('WARNING: li_at NOT found in cookies.sqlite after 30 s.')
        print('The profile directory exists but the auth cookie was not persisted.')
        print('Re-run test_browser.py and wait a few extra seconds after your feed')
        print('loads before pressing Enter.')

except Exception:
    traceback.print_exc()
