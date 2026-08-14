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
from datetime import datetime

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


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
                "last_scraped": datetime.now().isoformat(),
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


def migrate_job_ids(pairs: list):
    """
    Rewrite existing rows from the legacy (site+title) ID to the new one,
    preserving first_seen and the rest of the row.

    Only fires on the first scans after the ID scheme changed, so one request
    per row is fine. Delete this along with legacy_job_id() once it stays idle.
    """
    if not pairs:
        return
    for old_id, new_id in pairs:
        try:
            resp = requests.patch(
                f"{SUPABASE_URL}/rest/v1/jobs",
                headers=_headers(),
                params={"id": f"eq.{old_id}"},
                json={"id": new_id},
            )
            if resp.status_code >= 400:
                # A row already exists under the new ID — drop the legacy one
                requests.delete(
                    f"{SUPABASE_URL}/rest/v1/jobs",
                    headers=_headers(),
                    params={"id": f"eq.{old_id}"},
                )
        except Exception as e:
            logger.error(f"DB: Failed to migrate job ID {old_id}: {e}")


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
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
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
        now = datetime.now().isoformat()
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
        cutoff = datetime.now().isoformat()
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
                    last_seen = datetime.fromisoformat(r["last_seen"].replace("Z", "+00:00").replace("+00:00", ""))
                    age_days = (datetime.now() - last_seen).days
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
