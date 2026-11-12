"""
job_store.py - Unified interface for persisting sites, jobs, and search
queries, backed by either Supabase or local JSON files.

Both implementations share the same 8 operations so run_scraper() makes
exactly one branching decision (which store to construct) instead of
branching on use_db throughout.
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

import db

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
SEEN_JOBS_FILE = SCRIPT_DIR / "seen_jobs.json"
LAST_CLEANUP_FILE = SCRIPT_DIR / "last_cleanup.json"


class JobStore(ABC):
    """Where sites, jobs, and search queries live — Supabase or local JSON files."""

    backend_name = "unknown backend"

    @abstractmethod
    def get_all_sites(self) -> list[dict]:
        """Return [{"url":..., "name":..., "discovered": bool}, ...]."""

    @abstractmethod
    def add_discovered_site(self, name: str, url: str):
        """Persist a site found via general Google search."""

    @abstractmethod
    def get_search_queries(self) -> list[dict]:
        """Return [{"name":..., "query":...}, ...]."""

    @abstractmethod
    def update_site_status(self, name: str, status: str, job_count: int = 0) -> dict | None:
        """Record the outcome of scraping one site. Returns {"baseline", "count"}
        if the job count dropped sharply versus its recent history, else None."""

    @abstractmethod
    def get_seen_job_ids(self) -> set:
        """Return the set of job IDs already known from previous scans."""

    @abstractmethod
    def save_new_jobs(self, jobs: list[dict]):
        """Persist newly-found jobs (each already has 'id' set)."""

    @abstractmethod
    def update_last_seen(self, job_ids: list[str]):
        """Bump last_seen for jobs that showed up again this scan."""

    @abstractmethod
    def delete_stale_jobs(self, keep_ids: set):
        """Purge jobs no longer found on any site."""


class SupabaseJobStore(JobStore):
    """Thin adapter over db.py's Supabase REST functions — db.py itself is unchanged."""

    backend_name = "📦 Using Supabase database"

    def get_all_sites(self):
        return db.get_all_sites()

    def add_discovered_site(self, name, url):
        db.add_discovered_site(name, url)

    def get_search_queries(self):
        return db.get_search_queries()

    def update_site_status(self, name, status, job_count=0):
        return db.update_site_status(name, status, job_count)

    def get_seen_job_ids(self):
        return db.get_seen_job_ids()

    def save_new_jobs(self, jobs):
        db.save_new_jobs(jobs)

    def update_last_seen(self, job_ids):
        db.update_last_seen(job_ids)

    def delete_stale_jobs(self, keep_ids):
        db.delete_stale_jobs(keep_ids)


class LocalFileJobStore(JobStore):
    """
    Local-file backend: sites/search-queries live in config.json, seen jobs
    in seen_jobs.json. Used when Supabase isn't configured.
    """

    backend_name = "📁 Using local files"

    def __init__(self, config: dict):
        self._config = config
        self._seen_jobs = self._load_seen_jobs()

    # -- sites --

    def get_all_sites(self):
        return [
            {**u, "discovered": u.get("source") == "discovered"}
            for u in self._config.get("urls", [])
        ]

    def add_discovered_site(self, name, url):
        self._config.setdefault("urls", []).append(
            {"url": url, "name": name, "source": "discovered"}
        )
        self._save_config()

    def get_search_queries(self):
        return self._config.get("general_search_queries", [])

    def update_site_status(self, name, status, job_count=0):
        return None  # Local-file mode has never tracked per-site history

    # -- jobs --

    def get_seen_job_ids(self):
        return set(self._seen_jobs.keys())

    def save_new_jobs(self, jobs):
        now = datetime.now().isoformat()
        for job in jobs:
            jid = job["id"]
            if jid not in self._seen_jobs:
                self._seen_jobs[jid] = {
                    "title": job["title"],
                    "site": job["site"],
                    "link": job.get("link", ""),
                    "location": job.get("location", ""),
                    "new_company": job.get("new_company", False),
                    "first_seen": now,
                    "last_seen": now,
                }
        self._save_seen_jobs()

    def update_last_seen(self, job_ids):
        now = datetime.now().isoformat()
        for jid in job_ids:
            if jid in self._seen_jobs:
                self._seen_jobs[jid]["last_seen"] = now
        self._save_seen_jobs()

    def delete_stale_jobs(self, keep_ids):
        """
        Remove jobs from seen_jobs that are no longer found on any career page.
        Only runs once per day (controlled by cleanup_interval_hours), and only
        if cleanup_stale_jobs is enabled. This prevents re-notifying about jobs
        that reappear, while keeping memory clean of jobs that have been
        filled/removed.
        """
        if not self._config.get("cleanup_stale_jobs", True):
            return

        interval = self._config.get("cleanup_interval_hours", 24)
        if not self._should_run_cleanup(interval):
            return

        logger.info("🧹 Running daily stale job cleanup...")

        if not keep_ids:
            logger.info("  No live jobs found this scan — skipping cleanup to avoid false purge.")
            self._save_cleanup_timestamp()
            return

        stale_ids = [jid for jid in self._seen_jobs if jid not in keep_ids]
        if stale_ids:
            for jid in stale_ids:
                title = self._seen_jobs[jid].get("title", "?")
                site = self._seen_jobs[jid].get("site", "?")
                logger.info(f"  🗑️ Removing stale: {title} @ {site}")
                del self._seen_jobs[jid]
            self._save_seen_jobs()
            logger.info(f"  Cleaned up {len(stale_ids)} stale job(s). {len(self._seen_jobs)} remaining.")
        else:
            logger.info("  No stale jobs to clean up.")

        self._save_cleanup_timestamp()

    # -- local file plumbing --

    def _load_seen_jobs(self) -> dict:
        if SEEN_JOBS_FILE.exists():
            with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_seen_jobs(self):
        with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(self._seen_jobs, f, indent=2, ensure_ascii=False)

    def _save_config(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    def _should_run_cleanup(self, interval_hours: int = 24) -> bool:
        if not LAST_CLEANUP_FILE.exists():
            return True
        try:
            with open(LAST_CLEANUP_FILE, "r") as f:
                data = json.load(f)
            last_run = datetime.fromisoformat(data.get("last_cleanup", "2000-01-01"))
            hours_since = (datetime.now() - last_run).total_seconds() / 3600
            return hours_since >= interval_hours
        except Exception:
            return True

    def _save_cleanup_timestamp(self):
        with open(LAST_CLEANUP_FILE, "w") as f:
            json.dump({"last_cleanup": datetime.now().isoformat()}, f)


def get_store(config: dict) -> JobStore:
    """Pick Supabase or local-file storage based on whether Supabase is configured."""
    if db.is_configured():
        return SupabaseJobStore()
    return LocalFileJobStore(config)
