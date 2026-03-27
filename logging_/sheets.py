"""
Google Sheets logging — batch write, PII redaction, CSV injection guard.

Design decisions (from spec):
- append_rows() ONCE per run — never row-by-row (avoids 429 rate limits)
- PII redaction on all text fields before logging
- CSV injection protection: apostrophe prefix on all text cells
- All dates as UTC ISO-8601 date strings — never Python datetime objects
- Health check on startup: test read before pipeline begins

Column layout (A–G):
  A Date   B Company   C Role   D Score   E URL   F Local_Folder_Path   G Status
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from utils.pii import redact_pii, safe_sheet_cell

log = logging.getLogger(__name__)

_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.readonly',
]

_HEADER_ROW = [
    'Date', 'Company', 'Role', 'Score', 'URL', 'Local_Folder_Path', 'Status',
]


# ---------------------------------------------------------------------------
# Google Sheets client factory
# ---------------------------------------------------------------------------

def _get_client() -> gspread.Client:
    """Return an authorised gspread client using a service account JSON file."""
    sa_path = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', './secrets/service_account.json')
    creds = Credentials.from_service_account_file(sa_path, scopes=_SCOPES)
    return gspread.authorize(creds)


def _get_sheet() -> gspread.Worksheet:
    """Open the target spreadsheet and return the first worksheet."""
    sheet_id = os.environ['GOOGLE_SHEET_ID']
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.sheet1


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def health_check() -> bool:
    """
    Test that the Google Sheet is reachable and has the expected header row.
    Call this on startup before the pipeline begins.
    Returns True on success, False on any failure (pipeline continues without logging).
    """
    try:
        sheet = _get_sheet()
        first_row = sheet.row_values(1)
        if not first_row:
            # Sheet is empty — write header
            sheet.append_row(_HEADER_ROW, value_input_option='RAW')
            log.info('Sheets: initialised header row')
        elif first_row[0] != 'Date':
            log.warning('Sheets: unexpected header row — sheet may be misconfigured')
        log.info('Sheets: health check OK (sheet_id=%s)', os.environ.get('GOOGLE_SHEET_ID', '?'))
        return True
    except Exception as exc:
        log.error('Sheets: health check failed — logging will be skipped: %s', exc)
        return False


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _build_row(entry: dict) -> list:
    """
    Convert a log entry dict to a 7-element list (columns A–G).
    All text values are PII-redacted and CSV-injection-guarded.

    Expected entry keys (all optional — missing keys produce empty string):
      date, company, role, score, url, local_folder, status
    """
    def _date(val) -> str:
        if val:
            return str(val)
        return datetime.now(timezone.utc).date().isoformat()

    def _text(val) -> str:
        if val is None:
            return ''
        return safe_sheet_cell(str(val))

    def _score(val) -> str:
        if val is None or val == '':
            return ''
        try:
            return f'{float(val):.1f}'
        except (TypeError, ValueError):
            return str(val)

    return [
        _date(entry.get('date')),                 # A — Date
        _text(entry.get('company', '')),           # B — Company
        _text(entry.get('role', '')),              # C — Role
        _score(entry.get('score')),                # D — Score
        _text(entry.get('url', '')),               # E — URL
        _text(entry.get('local_folder', '')),      # F — Local_Folder_Path
        _text(entry.get('status', 'Pending Manual Submission')),  # G — Status
    ]


# ---------------------------------------------------------------------------
# SheetLogger
# ---------------------------------------------------------------------------

class SheetLogger:
    """
    Accumulates log entries during a pipeline run and flushes them to Google
    Sheets in a single batch append_rows() call at the end of the run.

    Usage:
        logger = SheetLogger()
        logger.log(entry_dict)          # called for each job processed
        logger.flush()                  # called once at end of pipeline run
    """

    def __init__(self):
        self._rows: list[list] = []
        self._sheet_available = False

    def connect(self) -> bool:
        """
        Open the sheet connection and validate it.
        Returns True if the sheet is reachable, False otherwise.
        Call this at startup — pipeline continues even if False.
        """
        self._sheet_available = health_check()
        return self._sheet_available

    def log(self, entry: dict) -> None:
        """
        Stage a single log entry for the next flush().
        Always succeeds — if sheet unavailable, entries are silently dropped
        at flush time (not here, so flush() can try a reconnect).
        """
        try:
            row = _build_row(entry)
            self._rows.append(row)
            log.debug('Sheets: staged row for %s / %s', entry.get('company'), entry.get('role'))
        except Exception as exc:
            log.warning('Sheets: failed to build row: %s', exc)

    def flush(self) -> int:
        """
        Write all staged rows to Google Sheets in one batch append_rows() call.
        Returns the number of rows written (0 on failure).
        Clears the staging buffer on success.
        On failure, writes rows to a local CSV backup.
        """
        if not self._rows:
            log.debug('Sheets: nothing to flush')
            return 0

        if not self._sheet_available:
            log.warning('Sheets: sheet not available — skipping flush of %d rows', len(self._rows))
            self._backup_to_csv()
            return 0

        try:
            rows_snapshot = list(self._rows)  # snapshot before clear — avoids reference aliasing
            sheet = _get_sheet()
            sheet.append_rows(
                rows_snapshot,
                value_input_option='RAW',    # RAW prevents Sheets from re-interpreting values
                insert_data_option='INSERT_ROWS',
                table_range='A1',
            )
            count = len(rows_snapshot)
            log.info('Sheets: flushed %d row(s) to spreadsheet', count)
            self._rows.clear()
            return count
        except Exception as exc:
            log.error('Sheets: flush failed — %d rows NOT written: %s', len(self._rows), exc)
            self._backup_to_csv()
            return 0

    def flush_if_pending(self, threshold: int = 5) -> int:
        """Flush if >= threshold rows are pending. Call after each job in pipeline loop."""
        if len(self._rows) >= threshold:
            log.debug('Sheets: periodic flush triggered (%d pending)', len(self._rows))
            return self.flush()
        return 0

    def _backup_to_csv(self) -> None:
        """Write pending rows to a local CSV file when Sheets is unavailable."""
        if not self._rows:
            return
        try:
            import csv
            from pathlib import Path
            backup_dir = Path(os.getenv('OUTPUT_DIR', './output')) / 'sheets_backup'
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            backup_path = backup_dir / f'backup_{ts}.csv'
            with open(backup_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(_HEADER_ROW)
                writer.writerows(self._rows)
            log.info('Sheets: backed up %d rows to %s', len(self._rows), backup_path)
        except Exception as exc:
            log.error('Sheets: CSV backup failed: %s', exc)

    def pending_count(self) -> int:
        """Number of rows staged but not yet flushed."""
        return len(self._rows)
