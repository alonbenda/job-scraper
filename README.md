# 🔍 Job Scraper v2

Monitors **90+ career pages** for job listings matching your keywords in Israel. Results land in Supabase and are viewed through a local dashboard — email is available but off by default.

## Features

- **90+ company career sites** pre-configured (Israeli tech companies + global companies with Israel offices), plus general Google-search discovery for new sites
- **Dual scraping strategy**: fast HTTP fetch for static sites, Selenium for JS-rendered pages
- **Keyword filtering**: matches job titles against your keywords (student, intern, software, engineer)
- **Israel location filter**: only shows jobs in Israeli locations
- **Local dashboard**: browse new jobs, site status, and filter/sort — instead of an email per scan
- **Optional email notifications**: HTML-formatted email grouped by company, off by default (`config.json` → `email.enabled`)
- **Duplicate detection**: link-based job IDs (falls back to title+location when a site's links don't discriminate between openings) so re-scans don't re-notify
- **Shared Supabase backend**: this same codebase runs both locally (Task Scheduler) and in the cloud (GitHub Actions) — both write into the same `sites`/`jobs` tables, so the dashboard shows results from either

## Quick Start

### 1. Install
```bash
pip install -r requirements.txt
```
Also need: **Google Chrome** installed.

### 2. Configure
Either run the setup wizard:
```bash
python job_scraper.py --setup
```
Or edit `config.json` directly — keywords, `location_filter`, `cv_path`, etc. `config.json` never holds secrets: Supabase (`SUPABASE_URL` / `SUPABASE_KEY`) and email (`SENDER_EMAIL` / `SENDER_PASSWORD` / `RECIPIENT_EMAIL`, only needed if `email.enabled` is `true`) are read from environment variables only. Without Supabase configured, the scraper falls back to local files (`config.json`'s `urls`, `seen_jobs.json`).

### 3. Run
```bash
python job_scraper.py              # Scan all sites
python dashboard.py                # View results at http://127.0.0.1:5057
python job_scraper.py --test-email # Verify email works (only if email.enabled)
python job_scraper.py --list       # See all configured sites
python job_scraper.py --reset      # Clear history, rescan everything
```

### 4. Schedule locally
Right-click `setup_scheduler.bat` → **Run as Administrator** (runs every 60 min). `run_scraper.bat` / `run_dashboard.bat` are double-click launchers for manual runs.

### 5. Cloud runs (GitHub Actions)
`.github/workflows/scraper.yml` runs `job_scraper.py` directly, daily via cron. Email/Supabase credentials come from GitHub Secrets as environment variables — same mechanism as a local run, no CI-specific wrapper or config mutation involved. It writes into the same Supabase project as local runs.

## Customization

Edit `config.json`:
- **role_keywords / field_keywords / exclude_keywords**: what counts as a match
- **location_filter**: `["israel", "tel aviv", "herzliya", ...]`
- **urls / general_search_queries**: fallback site list (ignored once Supabase is configured — sites and search queries then come from the DB)
- **email.enabled**: `false` by default — the dashboard is the primary way to see results. To turn it on, also set `SENDER_EMAIL` / `SENDER_PASSWORD` / `RECIPIENT_EMAIL` as environment variables (never in `config.json`)
- **headless**: `false` to watch the browser (debugging)

## Files
| File | Purpose |
|------|---------|
| `job_scraper.py` | Main scraper — scraping, filtering, job-ID assignment, email |
| `dashboard.py` | Local Flask dashboard — jobs table, site status, filters |
| `templates/dashboard.html` | Dashboard UI |
| `db.py` | Supabase REST client (sites, jobs, search queries, dashboard reads) |
| `config.json` | All non-secret settings (site list, keywords, email on/off) |
| `setup_scheduler.bat` | Windows scheduler setup |
| `run_scraper.bat` / `run_dashboard.bat` | Manual local launchers |
| `requirements.txt` | Python dependencies |
| `tests.py` | Unit tests (`pytest tests.py`) |
| `seen_jobs.json` | Auto-generated: tracks seen jobs (local-file fallback mode only) |
| `scraper.log` | Auto-generated: log file |

## Notes
- First run takes 15-30 min (scraping 90+ sites)
- Some sites (LinkedIn, NSO, Fiverr) have anti-scraping measures and may not always return results
- Set `"headless": false` in config to debug specific sites
