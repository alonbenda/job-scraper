"""
db.py - Supabase database operations for Job Scraper.
Replaces seen_jobs.json and config.json URLs with Supabase tables.

Environment variables required:
    SUPABASE_URL - your Supabase project URL
    SUPABASE_KEY - your Supabase secret key
"""

import os
import logging
import requests
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

NEW_BADGE_GRACE = timedelta(minutes=2)  # jobs stay NEW for this long after being found


def _headers(prefer: str = "") -> dict:
    """Build request headers for Supabase REST API."""
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def is_configured() -> bool:
    """Check if Supabase credentials are set."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Sites operations
# ---------------------------------------------------------------------------

def get_all_sites() -> list[dict]:
    """Fetch all sites from the database."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/sites",
            headers=_headers(),
            params={"select": "name,url,source", "order": "name.asc", "limit": 1000},
        )
        resp.raise_for_status()
        rows = resp.json()
        # Convert to the format expected by scrape_site: [{"url": ..., "name": ..., "discovered": ...}]
        return [
            {"url": r["url"], "name": r["name"], "discovered": r.get("source") == "discovered"}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"DB: Failed to fetch sites: {e}")
        return []


def add_discovered_site(name: str, url: str):
    """Insert (or update) a site discovered via general Google search."""
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/sites",
            headers=_headers(prefer="resolution=merge-duplicates"),
            json={"name": name, "url": url, "source": "discovered"},
        )
    except Exception:
        pass  # Non-critical, don't crash the scraper


def get_search_queries() -> list[dict]:
    """Fetch all general-search queries from the database."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/search_queries",
            headers=_headers(),
            params={"select": "name,query", "order": "name.asc", "limit": 1000},
        )
        resp.raise_for_status()
        rows = resp.json()
        return [{"name": r["name"], "query": r["query"]} for r in rows]
    except Exception as e:
        logger.error(f"DB: Failed to fetch search queries: {e}")
        return []


def update_site_status(name: str, status: str, job_count: int = 0):
    """Update a site's last_scraped timestamp and status."""
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/sites",
            headers=_headers(),
            params={"name": f"eq.{name}"},
            json={
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "last_status": status,
                "job_count": job_count,
            },
        )
    except Exception:
        pass  # Non-critical, don't crash the scraper


# ---------------------------------------------------------------------------
# Jobs operations
# ---------------------------------------------------------------------------

def get_seen_job_ids() -> set:
    """Fetch all seen job IDs from the database."""
    try:
        all_ids = set()
        offset = 0
        batch_size = 1000
        while True:
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/jobs",
                headers=_headers(),
                params={
                    "select": "id",
                    "limit": batch_size,
                    "offset": offset,
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            for r in rows:
                all_ids.add(r["id"])
            if len(rows) < batch_size:
                break
            offset += batch_size
        return all_ids
    except Exception as e:
        logger.error(f"DB: Failed to fetch seen jobs: {e}")
        return set()


def save_new_jobs(jobs: list[dict]):
    """Insert new jobs into the database."""
    if not jobs:
        return
    try:
        rows = []
        for job in jobs:
            rows.append({
                "id": job["id"],
                "title": job.get("title", ""),
                "company": job.get("site", ""),
                "link": job.get("link", ""),
                "location": job.get("location", ""),
                "posted_date": job.get("posted_date", ""),
                "fit_score": job.get("fit_score", -1),
                "matched_skills": job.get("matched_skills", []),
                "missing_skills": job.get("missing_skills", []),
                "matched_roles": job.get("matched_roles", []),
                "matched_fields": job.get("matched_fields", []),
                "notified": True,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
            })

        # Batch insert
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            resp = requests.post(
                f"{SUPABASE_URL}/rest/v1/jobs",
                headers=_headers(prefer="resolution=merge-duplicates"),
                json=batch,
            )
            if resp.status_code not in (200, 201):
                logger.error(f"DB: Failed to save jobs: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"DB: Failed to save jobs: {e}")


def update_last_seen(job_ids: list[str]):
    """Update last_seen timestamp for existing jobs."""
    if not job_ids:
        return
    try:
        # Process in batches
        batch_size = 100
        now = datetime.now(timezone.utc).isoformat()
        for i in range(0, len(job_ids), batch_size):
            batch = job_ids[i:i + batch_size]
            ids_filter = ",".join(batch)
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/jobs",
                headers=_headers(),
                params={"id": f"in.({ids_filter})"},
                json={"last_seen": now},
            )
    except Exception:
        pass  # Non-critical


def delete_stale_jobs(keep_ids: set, max_age_days: int = 30):
    """Delete jobs not seen in recent scans and older than max_age_days."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/jobs",
            headers=_headers(),
            params={
                "select": "id,last_seen",
                "limit": 5000,
            },
        )
        resp.raise_for_status()
        rows = resp.json()

        to_delete = []
        for r in rows:
            if r["id"] not in keep_ids:
                try:
                    last_seen = datetime.fromisoformat(r["last_seen"].replace("Z", "+00:00"))
                    if last_seen.tzinfo is None:
                        last_seen = last_seen.replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - last_seen).days
                    if age_days > max_age_days:
                        to_delete.append(r["id"])
                except Exception:
                    pass

        if to_delete:
            ids_filter = ",".join(to_delete)
            requests.delete(
                f"{SUPABASE_URL}/rest/v1/jobs",
                headers=_headers(),
                params={"id": f"in.({ids_filter})"},
            )
            logger.info(f"DB: Cleaned up {len(to_delete)} stale jobs")

    except Exception as e:
        logger.error(f"DB: Failed to clean stale jobs: {e}")


# ---------------------------------------------------------------------------
# Dashboard operations
# ---------------------------------------------------------------------------

def get_jobs(limit: int = 300) -> list[dict]:
    """Fetch jobs for the dashboard, newest first."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/jobs",
            headers=_headers(),
            params={"select": "*", "order": "first_seen.desc", "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"DB: Failed to fetch jobs: {e}")
        return []


def get_sites_status() -> list[dict]:
    """Fetch site statuses for the dashboard."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/sites",
            headers=_headers(),
            params={
                "select": "name,url,source,last_scraped,last_status,job_count",
                "order": "name.asc",
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"DB: Failed to fetch site statuses: {e}")
        return []


def get_last_viewed() -> str | None:
    """Fetch the timestamp the dashboard was last viewed."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/ui_state",
            headers=_headers(),
            params={"select": "last_viewed_at", "id": "eq.1"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0]["last_viewed_at"] if rows else None
    except Exception as e:
        logger.error(f"DB: Failed to fetch last_viewed_at: {e}")
        return None


def set_last_viewed():
    """
    Mark the dashboard as viewed. Anchored to (now - NEW_BADGE_GRACE), not to
    when any one tab/session started — a job stays NEW for that long after
    being found, consistently across however many tabs are open, instead of
    every refresh resetting the clock for all of them.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - NEW_BADGE_GRACE).isoformat()
        requests.post(
            f"{SUPABASE_URL}/rest/v1/ui_state",
            headers=_headers(prefer="resolution=merge-duplicates"),
            json={"id": 1, "last_viewed_at": cutoff},
        )
    except Exception:
        pass  # Non-critical, don't crash the dashboard


def mark_job_clicked(job_id: str):
    """Record that the user clicked through to a job's listing."""
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/jobs",
            headers=_headers(),
            params={"id": f"eq.{job_id}"},
            json={"clicked_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        pass  # Non-critical, don't crash the dashboard
