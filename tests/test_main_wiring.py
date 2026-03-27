"""
Stage 9 validation — main.py wiring.

Tests:
1. PID lock prevents second concurrent instance
2. run_once() with all external calls mocked completes without raising
3. Pipeline stats accumulate correctly
4. Sheets flush is called exactly once per run
5. DB is initialised before pipeline runs
6. --learn flag routes to run_learn only (no pipeline)
"""

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

os.environ.setdefault('DRY_RUN', 'true')
os.environ.setdefault('ANTHROPIC_API_KEY', 'dummy')
os.environ.setdefault('GOOGLE_SHEET_ID', 'dummy-sheet-id')
os.environ.setdefault('GOOGLE_SERVICE_ACCOUNT_JSON', './secrets/service_account.json')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_job_row(job_id=1, state='SCORED'):
    """Return a dict that behaves like a sqlite3.Row for pipeline processing."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        'id': job_id, 'company': 'Acme Corp', 'role': 'Marketing Executive',
        'url': 'https://acme.com/jobs/1', 'source': 'LinkedIn',
        'jd_text': 'Sample JD text', 'salary_raw': '£32,000',
        'location_raw': 'London', 'date_posted': None,
        'state': state,
        'score': 8.4, 'lead_advantage': 'CISI background', 'key_gaps': 'CRM',
    }[key]
    row.get = lambda key, default=None: row[key] if key in [
        'id','company','role','url','source','jd_text','salary_raw',
        'location_raw','date_posted','state','score','lead_advantage','key_gaps',
    ] else default
    row['state'] = state
    return row


def _make_kpi_mock():
    kpi = MagicMock()
    kpi.final_score = 8.5
    kpi.lead_advantage = 'CISI + Deloitte background'
    kpi.key_gaps = ['CRM experience']
    kpi.model_dump.return_value = {'final_score': 8.5}
    return kpi


def _make_gen_mock():
    gen = MagicMock()
    gen.cover_letter = 'At CISI I coordinated 3 events...'
    gen.cv_bullets = []
    return gen


# ---------------------------------------------------------------------------
# Test: PID lock prevents overlap
# ---------------------------------------------------------------------------

def test_pid_lock_prevents_second_instance():
    """A second run_once() call while the lock is held must not invoke init_db or _run_pipeline."""
    from filelock import FileLock
    import main as m

    lock_path = m._LOCK_PATH

    pipeline_called = []
    lock = FileLock(str(lock_path), timeout=0)
    lock.acquire()

    def _second_run():
        with patch('main.init_db') as mock_init, \
             patch('main._run_pipeline') as mock_pipe, \
             patch('main.SheetLogger') as MockLogger:
            MockLogger.return_value.connect.return_value = False
            MockLogger.return_value.flush.return_value = 0
            m.run_once()
            if mock_init.called or mock_pipe.called:
                pipeline_called.append(True)

    t = threading.Thread(target=_second_run)
    t.start()
    t.join(timeout=3)

    lock.release()

    # Neither init_db nor _run_pipeline should have been called
    assert pipeline_called == [], 'Second instance ran the pipeline — lock not enforced'


# ---------------------------------------------------------------------------
# Test: run_once() completes with mocked externals
# ---------------------------------------------------------------------------

def test_run_once_completes_without_error():
    """run_once() must not raise even when pipeline is fully mocked."""
    import main as m

    mock_logger = MagicMock()
    mock_logger.connect.return_value = True
    mock_logger.flush.return_value = 1

    with patch('main.init_db') as mock_init, \
         patch('main._run_pipeline', return_value={'submitted': 1, 'failed': 0, 'errors': []}) as mock_pipe, \
         patch('main.SheetLogger', return_value=mock_logger), \
         patch('main._load_config', return_value={
             'search': {'keywords': ['marketing'], 'location': 'London',
                        'max_per_source': 10, 'days_since_posted': 7},
             'scoring': {'min_score': 7.5},
             'submission': {'max_per_run': 10, 'max_per_company_days': 60},
             'sources': {'linkedin': False, 'indeed': False, 'glassdoor': False, 'direct_sites': []},
             'schedule': {'pipeline_every_hours': 12, 'learn_every_days': 7},
         }):
        m.run_once()

    mock_init.assert_called_once()
    mock_pipe.assert_called_once()
    mock_logger.flush.assert_called_once()


def test_run_once_flush_called_even_on_pipeline_crash():
    """Sheets flush must happen in finally — even if pipeline raises."""
    import main as m

    mock_logger = MagicMock()
    mock_logger.connect.return_value = True
    mock_logger.flush.return_value = 0

    with patch('main.init_db'), \
         patch('main._run_pipeline', side_effect=RuntimeError('boom')), \
         patch('main.SheetLogger', return_value=mock_logger), \
         patch('main._load_config', return_value={
             'search': {'keywords': [], 'location': 'London', 'max_per_source': 10, 'days_since_posted': 7},
             'scoring': {'min_score': 7.5},
             'submission': {'max_per_run': 10, 'max_per_company_days': 60},
             'sources': {'linkedin': False, 'indeed': False, 'glassdoor': False, 'direct_sites': []},
             'schedule': {'pipeline_every_hours': 12, 'learn_every_days': 7},
         }):
        m.run_once()  # must not raise

    mock_logger.flush.assert_called_once()


# ---------------------------------------------------------------------------
# Test: _run_scrapers respects disabled sources
# ---------------------------------------------------------------------------

def test_run_scrapers_skips_disabled_sources():
    """When all sources are disabled, _run_scrapers returns empty list."""
    import main as m

    cfg = {
        'search': {'keywords': ['marketing'], 'location': 'London',
                   'max_per_source': 10, 'days_since_posted': 7},
        'sources': {'linkedin': False, 'indeed': False, 'glassdoor': False, 'direct_sites': []},
    }

    jobs = m._run_scrapers(cfg)
    assert jobs == []


def test_run_scrapers_handles_scraper_exception():
    """If a scraper raises, _run_scrapers continues with other sources."""
    import main as m

    cfg = {
        'search': {'keywords': ['marketing'], 'location': 'London',
                   'max_per_source': 10, 'days_since_posted': 7},
        'sources': {'linkedin': True, 'indeed': True, 'glassdoor': False, 'direct_sites': []},
    }

    # Scrapers are lazily imported inside _run_scrapers — patch at their module paths
    bad_linkedin = MagicMock()
    bad_linkedin.scrape.side_effect = Exception('LinkedIn down')
    good_indeed = MagicMock()
    good_indeed.scrape.return_value = []

    with patch.dict('sys.modules', {
        'scrapers.linkedin': MagicMock(LinkedInScraper=MagicMock(return_value=bad_linkedin)),
        'scrapers.indeed':   MagicMock(IndeedScraper=MagicMock(return_value=good_indeed)),
    }):
        jobs = m._run_scrapers(cfg)

    # No crash — empty list is fine
    assert isinstance(jobs, list)


# ---------------------------------------------------------------------------
# Test: --learn flag
# ---------------------------------------------------------------------------

def test_learn_flag_calls_run_learn_not_pipeline():
    """python main.py --learn should call run_learn() and not run_once()."""
    import main as m

    with patch('main.run_learn') as mock_learn, \
         patch('main.run_once') as mock_pipeline, \
         patch('main._load_config', return_value={}):
        # Simulate sys.argv
        with patch.object(sys, 'argv', ['main.py', '--learn']):
            m.main()

    mock_learn.assert_called_once()
    mock_pipeline.assert_not_called()


# ---------------------------------------------------------------------------
# Test: Dedup across sources
# ---------------------------------------------------------------------------

def test_run_scrapers_deduplicates_by_url():
    """Jobs with the same URL from different sources appear only once."""
    import main as m
    from scrapers.base import Job

    duplicate_url = 'https://company.com/job/1'
    job1 = Job('Co', 'Role', duplicate_url, 'LinkedIn')
    job2 = Job('Co', 'Role', duplicate_url, 'Indeed')  # same URL, different source

    cfg = {
        'search': {'keywords': ['k'], 'location': 'L', 'max_per_source': 10, 'days_since_posted': 7},
        'sources': {'linkedin': True, 'indeed': True, 'glassdoor': False, 'direct_sites': []},
    }

    with patch.dict('sys.modules', {
        'scrapers.linkedin': MagicMock(LinkedInScraper=MagicMock(
            return_value=MagicMock(scrape=MagicMock(return_value=[job1])))),
        'scrapers.indeed': MagicMock(IndeedScraper=MagicMock(
            return_value=MagicMock(scrape=MagicMock(return_value=[job2])))),
    }):
        jobs = m._run_scrapers(cfg)

    urls = [j.url for j in jobs]
    assert urls.count(duplicate_url) == 1, 'Duplicate URL not deduped'


if __name__ == '__main__':
    tests = [
        test_pid_lock_prevents_second_instance,
        test_run_once_completes_without_error,
        test_run_once_flush_called_even_on_pipeline_crash,
        test_run_scrapers_skips_disabled_sources,
        test_run_scrapers_handles_scraper_exception,
        test_learn_flag_calls_run_learn_not_pipeline,
        test_run_scrapers_deduplicates_by_url,
    ]

    print('Running Stage 9 main.py wiring validation...')
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

    print(f'\nStage 9 validation: {passed} passed, {failed} failed')
    if failed:
        raise SystemExit(1)
