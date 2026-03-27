"""
Gmail API integration — OAuth authentication, OTP polling, and email monitoring.

Configuration (in .env):
    GMAIL_CREDENTIALS_PATH  path to OAuth client credentials JSON
                            default: ./secrets/gmail_credentials.json
    GMAIL_TOKEN_PATH        path where the saved token is stored
                            default: ./secrets/gmail_token.json

Scopes:
    gmail.readonly  — read messages and labels
    gmail.modify    — mark messages as read (remove UNREAD label)

Design:
- authenticate_gmail() is called lazily; subsequent calls reuse the cached
  service object for the lifetime of the process.
- read_otp_from_inbox() only looks at messages received AFTER polling begins,
  using the Gmail `after:` query operator with a Unix timestamp, so stale OTPs
  from earlier sessions are never returned.
- monitor_job_emails() fetches full message payloads (not just snippets) so
  multi-part MIME bodies are decoded correctly before keyword matching.
- Sheet updates are best-effort: failures are logged at WARNING and never raise.
- All functions are safe to call even if credentials are absent — they log a
  warning and return gracefully.
"""

import base64
import logging
import os
import re
import time
from email import message_from_bytes
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
]

_CREDENTIALS_PATH = Path(os.getenv('GMAIL_CREDENTIALS_PATH', './secrets/gmail_credentials.json'))
_TOKEN_PATH       = Path(os.getenv('GMAIL_TOKEN_PATH',       './secrets/gmail_token.json'))

# OTP: 4–8 consecutive digits, word-bounded (avoids matching phone/account numbers
# embedded in longer digit strings).
_OTP_RE = re.compile(r'(?<!\d)(\d{4,8})(?!\d)')

# Email classification patterns (subject + first 500 chars of body)
_ASSESSMENT_RE = re.compile(
    r'\b(assessment|online\s+test|aptitude\s+test|psychometric|complete\s+by|complete\s+the\s+test)\b',
    re.I,
)
_INTERVIEW_RE = re.compile(
    r'\b(interview|phone\s+call|video\s+call|meet\s+with|schedule\s+(an?\s+)?interview'
    r'|your\s+availability|invite\s+you\s+to|speak\s+with\s+us)\b',
    re.I,
)
_REJECTION_RE = re.compile(
    r'\b(unfortunately|not\s+successful|on\s+this\s+occasion|regret\s+to\s+inform'
    r'|not\s+moving\s+forward|other\s+candidates|not\s+taken\s+forward'
    r'|won.t\s+be\s+progressing|unable\s+to\s+offer)\b',
    re.I,
)
_CONFIRMATION_RE = re.compile(
    r'\b(application\s+received|thank\s+you\s+for\s+appl(ying|ication)'
    r'|we.ve?\s+received\s+your|received\s+your\s+application'
    r'|successfully\s+submitted|application\s+has\s+been\s+received)\b',
    re.I,
)

# Cached service object — built once per process
_gmail_service = None


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate_gmail():
    """
    Build and return an authenticated Gmail API service object.

    On first run: opens a local browser tab for OAuth consent, saves the
    resulting token to GMAIL_TOKEN_PATH.
    On subsequent runs: loads the saved token silently and refreshes it if
    expired (no browser required).

    Returns:
        googleapiclient.discovery.Resource — Gmail API service

    Raises:
        FileNotFoundError if GMAIL_CREDENTIALS_PATH does not exist.
        google.auth.exceptions.* on unrecoverable auth failures.
    """
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds_path = Path(os.getenv('GMAIL_CREDENTIALS_PATH', str(_CREDENTIALS_PATH)))
    token_path = Path(os.getenv('GMAIL_TOKEN_PATH', str(_TOKEN_PATH)))

    if not creds_path.exists():
        raise FileNotFoundError(
            f'Gmail credentials not found: {creds_path}\n'
            'Download OAuth client credentials from Google Cloud Console and '
            'save them to secrets/gmail_credentials.json'
        )

    creds = None

    # Load saved token if it exists
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
        except Exception as exc:
            log.warning('Gmail: could not load saved token (%s) — re-authenticating', exc)
            creds = None

    # Refresh expired token, or run the full consent flow
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log.info('Gmail: token refreshed silently')
        except Exception as exc:
            log.warning('Gmail: token refresh failed (%s) — running consent flow', exc)
            creds = None

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
        creds = flow.run_local_server(port=0)
        log.info('Gmail: OAuth consent completed — saving token to %s', token_path)

    # Persist token for future runs
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())

    _gmail_service = build('gmail', 'v1', credentials=creds)
    log.info('Gmail: service authenticated (user=%s)', _get_email_address())
    return _gmail_service


def _get_email_address() -> str:
    """Return the authenticated user's email address, or '?' on failure."""
    try:
        profile = _gmail_service.users().getProfile(userId='me').execute()
        return profile.get('emailAddress', '?')
    except Exception:
        return '?'


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_body(payload: dict) -> str:
    """
    Recursively decode a Gmail message payload into a plain-text string.
    Handles multipart/alternative and multipart/mixed MIME structures.
    Prefers text/plain; falls back to text/html (tags stripped).
    """
    mime_type = payload.get('mimeType', '')
    body_data = payload.get('body', {}).get('data', '')

    if mime_type == 'text/plain' and body_data:
        return base64.urlsafe_b64decode(body_data + '==').decode('utf-8', errors='replace')

    if mime_type == 'text/html' and body_data:
        raw = base64.urlsafe_b64decode(body_data + '==').decode('utf-8', errors='replace')
        # Strip HTML tags — crude but sufficient for keyword matching
        return re.sub(r'<[^>]+>', ' ', raw)

    if mime_type.startswith('multipart/'):
        parts = payload.get('parts', [])
        # Try text/plain first
        for part in parts:
            if part.get('mimeType') == 'text/plain':
                decoded = _decode_body(part)
                if decoded.strip():
                    return decoded
        # Fall back to html or recurse into nested multipart
        for part in parts:
            decoded = _decode_body(part)
            if decoded.strip():
                return decoded

    return ''


def _get_header(headers: list[dict], name: str) -> str:
    """Extract a single header value by name (case-insensitive)."""
    for h in headers:
        if h.get('name', '').lower() == name.lower():
            return h.get('value', '')
    return ''


def _mark_as_read(service, msg_id: str) -> None:
    """Remove the UNREAD label from a message."""
    try:
        service.users().messages().modify(
            userId='me',
            id=msg_id,
            body={'removeLabelIds': ['UNREAD']},
        ).execute()
    except Exception as exc:
        log.debug('Gmail: could not mark %s as read: %s', msg_id, exc)


def _update_sheet_col(company: str, col_letter: str, value: str) -> None:
    """
    Best-effort: find the most recent row in the Sheet whose Company column (B)
    matches ``company`` (case-insensitive substring) and write ``value`` into
    ``col_letter``.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials as SACredentials

        sa_path = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', './secrets/service_account.json')
        sheet_id = os.environ.get('GOOGLE_SHEET_ID', '')
        if not sheet_id or not Path(sa_path).exists():
            return

        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly',
        ]
        creds  = SACredentials.from_service_account_file(sa_path, scopes=scopes)
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(sheet_id).sheet1

        # Column B contains company names; find all values
        companies = sheet.col_values(2)  # 1-indexed; col B = 2
        company_lower = company.lower()

        # Search in reverse so we update the most recent matching row
        match_row = None
        for idx in range(len(companies) - 1, 0, -1):  # skip header (row 0 = col header row)
            if company_lower in companies[idx].lower():
                match_row = idx + 1  # gspread is 1-indexed
                break

        if match_row is None:
            log.debug('Gmail: Sheet update — no row found for company "%s"', company)
            return

        col_index = ord(col_letter.upper()) - ord('A') + 1
        sheet.update_cell(match_row, col_index, value)
        log.info('Gmail: Sheet row %d col %s updated → "%s" (company: %s)',
                 match_row, col_letter, value, company)

    except Exception as exc:
        log.warning('Gmail: Sheet update failed for "%s": %s', company, exc)


# ---------------------------------------------------------------------------
# OTP polling
# ---------------------------------------------------------------------------

def read_otp_from_inbox(
    sender_domain: str,
    timeout_seconds: int = 120,
) -> Optional[str]:
    """
    Poll the inbox every 5 seconds for up to ``timeout_seconds`` looking for a
    new email from ``sender_domain`` that contains a numeric OTP code.

    Only emails received AFTER this function is called are considered, so stale
    OTPs in the inbox do not cause false positives.

    Args:
        sender_domain:   Domain to match in the ``from`` header, e.g. ``"reed.co.uk"``.
        timeout_seconds: Maximum time to wait before returning None.

    Returns:
        OTP string (4–8 digits) or None on timeout.
    """
    try:
        service = authenticate_gmail()
    except Exception as exc:
        log.warning('Gmail: authenticate failed in read_otp_from_inbox: %s', exc)
        return None

    poll_start = int(time.time())
    deadline   = poll_start + timeout_seconds
    log.info('Gmail: polling for OTP from %s (timeout=%ds)', sender_domain, timeout_seconds)

    while time.time() < deadline:
        try:
            # after: uses Unix epoch seconds — only messages received after polling started
            query = f'from:{sender_domain} after:{poll_start}'
            result = service.users().messages().list(
                userId='me', q=query, maxResults=5,
            ).execute()

            messages = result.get('messages', [])
            for msg_meta in messages:
                msg = service.users().messages().get(
                    userId='me', id=msg_meta['id'], format='full',
                ).execute()
                headers  = msg.get('payload', {}).get('headers', [])
                subject  = _get_header(headers, 'subject')
                body     = _decode_body(msg.get('payload', {}))
                combined = f'{subject}\n{body}'

                matches = _OTP_RE.findall(combined)
                if matches:
                    otp = matches[0]
                    log.info('Gmail: OTP found from %s: %s', sender_domain, otp)
                    _mark_as_read(service, msg_meta['id'])
                    return otp

        except Exception as exc:
            log.warning('Gmail: error during OTP poll: %s', exc)

        remaining = int(deadline - time.time())
        if remaining > 0:
            log.debug('Gmail: no OTP yet — waiting 5s (%ds remaining)', remaining)
            time.sleep(5)

    log.warning('Gmail: OTP timeout after %ds — no email from %s', timeout_seconds, sender_domain)
    return None


# ---------------------------------------------------------------------------
# Email monitoring
# ---------------------------------------------------------------------------

def monitor_job_emails() -> list[dict]:
    """
    Read unread emails in the inbox, identify job-related messages, mark them
    as read, and return a list of finding dicts.

    Each finding dict contains:
        category  : 'assessment' | 'interview' | 'rejection' | 'confirmation'
        urgent    : bool — True for assessment and interview
        subject   : str
        sender    : str (From header)
        company   : str (best-effort extracted from subject)
        msg_id    : str (Gmail message ID)
        snippet   : str (first 200 chars of body)

    Side effects:
        - Marks every processed message as read
        - Updates Google Sheet column N to "Rejected" for rejections
        - Updates Google Sheet column H to "Confirmed" for confirmations

    Returns an empty list if credentials are absent or Gmail is unreachable.
    """
    try:
        service = authenticate_gmail()
    except FileNotFoundError:
        log.debug('Gmail: credentials not found — skipping email monitoring')
        return []
    except Exception as exc:
        log.warning('Gmail: could not authenticate for monitoring: %s', exc)
        return []

    findings: list[dict] = []

    try:
        result = service.users().messages().list(
            userId='me',
            q='is:unread in:inbox',
            maxResults=50,
        ).execute()
        messages = result.get('messages', [])
    except Exception as exc:
        log.warning('Gmail: failed to list inbox messages: %s', exc)
        return []

    if not messages:
        log.debug('Gmail: no unread messages in inbox')
        return []

    log.info('Gmail: processing %d unread message(s)', len(messages))

    for msg_meta in messages:
        try:
            msg = service.users().messages().get(
                userId='me', id=msg_meta['id'], format='full',
            ).execute()

            headers  = msg.get('payload', {}).get('headers', [])
            subject  = _get_header(headers, 'subject')
            sender   = _get_header(headers, 'from')
            body     = _decode_body(msg.get('payload', {}))

            # Use subject + first 500 chars of body for classification
            text = f'{subject}\n{body[:500]}'

            category = _classify_email(text)
            if category is None:
                # Not a job-related email — mark read and skip
                _mark_as_read(service, msg_meta['id'])
                continue

            company = _extract_company(subject, sender)
            urgent  = category in ('assessment', 'interview')

            finding = {
                'category': category,
                'urgent':   urgent,
                'subject':  subject,
                'sender':   sender,
                'company':  company,
                'msg_id':   msg_meta['id'],
                'snippet':  body[:200].strip(),
            }
            findings.append(finding)

            log.info(
                'Gmail: [%s] %s | from: %s',
                category.upper(), subject[:80], sender[:60],
            )

            # Sheet updates
            if category == 'rejection' and company:
                _update_sheet_col(company, 'N', 'Rejected')
            elif category == 'confirmation' and company:
                _update_sheet_col(company, 'H', 'Confirmed')

            _mark_as_read(service, msg_meta['id'])

        except Exception as exc:
            log.warning('Gmail: error processing message %s: %s', msg_meta['id'], exc)

    log.info('Gmail: monitoring complete — %d job-related email(s) found', len(findings))
    return findings


# ---------------------------------------------------------------------------
# Internal classifiers
# ---------------------------------------------------------------------------

def _classify_email(text: str) -> Optional[str]:
    """
    Return category string or None if email does not appear job-related.
    Order matters: assessment checked before interview to avoid misclassification
    of "complete your assessment interview".
    """
    if _ASSESSMENT_RE.search(text):
        return 'assessment'
    if _INTERVIEW_RE.search(text):
        return 'interview'
    if _REJECTION_RE.search(text):
        return 'rejection'
    if _CONFIRMATION_RE.search(text):
        return 'confirmation'
    return None


def _extract_company(subject: str, sender: str) -> str:
    """
    Best-effort company name extraction.

    Tries common subject patterns like:
      "Your application to Acme Corp — Software Engineer"
      "Update on your application at Acme Corp"
      "Interview invitation from Acme Corp"
      "Acme Corp: Application received"
    Falls back to the sender display name (part before the email address).
    """
    patterns = [
        re.compile(r'(?:application\s+(?:to|at|with)|invitation\s+from|from)\s+([A-Z][^—\-|:()\n]{2,50})', re.I),
        re.compile(r'^([A-Z][^:—\-|()\n]{2,40}):', re.I),
    ]
    for pat in patterns:
        m = pat.search(subject)
        if m:
            return m.group(1).strip(' .,')

    # Sender display name: "Acme Corp Careers <careers@acme.com>" → "Acme Corp Careers"
    m = re.match(r'^(.+?)\s*<', sender)
    if m:
        return m.group(1).strip('" ')

    return ''
