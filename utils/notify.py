"""
Telegram push notifications for pipeline events.

Configuration (in .env):
    TELEGRAM_BOT_TOKEN=<token from @BotFather>
    TELEGRAM_CHAT_ID=980936693

Usage:
    from utils.notify import send_notification, send_daily_digest
    send_notification("Pipeline complete", urgent=False)
    send_notification("⚠️ Session expired", urgent=True)
    send_daily_digest(3, top_jobs, sheet_url)

Design:
- Non-blocking — every send runs in a daemon thread.
- Never raises — all failures are logged at WARNING and swallowed.
- No-op if either env var is missing.
- HTML parse_mode — wrap bold text in <b>...</b>.
- urgent=True  → disable_notification=False  (audible alert)
- urgent=False → disable_notification=True   (silent delivery)
"""

import json
import logging
import os
import threading
import urllib.request
import urllib.error

log = logging.getLogger(__name__)

_API_BASE = 'https://api.telegram.org'


def _post(token: str, chat_id: str, text: str, silent: bool) -> None:
    """
    Blocking POST to Telegram sendMessage API.
    Called from a daemon thread — never call directly from pipeline code.
    """
    url = f'{_API_BASE}/bot{token}/sendMessage'
    payload = json.dumps({
        'chat_id':             chat_id,
        'text':                text,
        'parse_mode':          'HTML',
        'disable_notification': silent,
    }).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=payload,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            log.debug('Telegram: sent message, status=%d', resp.status)
    except urllib.error.HTTPError as exc:
        body = exc.read(200).decode('utf-8', errors='replace')
        log.warning('Telegram: HTTP %d — %s', exc.code, body)
    except Exception as exc:
        log.warning('Telegram: send failed — %s', exc)


def send_notification(message: str, urgent: bool = False) -> None:
    """
    Send a Telegram push notification.

    Args:
        message: Text to send. HTML tags (<b>, <i>, <code>) are rendered.
        urgent:  True  → audible alert (disable_notification=False)
                 False → silent delivery (disable_notification=True)

    Returns immediately. Actual send happens in a background daemon thread.
    No-op if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.
    """
    token   = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()

    if not token or not chat_id:
        log.debug('Telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping notification')
        return

    silent = not urgent  # urgent=True → audible → disable_notification=False

    t = threading.Thread(
        target=_post,
        args=(token, chat_id, message, silent),
        daemon=True,
    )
    t.start()


def send_daily_digest(
    jobs_packaged_count: int,
    top_jobs: list[dict],
    sheet_url: str,
) -> None:
    """
    Send a daily summary of packaged application docs as an audible Telegram alert.

    Args:
        jobs_packaged_count: Total number of application packages generated this run.
        top_jobs:            List of dicts, each with keys:
                               company (str), role (str),
                               score (float|str), interview_probability (int).
                             Sorted by score descending before display. Capped at 5.
        sheet_url:           Full URL to the Google Sheet for manual review.

    No-op if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.
    """
    if jobs_packaged_count == 0 and not top_jobs:
        log.debug('Telegram: daily digest skipped — nothing packaged this run')
        return

    label = 'package' if jobs_packaged_count == 1 else 'packages'
    lines = [
        f'📊 <b>Daily Digest — Lead Gen Bot</b>\n',
        f'<b>{jobs_packaged_count} new application {label} ready</b>\n',
    ]

    top_five = sorted(
        top_jobs,
        key=lambda j: float(j.get('score', 0) or 0),
        reverse=True,
    )[:5]

    if top_five:
        lines.append('Top opportunities:')
        for i, job in enumerate(top_five, 1):
            company  = job.get('company', '?')
            role     = job.get('role', '?')
            score    = job.get('score', '')
            prob     = job.get('interview_probability', '')
            score_str = f'KPI {float(score):.1f}' if score else ''
            prob_str  = f'P(interview)={prob}' if prob else ''
            detail    = '  |  '.join(p for p in [score_str, prob_str] if p)
            lines.append(f'{i}. <b>{company}</b> — {role}' + (f'  |  {detail}' if detail else ''))
        lines.append('')

    if sheet_url:
        lines.append(f'📂 Review: <a href="{sheet_url}">Open Sheet</a>')

    send_notification('\n'.join(lines), urgent=True)
