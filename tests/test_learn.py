"""
Stage 8 validation tests — learning/learn.py.

Tests:
1. Conversion rate calculation (correct bucketing, interview detection)
2. Minimum data threshold respected (no config change below 5 interviews)
3. Best lead_advantage selection from rates
4. config.yaml lead_advantage_boost updated correctly
5. Selector pruning logic
6. Honeypot report queries SQLite correctly
"""

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

os.environ.setdefault('GOOGLE_SHEET_ID', 'test-id')
os.environ.setdefault('GOOGLE_SERVICE_ACCOUNT_JSON', './secrets/service_account.json')

from learning.learn import (
    _calculate_conversion_rates,
    run_weight_optimisation,
    run_selector_evolution,
    run_honeypot_report,
)


# ---------------------------------------------------------------------------
# _calculate_conversion_rates
# ---------------------------------------------------------------------------

def test_conversion_rates_basic():
    data = [
        {'lead_advantage': 'CISI + Deloitte', 'interview': 'interview'},
        {'lead_advantage': 'CISI + Deloitte', 'interview': 'interview'},
        {'lead_advantage': 'CISI + Deloitte', 'interview': 'rejected'},
        {'lead_advantage': 'Todlr growth',     'interview': 'none'},
        {'lead_advantage': 'Todlr growth',     'interview': 'rejected'},
    ]
    rates = _calculate_conversion_rates(data)

    assert 'CISI + Deloitte' in rates
    assert rates['CISI + Deloitte']['interviews'] == 2
    assert rates['CISI + Deloitte']['total'] == 3
    assert abs(rates['CISI + Deloitte']['rate'] - 2/3) < 0.001

    assert rates['Todlr growth']['interviews'] == 0
    assert rates['Todlr growth']['total'] == 2
    assert rates['Todlr growth']['rate'] == 0.0


def test_conversion_rates_empty():
    assert _calculate_conversion_rates([]) == {}


def test_conversion_rates_all_interviews():
    data = [{'lead_advantage': 'fintech edge', 'interview': 'interview'} for _ in range(5)]
    rates = _calculate_conversion_rates(data)
    assert rates['fintech edge']['rate'] == 1.0
    assert rates['fintech edge']['total'] == 5


def test_conversion_rates_interview_substring_match():
    """'Interview scheduled' and 'Interview' should both count."""
    data = [
        {'lead_advantage': 'X', 'interview': 'Interview scheduled'},
        {'lead_advantage': 'X', 'interview': 'interview'},
        {'lead_advantage': 'X', 'interview': 'Rejected'},
    ]
    rates = _calculate_conversion_rates(data)
    assert rates['X']['interviews'] == 2


# ---------------------------------------------------------------------------
# run_weight_optimisation (mocked sheet + config)
# ---------------------------------------------------------------------------

def _sheet_rows_from_data(data: list[dict]) -> list[list[str]]:
    """
    Build a 2D list matching the Google Sheet layout.
    Col F (index 5) = lead_advantage, Col N (index 13) = interview.
    """
    header = ['Timestamp','Company','Role','Score','Score breakdown',
              'Lead advantage','Key gaps','Status','URL','Source',
              'CL excerpt','Notes','Date posted','Interview?']
    rows = [header]
    for d in data:
        row = [''] * 14
        row[5] = d.get('lead_advantage', '')
        row[13] = d.get('interview', '')
        rows.append(row)
    return rows


def _make_opt_data(n_cisi=6, n_todlr=4):
    """Generate enough interview rows to cross the MIN_DATA_POINTS threshold."""
    data = []
    # CISI bucket: 4 interviews out of n_cisi
    for i in range(n_cisi):
        data.append({'lead_advantage': 'CISI background', 'interview': 'interview' if i < 4 else 'rejected'})
    # Todlr bucket: 1 interview out of n_todlr
    for i in range(n_todlr):
        data.append({'lead_advantage': 'Todlr growth', 'interview': 'interview' if i < 1 else 'rejected'})
    return data


def test_weight_optimisation_below_threshold():
    """Below 5 interviews → config must NOT be updated."""
    mock_data = [
        {'lead_advantage': 'CISI', 'interview': 'interview'},
        {'lead_advantage': 'CISI', 'interview': 'rejected'},
        {'lead_advantage': 'CISI', 'interview': 'none'},
        {'lead_advantage': 'CISI', 'interview': ''},
    ]  # only 1 interview — below threshold

    with patch('learning.learn._fetch_sheet_data', return_value=mock_data):
        with patch('learning.learn._load_config') as mock_load:
            with patch('learning.learn._save_config') as mock_save:
                summary = run_weight_optimisation()

    assert summary['config_updated'] is False
    mock_save.assert_not_called()


def test_weight_optimisation_picks_best_bucket():
    """Best lead_advantage (highest conversion rate with >= 3 apps) is written to config."""
    data = _make_opt_data()

    fake_cfg = {'scoring': {'min_score': 7.5, 'lead_advantage_boost': None}}

    with patch('learning.learn._fetch_sheet_data', return_value=data):
        with patch('learning.learn._load_config', return_value=fake_cfg):
            with patch('learning.learn._save_config') as mock_save:
                summary = run_weight_optimisation()

    assert summary['config_updated'] is True
    assert summary['best_lead_advantage'] == 'CISI background'  # 4/6 = 67% vs 1/4 = 25%
    saved_cfg = mock_save.call_args[0][0]
    assert saved_cfg['scoring']['lead_advantage_boost'] == 'CISI background'


def test_weight_optimisation_requires_3_in_bucket():
    """Bucket with < 3 total applications must not be selected even with 100% rate."""
    data = [
        {'lead_advantage': 'niche angle', 'interview': 'interview'},  # 1/1 = 100% but n=1
        {'lead_advantage': 'niche angle', 'interview': 'interview'},  # make it n=2 still < 3
        {'lead_advantage': 'CISI broad',  'interview': 'interview'},
        {'lead_advantage': 'CISI broad',  'interview': 'interview'},
        {'lead_advantage': 'CISI broad',  'interview': 'interview'},
        {'lead_advantage': 'CISI broad',  'interview': 'rejected'},
        {'lead_advantage': 'CISI broad',  'interview': 'rejected'},
    ]  # 5 interviews total → above threshold; niche has 2/2 but n<3

    fake_cfg = {'scoring': {'lead_advantage_boost': None}}

    with patch('learning.learn._fetch_sheet_data', return_value=data):
        with patch('learning.learn._load_config', return_value=fake_cfg):
            with patch('learning.learn._save_config'):
                summary = run_weight_optimisation()

    # CISI broad (3/5 = 60%) should win over niche (1/1 = 100%, n=1)
    assert summary['best_lead_advantage'] == 'CISI broad'


def test_weight_optimisation_no_qualified_buckets():
    """All buckets < 3 applications → no config update even above interview threshold."""
    data = [
        {'lead_advantage': 'angle A', 'interview': 'interview'},
        {'lead_advantage': 'angle B', 'interview': 'interview'},
        {'lead_advantage': 'angle C', 'interview': 'interview'},
        {'lead_advantage': 'angle D', 'interview': 'interview'},
        {'lead_advantage': 'angle E', 'interview': 'interview'},
    ]  # 5 interviews but each bucket only has n=1

    with patch('learning.learn._fetch_sheet_data', return_value=data):
        with patch('learning.learn._load_config'):
            with patch('learning.learn._save_config') as mock_save:
                summary = run_weight_optimisation()

    assert summary['config_updated'] is False
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# run_selector_evolution (uses real SQLite)
# ---------------------------------------------------------------------------

def _init_test_db(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS selector_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            field_name TEXT NOT NULL,
            selector TEXT NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 1,
            fail_count INTEGER NOT NULL DEFAULT 0,
            success_rate REAL NOT NULL DEFAULT 1.0,
            updated_at TEXT NOT NULL,
            UNIQUE(domain, field_name, selector)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS honeypot_blocklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_hash TEXT NOT NULL UNIQUE,
            domain TEXT NOT NULL,
            selector TEXT NOT NULL,
            added_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def test_selector_evolution_prunes_dead():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / 'jobs.db'
        _init_test_db(db_path)

        con = sqlite3.connect(db_path)
        now = '2025-06-01T12:00:00+00:00'
        # Good selector
        con.execute(
            "INSERT INTO selector_cache VALUES (NULL,'lever.co','email','#email',20,2,0.91,?)",
            (now,)
        )
        # Dead selector (10+ attempts, <10% success)
        con.execute(
            "INSERT INTO selector_cache VALUES (NULL,'lever.co','phone','input.bad',1,12,0.07,?)",
            (now,)
        )
        con.commit()
        con.close()

        with patch('learning.learn.DB_PATH', db_path):
            # patch _conn to use our test db
            import learning.learn as lm
            import cache.db as cdb
            orig_db = cdb.DB_PATH
            cdb.DB_PATH = db_path
            try:
                summary = run_selector_evolution()
            finally:
                cdb.DB_PATH = orig_db

        assert summary['pruned'] == 1
        assert summary['total'] == 2

        # Verify dead selector removed
        con = sqlite3.connect(db_path)
        remaining = con.execute("SELECT selector FROM selector_cache").fetchall()
        con.close()
        selectors = [r[0] for r in remaining]
        assert '#email' in selectors
        assert 'input.bad' not in selectors


def test_honeypot_report_counts_recent():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / 'jobs.db'
        _init_test_db(db_path)

        from datetime import timedelta, timezone
        import datetime as dt
        now = dt.datetime.now(timezone.utc)
        recent = (now - timedelta(days=3)).isoformat()
        old = (now - timedelta(days=30)).isoformat()

        import hashlib
        def fh(d, s):
            return hashlib.sha256(f"{d}|{s}".encode()).hexdigest()[:16]

        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO honeypot_blocklist VALUES (NULL,?,?,?,?)",
            (fh('site.com','#trap'), 'site.com', '#trap', recent)
        )
        con.execute(
            "INSERT INTO honeypot_blocklist VALUES (NULL,?,?,?,?)",
            (fh('old.com','#old'), 'old.com', '#old', old)
        )
        con.commit()
        con.close()

        import cache.db as cdb
        orig_db = cdb.DB_PATH
        cdb.DB_PATH = db_path
        try:
            summary = run_honeypot_report(since_days=7)
        finally:
            cdb.DB_PATH = orig_db

        assert summary['total_honeypots'] == 2
        assert summary['new_this_week'] == 1
        assert summary['entries'][0]['domain'] == 'site.com'


if __name__ == '__main__':
    tests = [
        test_conversion_rates_basic,
        test_conversion_rates_empty,
        test_conversion_rates_all_interviews,
        test_conversion_rates_interview_substring_match,
        test_weight_optimisation_below_threshold,
        test_weight_optimisation_picks_best_bucket,
        test_weight_optimisation_requires_3_in_bucket,
        test_weight_optimisation_no_qualified_buckets,
        test_selector_evolution_prunes_dead,
        test_honeypot_report_counts_recent,
    ]

    print('Running Stage 8 learning validation...')
    passed = failed = 0
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f'  {name} ... PASS')
            passed += 1
        except Exception as e:
            import traceback
            print(f'  {name} ... FAIL: {e}')
            traceback.print_exc()
            failed += 1

    print(f'\nStage 8 validation: {passed} passed, {failed} failed')
    if failed:
        raise SystemExit(1)
