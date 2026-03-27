"""
Weekly self-improvement loop.

Three tasks per spec (Section 4):

1. Selector evolution
   - Read selector_cache from SQLite
   - Log top performers per domain (success_rate > 0.7)
   - Prune dead selectors (success_rate < 0.1 AND total attempts >= 10)
   - No rewrite needed — db.py handles EMA updates on every submission

2. Prompt weight optimisation
   - Pull Google Sheet column N (Interview?) and column F (Lead advantage)
   - Calculate interview conversion rate per lead_advantage bucket
   - Requires >= MIN_DATA_POINTS (5) interviews before any weight change
   - Write best-performing lead_advantage to config.yaml scoring.lead_advantage_boost
   - Never touch min_score or other scoring weights from here

3. Honeypot memory report
   - Count honeypot entries added since last run
   - Log a summary so the user can review new entries

Run schedule: weekly cron (see main.py)
"""

import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yaml
import gspread
from google.oauth2.service_account import Credentials

from cache.db import DB_PATH, _conn

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'
_MIN_DATA_POINTS = 5    # minimum interview confirmations before adjusting weights
_PRUNE_MIN_ATTEMPTS = 10
_PRUNE_MAX_RATE = 0.1

_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly',
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _save_config(cfg: dict) -> None:
    with open(_CONFIG_PATH, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _get_sheet() -> gspread.Worksheet:
    sa_path = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', './secrets/service_account.json')
    creds = Credentials.from_service_account_file(sa_path, scopes=_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(os.environ['GOOGLE_SHEET_ID'])
    return spreadsheet.sheet1


# ---------------------------------------------------------------------------
# Task 1 — Selector evolution
# ---------------------------------------------------------------------------

def run_selector_evolution() -> dict:
    """
    Read selector_cache, log top performers, prune dead entries.
    Returns a summary dict for reporting.
    """
    summary = {'top_selectors': [], 'pruned': 0, 'total': 0}

    with _conn() as con:
        rows = con.execute(
            "SELECT domain, field_name, selector, success_count, fail_count, success_rate "
            "FROM selector_cache ORDER BY domain, field_name, success_rate DESC"
        ).fetchall()

    summary['total'] = len(rows)

    # Log top performers
    top = [r for r in rows if r['success_rate'] >= 0.7]
    for r in top[:20]:  # cap log output
        log.info(
            'Selector OK  domain=%-30s field=%-20s rate=%.2f  selector=%s',
            r['domain'], r['field_name'], r['success_rate'], r['selector'][:60],
        )
    summary['top_selectors'] = [
        {'domain': r['domain'], 'field': r['field_name'],
         'selector': r['selector'], 'rate': r['success_rate']}
        for r in top
    ]

    # Prune dead selectors
    pruned_ids = []
    for r in rows:
        total_attempts = r['success_count'] + r['fail_count']
        if total_attempts >= _PRUNE_MIN_ATTEMPTS and r['success_rate'] < _PRUNE_MAX_RATE:
            pruned_ids.append((r['domain'], r['field_name'], r['selector']))

    if pruned_ids:
        with _conn() as con:
            for domain, field_name, selector in pruned_ids:
                con.execute(
                    "DELETE FROM selector_cache WHERE domain=? AND field_name=? AND selector=?",
                    (domain, field_name, selector),
                )
        log.info('Selector pruning: removed %d dead selectors', len(pruned_ids))
        summary['pruned'] = len(pruned_ids)

    return summary


# ---------------------------------------------------------------------------
# Task 2 — Prompt weight optimisation
# ---------------------------------------------------------------------------

def _fetch_sheet_data() -> list[dict]:
    """
    Pull all rows from the Google Sheet.
    Returns list of dicts with keys: lead_advantage, interview_outcome.
    Col F (index 5) = Lead advantage, Col N (index 13) = Interview?
    """
    try:
        sheet = _get_sheet()
        all_rows = sheet.get_all_values()
    except Exception as exc:
        log.error('learn: failed to fetch sheet data: %s', exc)
        return []

    if not all_rows:
        return []

    # Skip header row (row 0)
    data = []
    for row in all_rows[1:]:
        # Pad row if it's shorter than 14 columns
        while len(row) < 14:
            row.append('')

        lead_advantage = row[5].strip()   # col F (0-indexed: 5)
        interview_raw = row[13].strip()   # col N (0-indexed: 13)

        if not lead_advantage:
            continue

        # Normalise interview outcome — only count explicit positives
        interview = interview_raw.lower() if interview_raw else ''
        data.append({
            'lead_advantage': lead_advantage,
            'interview': interview,
        })

    return data


def _calculate_conversion_rates(data: list[dict]) -> dict[str, dict]:
    """
    Calculate interview conversion rate per lead_advantage bucket.

    Returns:
        {
          'lead_advantage_text': {
              'total': int,
              'interviews': int,
              'rate': float,
          }
        }

    Only rows with col N == 'interview' count as conversions.
    Rows with col N empty or 'none' are denominator only.
    Rows with col N == 'rejected' are denominator only.
    """
    buckets: dict[str, dict] = defaultdict(lambda: {'total': 0, 'interviews': 0})

    for row in data:
        adv = row['lead_advantage']
        buckets[adv]['total'] += 1
        if 'interview' in row['interview'].lower():
            buckets[adv]['interviews'] += 1

    result = {}
    for adv, counts in buckets.items():
        total = counts['total']
        interviews = counts['interviews']
        result[adv] = {
            'total': total,
            'interviews': interviews,
            'rate': interviews / total if total > 0 else 0.0,
        }

    return result


def run_weight_optimisation() -> dict:
    """
    Pull Sheet data, compute conversion rates, update config.yaml if sufficient data.

    Returns a summary dict for reporting.
    """
    summary = {
        'data_points': 0,
        'interview_count': 0,
        'conversion_rates': {},
        'best_lead_advantage': None,
        'config_updated': False,
        'reason': '',
    }

    data = _fetch_sheet_data()
    summary['data_points'] = len(data)
    summary['interview_count'] = sum(1 for r in data if 'interview' in r['interview'])

    if summary['interview_count'] < _MIN_DATA_POINTS:
        summary['reason'] = (
            f'Insufficient interview data: {summary["interview_count"]} interviews '
            f'(need >= {_MIN_DATA_POINTS}). Config unchanged.'
        )
        log.info('learn: %s', summary['reason'])
        return summary

    rates = _calculate_conversion_rates(data)
    summary['conversion_rates'] = {
        adv: f"{v['interviews']}/{v['total']} ({v['rate']:.0%})"
        for adv, v in rates.items()
    }

    # Find the lead_advantage with the highest conversion rate
    # Only consider buckets with at least 3 data points (avoid small-sample noise)
    qualified = {adv: v for adv, v in rates.items() if v['total'] >= 3}

    if not qualified:
        summary['reason'] = 'No lead_advantage bucket has >= 3 applications yet. Config unchanged.'
        log.info('learn: %s', summary['reason'])
        return summary

    best_adv = max(qualified, key=lambda adv: qualified[adv]['rate'])
    best_rate = qualified[best_adv]['rate']
    summary['best_lead_advantage'] = best_adv

    log.info(
        'learn: best lead_advantage = "%s" (%.0f%% conversion, n=%d)',
        best_adv, best_rate * 100, qualified[best_adv]['total'],
    )

    # Log all conversion rates
    for adv, stats in sorted(rates.items(), key=lambda x: -x[1]['rate']):
        log.info(
            'learn:   %-50s  %d/%d  (%.0f%%)',
            adv[:50], stats['interviews'], stats['total'], stats['rate'] * 100,
        )

    # Update config.yaml
    try:
        cfg = _load_config()
        old_val = cfg.get('scoring', {}).get('lead_advantage_boost')
        cfg.setdefault('scoring', {})['lead_advantage_boost'] = best_adv
        _save_config(cfg)
        summary['config_updated'] = True
        summary['reason'] = (
            f'Updated lead_advantage_boost: "{old_val}" → "{best_adv}" '
            f'(rate={best_rate:.0%}, n={qualified[best_adv]["total"]})'
        )
        log.info('learn: config.yaml updated — %s', summary['reason'])
    except Exception as exc:
        summary['reason'] = f'Config update failed: {exc}'
        log.error('learn: %s', summary['reason'])

    return summary


# ---------------------------------------------------------------------------
# Task 3 — Honeypot memory report
# ---------------------------------------------------------------------------

def run_honeypot_report(since_days: int = 7) -> dict:
    """
    Report honeypot entries added in the last N days.
    Returns a summary dict.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()

    with _conn() as con:
        total = con.execute("SELECT COUNT(*) FROM honeypot_blocklist").fetchone()[0]
        recent = con.execute(
            "SELECT domain, selector, added_at FROM honeypot_blocklist "
            "WHERE added_at >= ? ORDER BY added_at DESC",
            (cutoff,),
        ).fetchall()

    summary = {
        'total_honeypots': total,
        'new_this_week': len(recent),
        'entries': [
            {'domain': r['domain'], 'selector': r['selector'], 'added': r['added_at']}
            for r in recent
        ],
    }

    if recent:
        log.info('learn: %d new honeypot(s) added in last %d days:', len(recent), since_days)
        for r in recent:
            log.info('  %s  %s', r['domain'], r['selector'])
    else:
        log.info('learn: no new honeypots in last %d days (total=%d)', since_days, total)

    return summary


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_weekly_learn() -> dict:
    """
    Run all three learning tasks. Returns a combined summary.
    Called by main.py on the weekly cron schedule.
    """
    log.info('=== Weekly learn cycle starting ===')
    started = datetime.now(timezone.utc).isoformat()

    selector_summary = run_selector_evolution()
    weight_summary = run_weight_optimisation()
    honeypot_summary = run_honeypot_report()

    result = {
        'run_at': started,
        'selectors': selector_summary,
        'weights': weight_summary,
        'honeypots': honeypot_summary,
    }

    log.info(
        '=== Learn cycle complete: selectors=%d/%d pruned, '
        'interviews=%d, config_updated=%s, honeypots_new=%d ===',
        selector_summary['pruned'],
        selector_summary['total'],
        weight_summary['interview_count'],
        weight_summary['config_updated'],
        honeypot_summary['new_this_week'],
    )

    return result
