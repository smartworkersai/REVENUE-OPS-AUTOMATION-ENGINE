"""
Pipeline orchestrator for the Lead Gen + Document Automation bot.

Design decisions:
- filelock PID file: prevents overlapping cron executions
- Global try...finally: guarantees clean exit on every path
- sync_playwright only: no async (avoids event loop deadlock with gspread)
- APScheduler: 6h pipeline + 7d learn cron (both run in-process)
- DRY_RUN=true by default — never set to false here

Pipeline stages per run:
  1. Scrape        — LinkedIn + Indeed + Reed + TotalJobs + Direct
  2. Dedup + persist — URL dedup in memory; UNIQUE(company, role, url) in DB
  3. Pre-filter    — cheap rules, no API calls
  4. KPI Score     — 6-dimension scorer (Claude API)
  5. Strategic Score + Package — packager.py (Claude API); writes 5 output files
  6. Log           — batch write to Google Sheets

learn.py runs separately on the weekly schedule.
"""

import json
import logging
import logging.handlers
import os
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from filelock import FileLock, Timeout
from rich.console import Console
from rich.logging import RichHandler

# ---------------------------------------------------------------------------
# Bootstrap — load .env before importing any module that reads env vars
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).parent / '.env', override=False)

# ---------------------------------------------------------------------------
# Logging setup — console (Rich) + rotating file
# ---------------------------------------------------------------------------

console = Console()

_LOG_LEVEL  = os.getenv('LOG_LEVEL', 'INFO').upper()
_LOG_DIR    = Path(__file__).parent / 'logs'
_LOG_FILE   = _LOG_DIR / 'bot.log'
_LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
_LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'

_LOG_DIR.mkdir(parents=True, exist_ok=True)

_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE,
    maxBytes=10 * 1024 * 1024,   # 10 MB per file
    backupCount=3,
    encoding='utf-8',
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
_file_handler.setLevel(_LOG_LEVEL)

logging.basicConfig(
    level=_LOG_LEVEL,
    format='%(message)s',
    datefmt='[%X]',
    handlers=[
        RichHandler(console=console, rich_tracebacks=True, show_path=False),
        _file_handler,
    ],
)
log = logging.getLogger('main')

# ---------------------------------------------------------------------------
# Late imports (after env is loaded)
# ---------------------------------------------------------------------------

from cache.db import (
    init_db, upsert_job, get_found_jobs, get_jobs_for_processing,
    transition, has_matching_role, JobState,
)
from scoring.filters import pre_filter
from scoring.kpi import KPIScorer, KPIScore
from generation.packager import JobPackager
from logging_.sheets import SheetLogger
from scrapers.base import Job
from utils.notify import send_notification, send_daily_digest
from utils.gmail import monitor_job_emails

_CONFIG_PATH = Path(__file__).parent / 'config.yaml'
_LOCK_PATH   = Path(__file__).parent / '.pipeline.lock'


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def _run_scrapers(cfg: dict) -> list[Job]:
    """Run all enabled scrapers and return a combined deduplicated job list."""
    keywords   = cfg['search']['keywords']
    location   = cfg['search']['location']
    days       = cfg['search']['days_since_posted']
    max_each   = cfg['search']['max_per_source']
    sources    = cfg['sources']

    all_jobs: list[Job] = []

    if sources.get('linkedin'):
        try:
            from scrapers.linkedin import LinkedInScraper, LinkedInAuthRequired
            jobs = LinkedInScraper().scrape(keywords, location, days, max_each)
            log.info('LinkedIn: scraped %d jobs', len(jobs))
            if len(jobs) == 0:
                log.warning('LinkedIn returned 0 jobs — session may have expired')
                send_notification(
                    '⚠️ <b>LinkedIn returned 0 jobs</b>\n'
                    'Session may have expired. Run <code>python test_browser.py</code> '
                    'to renew the session cookie.',
                    urgent=True,
                )
            all_jobs.extend(jobs)
        except LinkedInAuthRequired:
            log.warning(
                'LinkedIn: no valid session — run the session-save script once. '
                'Skipping LinkedIn this run.'
            )
            send_notification(
                '⚠️ <b>LinkedIn session expired</b>\n'
                'Run <code>python test_browser.py</code> to renew.',
                urgent=True,
            )
        except Exception as exc:
            log.error('LinkedIn scraper failed: %s', exc)

    if sources.get('indeed'):
        try:
            from scrapers.indeed import IndeedScraper
            jobs = IndeedScraper().scrape(keywords, location, days, max_each)
            log.info('Indeed: scraped %d jobs', len(jobs))
            all_jobs.extend(jobs)
        except Exception as exc:
            log.error('Indeed scraper failed: %s', exc)

    if sources.get('glassdoor'):
        try:
            from scrapers.glassdoor import GlassdoorScraper
            jobs = GlassdoorScraper().scrape(keywords, location, days, max_each)
            log.info('Glassdoor: scraped %d jobs', len(jobs))
            all_jobs.extend(jobs)
        except Exception as exc:
            log.error('Glassdoor scraper failed: %s', exc)

    if sources.get('reed'):
        try:
            from scrapers.reed import ReedScraper
            jobs = ReedScraper().scrape(keywords, location, days, max_each)
            log.info('Reed: scraped %d jobs', len(jobs))
            if len(jobs) == 0:
                log.warning('Reed returned 0 jobs — possible selector change or block')
            all_jobs.extend(jobs)
        except Exception as exc:
            log.error('Reed scraper failed: %s', exc)

    if sources.get('totaljobs'):
        try:
            from scrapers.totaljobs import TotalJobsScraper
            jobs = TotalJobsScraper().scrape(keywords, location, days, max_each)
            log.info('TotalJobs: scraped %d jobs', len(jobs))
            if len(jobs) == 0:
                log.warning('TotalJobs returned 0 jobs — possible selector change or block')
            all_jobs.extend(jobs)
        except Exception as exc:
            log.error('TotalJobs scraper failed: %s', exc)

    if sources.get('efinancialcareers'):
        try:
            from scrapers.efinancialcareers import EFinancialCareersScraper
            jobs = EFinancialCareersScraper().scrape(keywords, location, days, max_each)
            log.info('eFinancialCareers: scraped %d jobs', len(jobs))
            if len(jobs) == 0:
                send_notification(
                    '⚠️ <b>eFinancialCareers returned 0 jobs</b>\n'
                    f'keywords={keywords[:3]}… location={location}',
                    urgent=False,
                )
            all_jobs.extend(jobs)
        except Exception as exc:
            log.error('eFinancialCareers scraper failed: %s', exc)
            send_notification(
                f'⚠️ <b>eFinancialCareers scraper error</b>\n<code>{exc}</code>',
                urgent=False,
            )

    for site in sources.get('direct_sites', []):
        try:
            from scrapers.direct import DirectScraper
            jobs = DirectScraper().scrape(keywords, location, days, max_each, sites=[site])
            log.info('Direct [%s]: scraped %d jobs', site['name'], len(jobs))
            all_jobs.extend(jobs)
        except Exception as exc:
            log.error('Direct scraper [%s] failed: %s', site['name'], exc)

    # Dedup across sources by URL
    seen: set[str] = set()
    deduped: list[Job] = []
    for j in all_jobs:
        if j.url not in seen:
            seen.add(j.url)
            deduped.append(j)

    log.info('Scrapers total: %d unique jobs (before DB dedup)', len(deduped))
    return deduped


# ---------------------------------------------------------------------------
# Pipeline: pre-filter → KPI score → strategic package → log
# ---------------------------------------------------------------------------

def _run_pipeline(cfg: dict, sheet_logger: SheetLogger) -> dict:
    """
    Main pipeline loop. Returns a summary dict.
    All exceptions within a job are caught — pipeline continues.

    Flow: SCRAPE → DEDUP → PRE-FILTER → KPI SCORE → STRATEGIC PACKAGE → LOG
    """
    min_score    = cfg['scoring']['min_score']
    max_to_score = cfg['scoring'].get('max_to_score_per_run', 20)

    scorer   = KPIScorer()
    packager = JobPackager()

    stats = {
        'scraped': 0, 'new': 0, 'filtered': 0,
        'scored': 0, 'skipped_score': 0,
        'packaged': 0, 'failed': 0,
        'errors': [], 'top_jobs': [],
    }

    # --- Stage 1: Scrape + persist to DB ---
    jobs = _run_scrapers(cfg)
    stats['scraped'] = len(jobs)

    for job in jobs:
        job_id = upsert_job(
            company=job.company, role=job.role, url=job.url,
            source=job.source, date_posted=job.date_posted,
            salary_raw=job.salary_raw, location_raw=job.location_raw,
            jd_text=job.jd_text,
        )
        if job_id:
            stats['new'] += 1

    # --- Stage 2: Pre-filter FOUND jobs ---
    found = get_found_jobs()
    log.info('FOUND jobs to pre-filter: %d', len(found))

    for row in found:
        job = Job(
            company=row['company'], role=row['role'], url=row['url'],
            source=row['source'], jd_text=row['jd_text'] or '',
            salary_raw=row['salary_raw'] or '', location_raw=row['location_raw'] or '',
            date_posted=row['date_posted'],
        )
        result = pre_filter(job)
        if not result.passed:
            # Store rejection reason in notes; keep state FOUND with a score sentinel
            # so get_jobs_for_processing() skips it (score IS NOT NULL).
            transition(row['id'], JobState.FOUND, score=-1.0, notes=result.reason)
            log.info('Filtered — low fit: %s / %s (%s)', job.company, job.role, result.reason)
            sheet_logger.log({
                'date':        datetime.now(timezone.utc).date().isoformat(),
                'company':     job.company,
                'role':        job.role,
                'score':       '',
                'url':         job.url,
                'local_folder': '',
                'status':      f'Filtered — low fit: {result.reason}',
            })
            stats['filtered'] += 1
        else:
            transition(row['id'], JobState.SCORED)

    # --- Stages 3–5: KPI Score → Strategic Package → Log ---
    processable = get_jobs_for_processing()
    log.info('Jobs ready for processing: %d', len(processable))
    scored_this_run = 0

    for row in processable:
        job_id = row['id']
        job = Job(
            company=row['company'], role=row['role'], url=row['url'],
            source=row['source'], jd_text=row['jd_text'] or '',
            salary_raw=row['salary_raw'] or '', location_raw=row['location_raw'] or '',
            date_posted=row['date_posted'],
        )

        try:
            kpi: KPIScore | None = None

            # Reconstruct KPI from DB if already scored in a prior run
            if row['score_breakdown']:
                try:
                    kpi = KPIScore.model_validate(json.loads(row['score_breakdown']))
                except Exception as _e:
                    log.debug('Could not reconstruct KPIScore from DB: %s', _e)

            # --- Stage 3: KPI Score ---
            if row['state'] == JobState.SCORED and kpi is None:
                # Cross-source dedup: skip if another source already scored this role
                if has_matching_role(job.company, job.role, job_id):
                    log.info(
                        'Cross-source dedup: %s / %s already scored from another source — skipping',
                        job.company, job.role,
                    )
                    transition(job_id, JobState.FOUND, score=-1.0, notes='Cross-source duplicate')
                    stats['filtered'] += 1
                    continue

                if scored_this_run >= max_to_score:
                    log.debug('Scoring cap (%d) reached — deferring %s/%s', max_to_score, job.company, job.role)
                    continue

                try:
                    kpi = scorer.score(job)
                except Exception as score_exc:
                    log.error('Scoring failed for %s/%s: %s', job.company, job.role, score_exc)
                    # Leave in SCORED state so it retries next run; cap notes length
                    transition(job_id, JobState.SCORED, notes=str(score_exc)[:500])
                    stats['failed'] += 1
                    stats['errors'].append(f'{job.company}/{job.role}: score failed: {score_exc}')
                    continue

                scored_this_run += 1

                if kpi is None or kpi.final_score < min_score:
                    score_val = kpi.final_score if kpi else 0.0
                    notes = f'KPI {score_val:.2f} < threshold {min_score}'
                    # score=-1.0 sentinel keeps get_jobs_for_processing() from re-queuing
                    transition(job_id, JobState.FOUND, score=-1.0, notes=notes)
                    log.info('Skipped (low KPI): %s / %s — %s', job.company, job.role, notes)
                    sheet_logger.log({
                        'date':        datetime.now(timezone.utc).date().isoformat(),
                        'company':     job.company,
                        'role':        job.role,
                        'score':       f'{score_val:.1f}',
                        'url':         job.url,
                        'local_folder': '',
                        'status':      f'Filtered — low fit: {notes}',
                    })
                    stats['skipped_score'] += 1
                    continue

                transition(
                    job_id, JobState.SCORED,
                    score=kpi.final_score,
                    score_breakdown=kpi.model_dump(),
                    lead_advantage=kpi.lead_advantage,
                    key_gaps=', '.join(kpi.key_gaps) if kpi.key_gaps else '',
                )
                stats['scored'] += 1

            # --- Stage 4: Strategic Score + Package ---
            # package_job_assets() runs the strategic scorer (cheap, ~512 tokens)
            # then only generates documents if interview_probability >= 7 AND
            # salary_ceiling_3yr >= 55,000.
            result = packager.package_job_assets(job_id, job, kpi_score=kpi)

            if result.passed:
                transition(
                    job_id, JobState.GENERATED,
                    local_folder=result.local_folder,
                )
                send_notification(
                    f'📁 <b>{job.company}</b> — {job.role}\n'
                    f'KPI <b>{kpi.final_score:.1f}</b>  '
                    f'P(interview)={result.interview_probability}  '
                    f'Salary 3yr ceiling=£{result.salary_ceiling_3yr:,}\n'
                    f'Floor ask: £{result.recommended_floor_salary:,}  '
                    f'Track: {result.cv_track}\n'
                    f'📂 {result.local_folder}',
                )
                sheet_logger.log({
                    'date':         datetime.now(timezone.utc).date().isoformat(),
                    'company':      job.company,
                    'role':         job.role,
                    'score':        f'{kpi.final_score:.1f}' if kpi else '',
                    'url':          job.url,
                    'local_folder': result.local_folder or '',
                    'status':       'Pending Manual Submission',
                })
                stats['packaged'] += 1
                stats['top_jobs'].append({
                    'company':              job.company,
                    'role':                 job.role,
                    'score':                kpi.final_score if kpi else '',
                    'interview_probability': result.interview_probability,
                })
            else:
                # Strategic gate failed — leave SCORED so we don't retry next run
                transition(job_id, JobState.FOUND, score=-1.0, notes=f'Strategic gate: {result.rationale}')
                log.info(
                    'Strategic gate failed: %s / %s — %s',
                    job.company, job.role, result.rationale,
                )
                sheet_logger.log({
                    'date':         datetime.now(timezone.utc).date().isoformat(),
                    'company':      job.company,
                    'role':         job.role,
                    'score':        f'{kpi.final_score:.1f}' if kpi else '',
                    'url':          job.url,
                    'local_folder': '',
                    'status':       f'Filtered — low fit: {result.rationale}',
                })
                stats['skipped_score'] += 1

        except Exception as exc:
            log.error('Pipeline error for %s/%s: %s', job.company, job.role, exc, exc_info=True)
            sheet_logger.log({
                'date':         datetime.now(timezone.utc).date().isoformat(),
                'company':      job.company,
                'role':         job.role,
                'score':        '',
                'url':          job.url,
                'local_folder': '',
                'status':       'Failed',
            })
            stats['failed'] += 1
            stats['errors'].append(f'{job.company}/{job.role}: {exc}')

        # Periodic flush every 5 jobs to avoid losing data on crash
        sheet_logger.flush_if_pending(threshold=5)

    return stats


# ---------------------------------------------------------------------------
# Single pipeline run (called by scheduler + directly)
# ---------------------------------------------------------------------------

def _startup_validation() -> list[str]:
    """
    SY7: Validate required env vars and files before pipeline begins.
    Returns a list of warning strings (non-fatal; pipeline continues but degraded).
    """
    warnings = []

    if not os.getenv('ANTHROPIC_API_KEY'):
        warnings.append('ANTHROPIC_API_KEY not set — scoring and generation will fail')

    assets_dir = Path(__file__).parent / 'assets'
    for cv_file in ('cv_marketing.pdf', 'cv_technical.pdf'):
        if not (assets_dir / cv_file).exists():
            warnings.append(f'Base CV not found: assets/{cv_file}')

    tpl_path = os.getenv('CV_TEMPLATE_PATH', './assets/cv_template.html')
    if not Path(tpl_path).exists():
        warnings.append(f'CV template not found: {tpl_path}')

    sa_path = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', './secrets/service_account.json')
    if not Path(sa_path).exists():
        warnings.append(f'Google service account JSON not found: {sa_path} — Sheets logging disabled')

    from utils.browser import LINKEDIN_PROFILE_DIR
    if not LINKEDIN_PROFILE_DIR.exists():
        warnings.append(
            f'LinkedIn profile not found: {LINKEDIN_PROFILE_DIR} — '
            'LinkedIn scraping will be skipped. Run: PYTHONPATH="." python test_browser.py'
        )

    # Disk space check — warn if free space < 2 GB
    try:
        free_bytes = shutil.disk_usage(Path(__file__).parent).free
        free_gb    = free_bytes / (1024 ** 3)
        if free_gb < 2.0:
            msg = f'Low disk space: {free_gb:.1f} GB free (threshold 2 GB) — screenshots and PDFs may fail'
            warnings.append(msg)
            send_notification(f'⚠️ <b>Disk space warning</b>\n{msg}', urgent=True)
    except Exception as exc:
        log.debug('Disk space check failed: %s', exc)

    return warnings


_LOCK_STALE_HOURS = 2   # lock files older than this are assumed stale


def _clear_stale_lock() -> None:
    """
    Delete .pipeline.lock if it exists and is older than _LOCK_STALE_HOURS.
    A lock this old means the previous process was killed without cleanup
    (SIGKILL, OOM, power loss). Logging at WARNING so it's visible in the run log.
    """
    if not _LOCK_PATH.exists():
        return
    age_seconds = time.time() - _LOCK_PATH.stat().st_mtime
    if age_seconds > _LOCK_STALE_HOURS * 3600:
        log.warning(
            'Stale lock file detected (age=%.0fh) — deleting and proceeding: %s',
            age_seconds / 3600,
            _LOCK_PATH,
        )
        try:
            _LOCK_PATH.unlink(missing_ok=True)
        except OSError as exc:
            log.warning('Could not delete stale lock file: %s', exc)


def run_once() -> None:
    """Run one full pipeline cycle. Guarded by filelock."""
    cfg = _load_config()

    # SY7: startup validation
    for warn in _startup_validation():
        log.warning('Startup: %s', warn)

    # Remove stale lock before attempting to acquire (prevents permanent blockage
    # after an unclean shutdown — SIGKILL, OOM, host restart).
    _clear_stale_lock()

    lock = FileLock(str(_LOCK_PATH), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        log.warning('Another pipeline instance is running — exiting immediately.')
        return

    # Install signal handlers AFTER acquiring the lock so SIGTERM/SIGINT always
    # release it cleanly. Both signals call lock.release() then re-raise so the
    # process exits with the correct status code (128 + signum).
    def _handle_signal(signum, frame):
        log.warning('Signal %d received — releasing lock and exiting cleanly', signum)
        try:
            lock.release()
        except Exception:
            pass
        signal.signal(signum, signal.SIG_DFL)   # restore default handler
        signal.raise_signal(signum)              # re-raise so exit code is correct

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    sheet_logger = SheetLogger()
    sheet_logger.connect()  # health check; pipeline continues even if False

    started = time.monotonic()
    dry_run_label = ' [DRY RUN]' if os.getenv('DRY_RUN', 'true').lower() == 'true' else ''
    log.info('=== Pipeline run starting (DRY_RUN=%s) ===', os.getenv('DRY_RUN', 'true'))
    send_notification(
        f'🤖 <b>Job Bot started{dry_run_label}</b>\n'
        f'Scanning for roles — Telegram notifications active.',
    )

    try:
        init_db()

        # --- Email monitoring: run before scraping so urgent items reach Telegram first ---
        try:
            email_findings = monitor_job_emails()
            for finding in email_findings:
                if finding['urgent']:
                    category = finding['category'].capitalize()
                    send_notification(
                        f'📬 <b>{category}</b>: {finding["subject"][:120]}\n'
                        f'From: {finding["sender"][:80]}',
                        urgent=True,
                    )
                    log.info(
                        'Gmail [%s] urgent: %s — %s',
                        finding['category'], finding['company'] or finding['sender'],
                        finding['subject'][:80],
                    )
                else:
                    log.info(
                        'Gmail [%s]: %s — %s',
                        finding['category'], finding['company'] or finding['sender'],
                        finding['subject'][:80],
                    )
        except Exception as gmail_exc:
            log.warning('Gmail monitoring error (non-fatal): %s', gmail_exc)

        stats = _run_pipeline(cfg, sheet_logger)
        send_notification(
            f'🏁 <b>Pipeline complete{dry_run_label}</b>\n'
            f'Scraped: {stats.get("scraped", 0)}  '
            f'Packaged: {stats.get("packaged", 0)}  '
            f'Failed: {stats.get("failed", 0)}',
        )
        if stats.get('packaged', 0) > 0:
            sheet_id  = os.getenv('GOOGLE_SHEET_ID', '')
            sheet_url = f'https://docs.google.com/spreadsheets/d/{sheet_id}' if sheet_id else ''
            send_daily_digest(
                jobs_packaged_count=stats['packaged'],
                top_jobs=stats.get('top_jobs', []),
                sheet_url=sheet_url,
            )
    except Exception as exc:
        log.error('Pipeline run crashed: %s', exc, exc_info=True)
        send_notification(
            f'❌ <b>Pipeline crashed</b>\n<code>{str(exc)[:300]}</code>',
            urgent=True,
        )
        stats = {}
    finally:
        flush_count = sheet_logger.flush()
        elapsed = time.monotonic() - started
        log.info(
            '=== Pipeline run complete: %s | sheet_rows=%d | elapsed=%.0fs ===',
            stats, flush_count, elapsed,
        )
        lock.release()


# ---------------------------------------------------------------------------
# Weekly learn run
# ---------------------------------------------------------------------------

def run_learn() -> None:
    """Run the weekly self-improvement cycle."""
    try:
        from learning.learn import run_weekly_learn
        run_weekly_learn()
    except Exception as exc:
        log.error('Learn cycle failed: %s', exc, exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def _start_scheduler(cfg: dict) -> None:
    """
    Start APScheduler with two jobs:
    - Pipeline: every pipeline_every_hours hours
    - Learn:    every learn_every_days days

    Runs in-process (blocking). Use Ctrl-C or SIGTERM to stop.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    pipeline_hours = cfg['schedule']['pipeline_every_hours']
    learn_days     = cfg['schedule']['learn_every_days']

    scheduler = BlockingScheduler(timezone='UTC')

    scheduler.add_job(
        run_once,
        trigger=IntervalTrigger(hours=pipeline_hours),
        id='pipeline',
        name=f'Pipeline (every {pipeline_hours}h)',
        max_instances=1,            # prevent overlap (lock is a second guard)
        coalesce=True,              # skip missed runs rather than catching up
        misfire_grace_time=300,     # 5-minute grace window
    )

    scheduler.add_job(
        run_learn,
        trigger=IntervalTrigger(days=learn_days),
        id='learn',
        name=f'Learn cycle (every {learn_days}d)',
        max_instances=1,
        coalesce=True,
    )

    log.info(
        'Scheduler started: pipeline every %dh, learn every %dd',
        pipeline_hours, learn_days,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info('Scheduler stopped by user.')
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Usage:
      python main.py           — run once immediately, then exit
      python main.py --daemon  — run immediately then start scheduler loop
      python main.py --learn   — run learn cycle only, then exit
    """
    args = sys.argv[1:]
    cfg = _load_config()

    if '--learn' in args:
        run_learn()
        return

    # Always run once immediately on startup
    run_once()

    if '--daemon' in args:
        _start_scheduler(cfg)


if __name__ == '__main__':
    main()
