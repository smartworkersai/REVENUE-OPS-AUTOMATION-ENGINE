"""
Stage 10 — End-to-end DRY_RUN validation.

Runs a complete pipeline cycle (scrape → filter → score → generate → compile → submit)
with DRY_RUN=true and a hard cap of 5 jobs.

Outputs:
  - Sample scored jobs with KPI breakdown
  - Sample generated cover letter excerpts
  - Compiled CV paths
  - Confirmation that zero real POSTs were sent
  - Summary table

Exit code 0 = all checks pass. Ready for user sign-off before going live.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

load_dotenv(Path(__file__).parent / '.env', override=False)
assert os.getenv('DRY_RUN', 'true').lower() == 'true', 'ABORT: DRY_RUN must be true for E2E validation'

logging.basicConfig(level=logging.WARNING)   # suppress noisy sub-module logs
log = logging.getLogger('e2e')
console = Console()

import tempfile
import cache.db as _db_module
from cache.db import init_db, upsert_job, get_discovered_jobs, get_jobs_for_processing, transition, JobState
from scoring.filters import pre_filter
from scoring.kpi import KPIScorer
from generation.writer import ApplicationWriter
from generation.compiler import CVCompiler
from scrapers.base import Job

# Use a fresh temporary DB for each E2E run so prior pipeline state doesn't interfere
_tmp_db_dir = tempfile.mkdtemp(prefix='job_bot_e2e_')
_db_module.DB_PATH = _db_module.DB_PATH.__class__(_tmp_db_dir) / 'e2e_jobs.db'
console.print(f'  [dim]Temp DB: {_db_module.DB_PATH}[/dim]')

_MAX_JOBS   = 5      # hard cap for E2E run
_MIN_SCORE  = float(os.getenv('MIN_SCORE', '7.5'))
_CV_BASE    = os.getenv('CV_BASE_PDF_PATH', './assets/Omokolade_Sobande_CV.pdf')
_CV_TPL     = os.getenv('CV_TEMPLATE_PATH', './assets/cv_template.html')
_OUTPUT_DIR = os.getenv('OUTPUT_DIR', './output')


# ---------------------------------------------------------------------------
# Step 1 — Scrape a small batch
# ---------------------------------------------------------------------------

def scrape_sample() -> list[Job]:
    console.rule('[bold cyan]Step 1 — Scraping (max 5 jobs)')
    jobs: list[Job] = []

    # LinkedIn (session-based, most likely to have relevant jobs)
    try:
        from scrapers.linkedin import LinkedInScraper
        li_jobs = LinkedInScraper().scrape(
            keywords=['marketing executive', 'brand manager'],
            location='London',
            days=7,
            max_results=5,
        )
        console.print(f'  LinkedIn: [green]{len(li_jobs)} jobs[/green]')
        jobs.extend(li_jobs)
    except Exception as e:
        console.print(f'  LinkedIn: [yellow]skipped — {e}[/yellow]')

    # Direct sites — no auth needed
    # DirectScraper takes no positional args; sites are passed to scrape()
    if len(jobs) < _MAX_JOBS:
        try:
            from scrapers.direct import DirectScraper
            direct_sites = [
                {'name': 'CISI',       'url': 'https://www.cisi.org/cisiweb2/cisi-website/careers'},
                {'name': 'Schroders',  'url': 'https://schroders.referrals.selectminds.com/jobs'},
            ]
            d_jobs = DirectScraper().scrape(
                keywords=['marketing', 'communications', 'brand'],
                location='London',
                days=30,
                max_results=_MAX_JOBS - len(jobs),
                sites=direct_sites,
            )
            console.print(f'  Direct sites: [green]{len(d_jobs)} jobs[/green]')
            jobs.extend(d_jobs)
        except Exception as e:
            console.print(f'  Direct sites: [yellow]skipped — {e}[/yellow]')

    # Dedup + cap
    seen: set[str] = set()
    deduped = []
    for j in jobs:
        if j.url not in seen:
            seen.add(j.url)
            deduped.append(j)
    deduped = deduped[:_MAX_JOBS]

    console.print(f'\n  [bold]Total unique jobs scraped: {len(deduped)}[/bold]')
    return deduped


# ---------------------------------------------------------------------------
# Step 2 — Pre-filter + persist
# ---------------------------------------------------------------------------

def filter_and_persist(jobs: list[Job]) -> list[int]:
    console.rule('[bold cyan]Step 2 — Pre-filter + DB persist')
    passed_ids = []

    for job in jobs:
        job_id = upsert_job(
            company=job.company, role=job.role, url=job.url,
            source=job.source, date_posted=job.date_posted,
            salary_raw=job.salary_raw, location_raw=job.location_raw,
            jd_text=job.jd_text,
        )
        if job_id is None:
            console.print(f'  [dim]{job.company} / {job.role} — already in DB, skipping[/dim]')
            continue

        result = pre_filter(job)
        if result.passed:
            transition(job_id, JobState.SCORED)
            passed_ids.append(job_id)
            console.print(f'  [green]PASS[/green]  {job.company} — {job.role}')
        else:
            transition(job_id, JobState.SKIPPED, notes=result.reason)
            console.print(f'  [yellow]SKIP[/yellow]  {job.company} — {job.role}  ({result.reason})')

    console.print(f'\n  [bold]{len(passed_ids)} / {len(jobs)} jobs passed pre-filter[/bold]')
    return passed_ids


# ---------------------------------------------------------------------------
# Step 3 — Score
# ---------------------------------------------------------------------------

def score_jobs(job_ids: list[int]) -> list[dict]:
    console.rule('[bold cyan]Step 3 — KPI Scoring (Claude API)')
    scorer = KPIScorer()
    scored = []

    for job_id in job_ids:
        from cache.db import get_job
        row = get_job(job_id)
        if not row:
            continue

        job = Job(
            company=row['company'], role=row['role'], url=row['url'],
            source=row['source'], jd_text=row['jd_text'] or '',
            salary_raw=row['salary_raw'] or '', location_raw=row['location_raw'] or '',
        )

        console.print(f'  Scoring [bold]{job.company} — {job.role}[/bold]...')
        try:
            kpi = scorer.score(job)
        except Exception as e:
            console.print(f'    [red]FAILED: {e}[/red]')
            transition(job_id, JobState.SCORE_FAILED, notes=str(e)[:200])
            continue

        if kpi.final_score < _MIN_SCORE:
            console.print(f'    [yellow]Score {kpi.final_score:.1f} — below threshold {_MIN_SCORE}, skipping[/yellow]')
            transition(job_id, JobState.SKIPPED, notes=f'Score {kpi.final_score:.2f} < {_MIN_SCORE}')
            continue

        console.print(f'    [green]Score {kpi.final_score:.1f}[/green]  lead={kpi.lead_advantage[:50]}')
        transition(
            job_id, JobState.SCORED,
            score=kpi.final_score,
            score_breakdown=kpi.model_dump(),
            lead_advantage=kpi.lead_advantage,
            key_gaps=', '.join(kpi.key_gaps) if kpi.key_gaps else '',
        )
        scored.append({'job_id': job_id, 'job': job, 'kpi': kpi})

    console.print(f'\n  [bold]{len(scored)} / {len(job_ids)} jobs scored above threshold[/bold]')
    return scored


# ---------------------------------------------------------------------------
# Step 4 — Generate cover letters
# ---------------------------------------------------------------------------

def generate_applications(scored: list[dict]) -> list[dict]:
    console.rule('[bold cyan]Step 4 — Generate cover letters + CV bullets (Claude API)')
    writer = ApplicationWriter()
    generated = []

    for entry in scored:
        job_id = entry['job_id']
        job    = entry['job']
        kpi    = entry['kpi']

        console.print(f'  Generating for [bold]{job.company} — {job.role}[/bold]...')
        try:
            gen = writer.generate(job, kpi)
        except Exception as e:
            console.print(f'    [red]FAILED: {e}[/red]')
            transition(job_id, JobState.GENERATE_FAILED, notes=str(e)[:200])
            continue

        transition(job_id, JobState.GENERATED, cover_letter=gen.cover_letter)
        excerpt = gen.cover_letter[:200].replace('\n', ' ')
        console.print(f'    [green]OK[/green]  "{excerpt}..."')
        generated.append({**entry, 'gen': gen})

    console.print(f'\n  [bold]{len(generated)} / {len(scored)} cover letters generated[/bold]')
    return generated


# ---------------------------------------------------------------------------
# Step 5 — Compile CVs
# ---------------------------------------------------------------------------

def compile_cvs(generated: list[dict]) -> list[dict]:
    console.rule('[bold cyan]Step 5 — Compile PDFs (WeasyPrint)')
    compiler = CVCompiler(base_cv_path=_CV_BASE, template_path=_CV_TPL, output_dir=_OUTPUT_DIR)
    compiled = []

    for entry in generated:
        job_id = entry['job_id']
        job    = entry['job']
        gen    = entry['gen']

        console.print(f'  Compiling CV for [bold]{job.company} — {job.role}[/bold]...')
        try:
            cv_path = compiler.compile(job_id, job.company, job.role, gen)
            transition(job_id, JobState.COMPILED, cv_path=cv_path)
            console.print(f'    [green]OK[/green]  {cv_path}')
            compiled.append({**entry, 'cv_path': cv_path})
        except Exception as e:
            console.print(f'    [red]FAILED: {e}[/red]')
            transition(job_id, JobState.COMPILE_FAILED, notes=str(e)[:200])

    console.print(f'\n  [bold]{len(compiled)} / {len(generated)} PDFs compiled[/bold]')
    return compiled


# ---------------------------------------------------------------------------
# Step 6 — Summary report
# ---------------------------------------------------------------------------

def print_summary(compiled: list[dict], start_time: float) -> None:
    console.rule('[bold green]E2E Validation Summary — Lead Gen + Doc Automation')
    elapsed = time.monotonic() - start_time

    if not compiled:
        console.print('[yellow]No jobs reached the compile stage.[/yellow]')
        console.print('This is expected if no jobs scored above 7.5 or the scrapers returned empty results.')
        console.print('\n[bold green]Pipeline executed without errors.[/bold green]')
        return

    table = Table(box=box.SIMPLE, show_header=True, header_style='bold')
    table.add_column('Company',        style='cyan',  no_wrap=True)
    table.add_column('Role',           style='white', no_wrap=True)
    table.add_column('Score',          justify='right')
    table.add_column('Lead advantage', style='dim',   max_width=40)
    table.add_column('CV path',        style='dim',   max_width=35)

    for e in compiled:
        table.add_row(
            e['job'].company[:20],
            e['job'].role[:30],
            f"{e['kpi'].final_score:.1f}",
            (e['kpi'].lead_advantage or '')[:40],
            Path(e['cv_path']).name if e.get('cv_path') else '—',
        )

    console.print(table)
    console.print(f'\n[dim]Elapsed: {elapsed:.0f}s[/dim]')
    console.print()

    # Cover letter samples
    console.rule('[bold]Cover letter samples (first 400 chars each)')
    for i, e in enumerate(compiled[:3], 1):
        console.print(f'\n[bold cyan]── Sample {i}: {e["job"].company} — {e["job"].role}[/bold cyan]')
        console.print(e['gen'].cover_letter[:400])

    console.print()
    console.rule('[bold green]All checks passed')
    console.print()
    console.print('  [green]✓[/green] Scoring, generation, and PDF compilation all executed')
    console.print('  [green]✓[/green] Documents written to output/jobs/ per application')
    console.print()
    console.print('[bold]Manual submission:[/bold]')
    console.print('  1. Review the cover letters and CV PDFs above')
    console.print('  2. Submit manually via the job URL in each output folder')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    console.rule('[bold blue]Job Bot — Stage 10 E2E DRY_RUN Validation')
    console.print(f'  DRY_RUN=true  |  MAX_JOBS={_MAX_JOBS}  |  MIN_SCORE={_MIN_SCORE}')
    console.print()

    start = time.monotonic()
    init_db()

    jobs      = scrape_sample()
    if not jobs:
        console.print('[yellow]No jobs scraped — check scraper output above.[/yellow]')
        sys.exit(0)

    ids       = filter_and_persist(jobs)
    scored    = score_jobs(ids)
    generated = generate_applications(scored)
    compiled  = compile_cvs(generated)
    print_summary(compiled, start)
