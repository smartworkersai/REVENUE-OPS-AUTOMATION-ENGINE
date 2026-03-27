# MASTER_STATUS.md — Job Bot Living State
# Last updated: 2026-03-27

---

## SYSTEM HEALTH

| Component | Status | Notes |
|-----------|--------|-------|
| LinkedIn session | NEEDS VERIFICATION | Persistent profile exists; li_at present in cookies.sqlite but fresh login not yet confirmed working via _verify_auth() |
| Anthropic API | UNKNOWN | Not tested this session |
| Proxy (Decodo) | UNKNOWN | Not tested this session |
| Telegram | UNKNOWN | Not tested this session |
| Google Sheets | UNKNOWN | Not tested this session |
| DRY_RUN flag | TRUE (default) | Never changed — safe |

---

## COMPLETED THIS SESSION

### PIVOT: Auto-Submission → Lead Gen + Document Automation (2026-03-27)

Complete architectural pivot executed in 7 steps. The bot no longer submits applications.
It scrapes, scores, generates application docs, and deposits them to `output/jobs/` for
manual review and submission.

#### Step 1 — PURGE (submission layer removed)
- **Deleted:** entire `submission/` folder (11 files), `tests/test_submission_dry_run.py`, `test_workday.py`
- **Modified:** `main.py` — removed all submission imports, Stage 3.5 probe gate, Stage 6 submit block
- **Modified:** `e2e_validate.py` — removed `submit_dry_run()` and result.status references
- Zero submission imports remain anywhere in codebase (verified with grep)

#### Step 2 — STATE MACHINE (cache/db.py rewritten)
- **3 states only:** `FOUND → SCORED → GENERATED`
- Removed: 10 states (PREFILTERED, SKIPPED, SCORE_FAILED, GENERATE_FAILED, COMPILED, COMPILE_FAILED, SUBMITTED, FAILED, REQUIRES_MANUAL, DRY_RUN)
- Removed: `count_recent_submissions()`, `record_submission()`, `get_account()`, `save_account()`, `touch_account()`, selector cache, honeypot detection
- Added: `get_found_jobs()`, updated `get_jobs_for_processing()` with `score IS NULL` sentinel
- Added: `local_folder TEXT` column to jobs table

#### Step 3 — PACKAGER (generation/packager.py — NEW FILE)
- `JobPackager` class: two-call design — strategic scorer (claude-sonnet-4-5, ~512 tokens) then doc generator (claude-sonnet-4-6, ~4,000 tokens)
- Gate: `interview_probability >= 7 AND salary_ceiling_3yr >= 55,000`
- Dual CV track: JD keyword detection authoritative; technical keywords hardwired
- Portfolio injection on technical track
- 5-file output per job: `cv_tailored.pdf`, `cover_letter.txt`, `advice.txt`, `job_link.txt`, `score_summary.txt`
- `StrategicScore` Pydantic model: 8 fields including red_flags/green_flags
- Banned phrase retry up to `MAX_DOCUMENT_RETRIES=2`

#### Step 4 — MAIN PIPELINE (main.py + filters.py + db.py + config.yaml)
- **filters.py:** removed `engineer|developer|software|devops|data scientist` from `_HARD_EXCLUDE_PATTERNS`
- **db.py:** added `has_matching_role(company, role, exclude_job_id)` — case-insensitive, whitespace-normalised cross-source dedup using `re.sub(r'\s+', ' ', s.strip()).lower()` on Python side + `LOWER(TRIM(...))` in SQL
- **main.py:** complete `_run_pipeline()` rewrite — new flow `SCRAPE→DEDUP→PRE-FILTER→KPI→STRATEGIC→LOG`; all old JobState refs replaced; `JobPackager` replaces `ApplicationWriter`+`CVCompiler`; `send_daily_digest` wired to fire when `packaged > 0`
- **config.yaml:** `pipeline_every_hours: 12 → 6`; dead `submission` block removed

#### Step 5 — SHEETS (logging_/sheets.py rewritten)
- Schema: 14 columns → `[Date, Company, Role, Score, URL, Local_Folder_Path, Status]` (7 columns)
- `status` defaults to `"Pending Manual Submission"` when key absent
- Removed: `_score_breakdown`, `_key_gaps`, `_cl_excerpt` helpers; `json` import

#### Step 6 — TELEGRAM (utils/notify.py)
- Added `send_daily_digest(jobs_packaged_count, top_jobs, sheet_url)` — audible alert with ranked job list + Sheet link
- No submission-related code was present to remove (already cleaned in Step 1)

#### Step 7 — CLEANUP
- All modified files syntax-checked (7 files — all OK)
- Zero unused imports in main.py (verified)
- Dead `submission` config block removed from config.yaml
- Stale `PREFILTERED` comment updated in config.yaml

---

## HANDLER VERIFICATION STATUS

| Component | Status | Notes |
|-----------|--------|-------|
| `totaljobs.py` scraper | VERIFIED (smoke test) | 5 jobs, full JD — prior session |
| `reed.py` scraper | PREVIOUSLY VERIFIED | Prior sessions |
| `efinancialcareers.py` scraper | PREVIOUSLY VERIFIED | Prior sessions |
| `generation/packager.py` | SYNTAX + LOGIC VERIFIED | Assertions pass; no live API run yet |
| LinkedIn session | UNVERIFIED | Needs fresh login confirmation |

---

## IN PROGRESS

Nothing in progress — Step 7 complete. Pipeline pivot is architecturally done.

---

## PRIORITY QUEUE (next session, in order)

### P1 — e2e_validate.py update
`e2e_validate.py` still imports `get_discovered_jobs` and references old `JobState.SKIPPED`/`SCORE_FAILED`/`COMPILED` states from the pre-pivot version. Must be updated to match new 3-state pipeline before any test run:
- Replace `get_discovered_jobs` → `get_found_jobs`
- Replace `transition(job_id, JobState.SKIPPED, ...)` → `transition(job_id, JobState.FOUND, score=-1.0, ...)`
- Remove `submit_dry_run` step (already removed) and `compile_cvs` step — replace with `package_job_assets()` call
- Update summary table columns to match new 7-column Sheets schema

### P2 — Confirm LinkedIn Session (needed for live run)
```bash
PYTHONPATH="." python test_browser.py
```
- Complete full login — DO NOT press Enter until profile picture is visible in feed
- Expected: `li_at found after N s` + `LinkedIn session ready`

### P3 — Top up API credits + first live DRY_RUN
- Confirm Anthropic API credits available
- Run: `PYTHONPATH="." python e2e_validate.py` (after P1 fix)
- Confirm: scoring, generation, PDF compile all execute; output/jobs/ populated

### P4 — Pre-Live Checklist (when DRY_RUN confirmed)
- [ ] e2e_validate.py runs cleanly end-to-end
- [ ] LinkedIn session valid and returning > 0 results
- [ ] Anthropic API credits available and sufficient
- [ ] Proxy reachable (port 30001+ responding)
- [ ] Telegram notifications confirmed working
- [ ] Google Sheets logging confirmed writing
- [ ] DRY_RUN=false set manually by human in .env

---

## KNOWN BUGS / BLOCKERS

| Bug | Severity | Status |
|-----|----------|--------|
| `e2e_validate.py` uses old state machine (get_discovered_jobs, SKIPPED, COMPILED) | HIGH | Must fix before any test run — see P1 |
| LinkedIn `li_at` not confirmed fresh | HIGH | Fix implemented in test_browser.py; needs successful re-run |
| `cv_technical.pdf` and `cv_marketing.pdf` may not exist in assets/ | MEDIUM | Startup validation will warn; pipeline falls back to base CV copy |

---

## FILES MODIFIED THIS SESSION (2026-03-27 pivot)

- `submission/` — DELETED (entire folder, 11 files)
- `tests/test_submission_dry_run.py` — DELETED
- `test_workday.py` — DELETED
- `cache/db.py` — REWRITTEN (3-state machine; added has_matching_role)
- `generation/packager.py` — NEW FILE
- `main.py` — REWRITTEN (_run_pipeline; imports; docstring)
- `scoring/filters.py` — engineer|developer|software|devops|data scientist removed from hard excludes
- `logging_/sheets.py` — REWRITTEN (7-column schema)
- `utils/notify.py` — send_daily_digest added
- `config.yaml` — pipeline_every_hours 12→6; submission block removed

---

## SESSION NOTES

**Next session opening steps (copy-paste ready):**

1. Read CLAUDE.md and MASTER_STATUS.md (silent)
2. Fix `e2e_validate.py` — update to new 3-state pipeline (P1 above) — NO API credits needed
3. Confirm LinkedIn session: `PYTHONPATH="." python test_browser.py`
4. Once e2e_validate.py fixed: `PYTHONPATH="." python e2e_validate.py`
5. Mode: BUILD

**Context for next session:**
- The submission layer is gone permanently. The bot is now a lead gen + doc automation tool.
- Output goes to `output/jobs/[Company]_[Role]/` — 5 files per job — for manual review.
- The Sheets log is the review dashboard: Date, Company, Role, Score, URL, Local_Folder_Path, Status.
- `e2e_validate.py` is the only file with stale references to the old state machine. Fix it before any test run.
- Do NOT suggest re-adding submission logic. This is a deliberate architectural decision.

---

[Older items archived to HISTORY.md as needed]
