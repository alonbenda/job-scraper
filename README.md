# 🔍 Job Scraper v2

Monitors **90+ career pages** for job listings matching your keywords in Israel. Results land in Supabase and are viewed through a local dashboard — email is available but off by default.

## Features

- **90+ company career sites** pre-configured (Israeli tech companies + global companies with Israel offices), plus general Google-search discovery for new sites
- **Dual scraping strategy**: fast HTTP fetch for static sites, Selenium for JS-rendered pages
- **Keyword filtering**: matches job titles against your keywords (student, intern, software, engineer)
- **Israel location filter**: only shows jobs in Israeli locations
- **Local dashboard**: browse new jobs, site status, and filter/sort — instead of an email per scan
- **Global dashboard** (`web/index.html`, deployed on Netlify): the same view, reachable from any device — see "Global dashboard" below
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

## Global dashboard (Netlify + Supabase Auth)

`web/index.html` is a self-contained static page that talks to Supabase **directly from the browser** — no backend, so it deploys to Netlify's static hosting and works from any device (PC, phone, anywhere). It uses the public **anon key** (not the secret key `db.py` uses), gated by Supabase Auth (a real login) and Row Level Security policies — the anon key alone can't read anything without a valid login.

One-time setup:
1. In the Supabase SQL editor, run the RLS policy SQL (enables login-gated read access to `jobs`/`sites`/`ui_state` — the scraper's secret key bypasses RLS entirely, so this doesn't affect scraping).
2. Authentication → Users → Add User, to create your login (email + password) — done in the Supabase dashboard, not committed anywhere.
3. Paste the anon key (Project Settings → API) into `SUPABASE_ANON_KEY` in `web/index.html`.
4. Connect this repo to Netlify (`publish = "web"`, already set in `netlify.toml`, no build command) — auto-deploys on every push to `main`.

### Junior detection without "junior" in the title

Jobs with a field keyword but no role keyword in the title get their description fetched and classified by ordered rules (`classify_by_description` in `job_scraper.py`): **reject** (3+ years, lead/principal/staff…) → **junior** (0–2 years, entry level, graduate…) → otherwise **maybe** ("Not sure" in the dashboard and email). Rejected jobs are stored as hidden rows (`category='rejected'`, `dismissed=true`) so they aren't fetched again, and are cleaned up with the other stale jobs; at most `MAX_DESC_FETCHES_PER_RUN` descriptions are fetched per run.

The ✕ button on a dashboard row hides the job (`dismissed = true`, a soft delete so the scraper doesn't re-add it). Run once in the Supabase SQL editor:

```sql
alter table jobs add column if not exists category text;
alter table jobs add column if not exists dismissed boolean not null default false;
-- the Netlify page needs UPDATE on jobs for the logged-in user (it already PATCHes clicked_at)
```

Until the columns exist, the scraper and dashboards fall back to the old behaviour (no category, no dismissing).

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
| `templates/dashboard.html` | Local dashboard UI |
| `web/index.html` | Global dashboard — static, talks to Supabase directly, deployed on Netlify |
| `netlify.toml` | Netlify deploy config (`publish = "web"`) |
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
