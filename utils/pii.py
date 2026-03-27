"""
PII redaction utilities.

Must be applied to all text before writing to Google Sheets.
"""

import re

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_PHONE_RE = re.compile(
    r'(\+44\s?|0)[\s\-]?'
    r'(\d[\s\-]?){9,10}'
)


def redact_pii(text: str) -> str:
    """Replace email addresses and phone numbers with placeholders."""
    if not text:
        return text
    text = _EMAIL_RE.sub('[EMAIL]', text)
    text = _PHONE_RE.sub('[PHONE]', text)
    return text


def csv_safe(value: str) -> str:
    """
    Prepend apostrophe to prevent CSV injection.
    Excel/Sheets interprets cells starting with =, +, -, @ as formulas.
    """
    if not isinstance(value, str):
        value = str(value)
    if value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def safe_sheet_cell(text: str) -> str:
    """Redact PII then guard against CSV injection. Apply to all text cells."""
    return csv_safe(redact_pii(text))
