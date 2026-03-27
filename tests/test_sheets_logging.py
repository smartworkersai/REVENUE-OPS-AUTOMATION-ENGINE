"""
Stage 7 validation tests — Google Sheets logging.

Tests:
1. _build_row: all 14 columns populated correctly
2. PII redacted from Company/Role/Notes/CL excerpt
3. CSV injection guarded on all text cells
4. Score breakdown capped at 1000 chars
5. Key gaps list → comma-separated string
6. Cover letter excerpt → first 200 chars
7. Timestamps normalised to ISO-8601 UTC
8. SheetLogger.log() + flush() with mocked gspread
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('GOOGLE_SHEET_ID', 'test-sheet-id')
os.environ.setdefault('GOOGLE_SERVICE_ACCOUNT_JSON', './secrets/service_account.json')

from logging_.sheets import _build_row, SheetLogger


# ---------------------------------------------------------------------------
# _build_row tests
# ---------------------------------------------------------------------------

def _sample_entry(**overrides) -> dict:
    base = {
        'timestamp': datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        'company': 'Acme Corp',
        'role': 'Marketing Executive',
        'score': 8.4,
        'score_breakdown': {'skill_match': 8.5, 'seniority_fit': 9.0},
        'lead_advantage': 'CISI + Deloitte background',
        'key_gaps': ['CRM experience', 'B2B focus'],
        'status': 'Submitted',
        'url': 'https://acme.com/jobs/123',
        'source': 'LinkedIn',
        'cover_letter': 'At CISI, I coordinated 3 high-profile events reaching senior stakeholders. '
                        'Here is my email: omokoladesobande@gmail.com and phone +44 7310552174.',
        'notes': '',
        'date_posted': datetime(2025, 5, 30, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def test_build_row_length():
    row = _build_row(_sample_entry())
    assert len(row) == 14, f'Expected 14 columns, got {len(row)}'


def test_build_row_timestamp_iso():
    row = _build_row(_sample_entry())
    ts = row[0]  # col A
    assert 'T' in ts
    assert ts.endswith('+00:00') or ts.endswith('Z') or '+00:00' in ts, f'Not UTC ISO: {ts}'


def test_build_row_score_formatted():
    row = _build_row(_sample_entry(score=8.4123))
    assert row[3] == '8.41'  # col D — 2 decimal places


def test_build_row_score_breakdown_capped():
    big = {'key': 'x' * 2000}
    row = _build_row(_sample_entry(score_breakdown=big))
    raw = row[4]  # col E
    # Strip leading apostrophe added by safe_sheet_cell
    content = raw.lstrip("'")
    assert len(content) <= 1000, f'Score breakdown exceeded 1000 chars: {len(content)}'


def test_build_row_key_gaps_list():
    row = _build_row(_sample_entry(key_gaps=['CRM', 'B2B focus', 'SQL']))
    gaps = row[6]  # col G
    assert 'CRM' in gaps
    assert 'B2B focus' in gaps
    assert 'SQL' in gaps


def test_build_row_cover_letter_excerpt_max_200():
    long_cl = 'A' * 500
    row = _build_row(_sample_entry(cover_letter=long_cl))
    excerpt = row[10]  # col K
    # Strip apostrophe prefix
    content = excerpt.lstrip("'")
    assert len(content) <= 200, f'CL excerpt too long: {len(content)}'


def test_build_row_pii_redacted_from_cover_letter():
    entry = _sample_entry(
        cover_letter='Contact me at omokoladesobande@gmail.com or +44 7310552174 about this role.'
    )
    row = _build_row(entry)
    excerpt = row[10]  # col K
    assert 'omokoladesobande' not in excerpt
    assert '7310552174' not in excerpt


def test_build_row_pii_redacted_from_notes():
    entry = _sample_entry(notes='Error contacting omokoladesobande@gmail.com')
    row = _build_row(entry)
    notes = row[11]  # col L
    assert 'omokoladesobande' not in notes


def test_build_row_csv_injection_guarded():
    """Cells starting with =, +, -, @ must be prefixed with apostrophe."""
    entry = _sample_entry(
        company='=HYPERLINK("http://evil.com")',
        role='+malicious',
        notes='-cmd',
    )
    row = _build_row(entry)
    company = row[1]   # col B
    role = row[2]      # col C
    notes = row[11]    # col L

    assert company.startswith("'"), f'CSV injection not guarded in company: {company}'
    assert role.startswith("'"), f'CSV injection not guarded in role: {role}'
    assert notes.startswith("'"), f'CSV injection not guarded in notes: {notes}'


def test_build_row_interview_col_always_empty():
    row = _build_row(_sample_entry())
    assert row[13] == '', f'Col N (Interview) should be empty, got: {row[13]}'


def test_build_row_missing_fields_no_crash():
    row = _build_row({})
    assert len(row) == 14
    # Timestamp auto-generated
    assert row[0] != ''
    # All other text fields empty
    assert row[1] == ''
    assert row[3] == ''  # score


def test_build_row_naive_timestamp_treated_as_utc():
    naive_dt = datetime(2025, 6, 1, 9, 30, 0)  # no tzinfo
    row = _build_row(_sample_entry(timestamp=naive_dt))
    ts = row[0]
    assert '2025-06-01' in ts


# ---------------------------------------------------------------------------
# SheetLogger tests (mocked gspread)
# ---------------------------------------------------------------------------

def _make_logger_with_mock():
    """Return (SheetLogger, mock_sheet) with gspread patched out."""
    mock_sheet = MagicMock()
    mock_sheet.row_values.return_value = ['Timestamp']  # already initialised

    with patch('logging_.sheets._get_sheet', return_value=mock_sheet):
        with patch('logging_.sheets._get_client'):
            logger = SheetLogger()
            logger._sheet_available = True  # bypass real health check
    return logger, mock_sheet


def test_logger_log_stages_rows():
    logger, _ = _make_logger_with_mock()
    logger.log(_sample_entry())
    logger.log(_sample_entry(company='Other Co'))
    assert logger.pending_count() == 2


def test_logger_flush_calls_append_rows():
    logger, mock_sheet = _make_logger_with_mock()
    logger.log(_sample_entry())
    logger.log(_sample_entry(company='Firm B'))

    with patch('logging_.sheets._get_sheet', return_value=mock_sheet):
        count = logger.flush()

    assert count == 2
    mock_sheet.append_rows.assert_called_once()
    call_args = mock_sheet.append_rows.call_args
    rows_written = call_args[0][0]  # positional first arg
    assert len(rows_written) == 2


def test_logger_flush_clears_buffer():
    logger, mock_sheet = _make_logger_with_mock()
    logger.log(_sample_entry())

    with patch('logging_.sheets._get_sheet', return_value=mock_sheet):
        logger.flush()

    assert logger.pending_count() == 0


def test_logger_flush_no_rows_does_nothing():
    logger, mock_sheet = _make_logger_with_mock()
    with patch('logging_.sheets._get_sheet', return_value=mock_sheet):
        count = logger.flush()
    assert count == 0
    mock_sheet.append_rows.assert_not_called()


def test_logger_flush_when_sheet_unavailable():
    logger, mock_sheet = _make_logger_with_mock()
    logger._sheet_available = False
    logger.log(_sample_entry())

    with patch('logging_.sheets._get_sheet', return_value=mock_sheet):
        count = logger.flush()

    assert count == 0
    mock_sheet.append_rows.assert_not_called()
    # Row is still staged (not lost)
    assert logger.pending_count() == 1


def test_logger_flush_gspread_error_does_not_raise():
    logger, mock_sheet = _make_logger_with_mock()
    mock_sheet.append_rows.side_effect = Exception('API 429 rate limit')
    logger.log(_sample_entry())

    with patch('logging_.sheets._get_sheet', return_value=mock_sheet):
        count = logger.flush()  # must not raise

    assert count == 0


if __name__ == '__main__':
    tests = [
        test_build_row_length,
        test_build_row_timestamp_iso,
        test_build_row_score_formatted,
        test_build_row_score_breakdown_capped,
        test_build_row_key_gaps_list,
        test_build_row_cover_letter_excerpt_max_200,
        test_build_row_pii_redacted_from_cover_letter,
        test_build_row_pii_redacted_from_notes,
        test_build_row_csv_injection_guarded,
        test_build_row_interview_col_always_empty,
        test_build_row_missing_fields_no_crash,
        test_build_row_naive_timestamp_treated_as_utc,
        test_logger_log_stages_rows,
        test_logger_flush_calls_append_rows,
        test_logger_flush_clears_buffer,
        test_logger_flush_no_rows_does_nothing,
        test_logger_flush_when_sheet_unavailable,
        test_logger_flush_gspread_error_does_not_raise,
    ]

    print('Running Stage 7 Sheets logging validation...')
    passed = 0
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f'  {name} ... PASS')
            passed += 1
        except Exception as e:
            print(f'  {name} ... FAIL: {e}')
            failed += 1

    print(f'\nStage 7 validation: {passed} passed, {failed} failed')
    if failed:
        raise SystemExit(1)
