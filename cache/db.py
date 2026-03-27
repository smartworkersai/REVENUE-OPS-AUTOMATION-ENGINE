# cache/db.py
# Purpose: SQLite state machine and core DB operations for Lead Gen + Doc Automation pipeline
# Created: 2026-03-25
# Last Modified: 2026-03-27

# --- Imports ---

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


# --- Constants ---

DB_PATH = Path(__file__).parent.parent / 'cache' / 'jobs.db'


# --- State machine ---

class JobState(str, Enum):
    """
    3-state pipeline machine:

    FOUND     → job discovered by scraper and persisted to DB
    SCORED    → passed pre-filter AND KPI score >= threshold; documents not yet generated
    GENERATED → cover letter, tailored CV, advice, and score summary written to output/jobs/
    """
    FOUND     = 'FOUND'
    SCORED    = 'SCORED'
    GENERATED = 'GENERATED'


# --- Classes / Functions ---

@contextmanager
def _conn():
    """Context manager yielding a SQLite connection with WAL mode enabled."""
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    """Create the jobs table if it does not exist. Safe to call multiple times."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                company         TEXT    NOT NULL,
                role            TEXT    NOT NULL,
                url             TEXT    NOT NULL,
                source          TEXT    NOT NULL,
                state           TEXT    NOT NULL DEFAULT 'FOUND',
                score           REAL,
                score_breakdown TEXT,       -- JSON string
                lead_advantage  TEXT,
                key_gaps        TEXT,       -- comma-separated
                cover_letter    TEXT,
                cv_path         TEXT,
                date_posted     TEXT,       -- UTC ISO-8601
                salary_raw      TEXT,
                location_raw    TEXT,
                jd_text         TEXT,
                notes           TEXT,
                local_folder    TEXT,       -- path to output/jobs/[Company]_[Role]/
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                UNIQUE(company, role, url)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
        """)
        # Non-destructive migrations for DBs created before this schema version
        for _migration in [
            "ALTER TABLE jobs ADD COLUMN cv_format TEXT",
            "ALTER TABLE jobs ADD COLUMN local_folder TEXT",
        ]:
            try:
                con.execute(_migration)
            except Exception:
                pass  # column already exists


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_job(
    company: str,
    role: str,
    url: str,
    source: str,
    date_posted: Optional[str] = None,
    salary_raw: Optional[str] = None,
    location_raw: Optional[str] = None,
    jd_text: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Optional[int]:
    """
    Insert a newly discovered job. Returns the row id on first insert.
    Returns None if (company, role, url) already exists — already seen.
    """
    now = _now()
    with _conn() as con:
        try:
            cur = con.execute(
                """
                INSERT INTO jobs
                    (company, role, url, source, state, date_posted,
                     salary_raw, location_raw, jd_text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (company, role, url, source, JobState.FOUND,
                 date_posted, salary_raw, location_raw, jd_text, now, now),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # duplicate


def transition(job_id: int, new_state: JobState, **fields) -> None:
    """
    Move a job to a new state. Pass additional column updates as kwargs.
    Supported kwargs: score, score_breakdown (dict), lead_advantage,
                      key_gaps, cover_letter, cv_path, local_folder, notes
    """
    now = _now()
    sets = ['state = ?', 'updated_at = ?']
    values: list = [new_state.value, now]

    if 'score' in fields:
        sets.append('score = ?')
        values.append(fields['score'])
    if 'score_breakdown' in fields:
        sets.append('score_breakdown = ?')
        values.append(json.dumps(fields['score_breakdown']))
    if 'lead_advantage' in fields:
        sets.append('lead_advantage = ?')
        values.append(fields['lead_advantage'])
    if 'key_gaps' in fields:
        sets.append('key_gaps = ?')
        values.append(fields['key_gaps'])
    if 'cover_letter' in fields:
        sets.append('cover_letter = ?')
        values.append(fields['cover_letter'])
    if 'cv_path' in fields:
        sets.append('cv_path = ?')
        values.append(fields['cv_path'])
    if 'local_folder' in fields:
        sets.append('local_folder = ?')
        values.append(fields['local_folder'])
    if 'notes' in fields:
        sets.append('notes = ?')
        values.append(fields['notes'])

    values.append(job_id)
    with _conn() as con:
        con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", values)


def get_found_jobs() -> list[sqlite3.Row]:
    """Return all FOUND jobs (need pre-filter + scoring)."""
    with _conn() as con:
        return con.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY created_at ASC",
            (JobState.FOUND,),
        ).fetchall()


def get_jobs_for_processing() -> list[sqlite3.Row]:
    """
    Return jobs that need active processing:
    - FOUND with score IS NULL: not yet scored (includes pre-filter step)
    - SCORED: passed threshold, awaiting document generation
    Jobs in FOUND state with score already set were scored below threshold — skip them.
    """
    with _conn() as con:
        return con.execute(
            """
            SELECT * FROM jobs
            WHERE (state = ? AND score IS NULL) OR state = ?
            ORDER BY created_at ASC
            """,
            (JobState.FOUND, JobState.SCORED),
        ).fetchall()


def get_job(job_id: int) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def already_seen(company: str, role: str, url: str) -> bool:
    """Check if (company, role, url) triple exists regardless of state."""
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM jobs WHERE company=? AND role=? AND url=?",
            (company, role, url),
        ).fetchone()
    return row is not None


def _normalise(s: str) -> str:
    """Lowercase, strip, and collapse internal whitespace for fuzzy (company, role) matching."""
    return re.sub(r'\s+', ' ', s.strip()).lower()


def has_matching_role(company: str, role: str, exclude_job_id: int) -> bool:
    """
    Return True if another row with the same (company, role) — case-insensitive,
    whitespace-normalised — exists and has already been scored or packaged.

    Catches cross-source duplicates where Reed returns "Marketing Analyst" and
    TotalJobs returns "marketing analyst" for the same posting.

    Python side:  strip() + re.sub(r'\\s+', ' ', ...) + lower()
    SQL side:     LOWER(TRIM(...)) on both columns

    Use this before the scorer to skip cross-source duplicates:
      if has_matching_role(job.company, job.role, job_id):
          # another source already scored this role — skip
    """
    norm_company = _normalise(company)
    norm_role    = _normalise(role)
    with _conn() as con:
        row = con.execute(
            """
            SELECT id FROM jobs
            WHERE LOWER(TRIM(company)) = ? AND LOWER(TRIM(role)) = ?
              AND id != ?
              AND (state = ? OR score IS NOT NULL)
            LIMIT 1
            """,
            (norm_company, norm_role, exclude_job_id, JobState.SCORED),
        ).fetchone()
    return row is not None


# --- Exports (if applicable) ---
# JobState, init_db, upsert_job, transition, get_found_jobs,
# get_jobs_for_processing, get_job, already_seen, has_matching_role
