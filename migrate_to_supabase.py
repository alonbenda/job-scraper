"""
Migration script: uploads sites from config.json and seen_jobs from seen_jobs.json to Supabase.
Run this once to populate the database.

Usage:
    python migrate_to_supabase.py

Requires environment variables:
    SUPABASE_URL - your Supabase project URL
    SUPABASE_KEY - your Supabase secret key
"""

import json
import os
import sys
import requests
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Set SUPABASE_URL and SUPABASE_KEY environment variables first.")
    print("   Example:")
    print('   set SUPABASE_URL=https://bopkycnbkgqhycevjutn.supabase.co')
    print('   set SUPABASE_KEY=your_secret_key_here')
    sys.exit(1)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}

def supabase_upsert(table: str, rows: list[dict]) -> bool:
    """Insert or update rows into a Supabase table."""
    if not rows:
        return True
    # Supabase REST API has a limit, send in batches of 500
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            json=batch,
        )
        if resp.status_code not in (200, 201):
            print(f"  ❌ Error inserting into {table}: {resp.status_code} {resp.text}")
            return False
    return True


def migrate_sites():
    """Upload sites from config.json to Supabase."""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        print("⚠️ config.json not found, skipping sites migration.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    urls = config.get("urls", [])
    print(f"📡 Uploading {len(urls)} sites to Supabase...")

    rows = []
    for u in urls:
        rows.append({
            "name": u["name"],
            "url": u["url"],
        })

    if supabase_upsert("sites", rows):
        print(f"  ✅ {len(rows)} sites uploaded.")
    else:
        print("  ❌ Failed to upload sites.")


def migrate_seen_jobs():
    """Upload seen_jobs from seen_jobs.json to Supabase."""
    seen_path = os.path.join(os.path.dirname(__file__), "seen_jobs.json")
    if not os.path.exists(seen_path):
        print("⚠️ seen_jobs.json not found, skipping jobs migration.")
        return

    with open(seen_path, "r", encoding="utf-8") as f:
        seen = json.load(f)

    print(f"📋 Uploading {len(seen)} seen jobs to Supabase...")

    rows = []
    for job_id, job_data in seen.items():
        if isinstance(job_data, dict):
            rows.append({
                "id": job_id,
                "title": job_data.get("title", "Unknown"),
                "company": job_data.get("site", "Unknown"),
                "link": job_data.get("link", ""),
                "location": job_data.get("location", ""),
                "posted_date": job_data.get("posted_date", ""),
                "fit_score": job_data.get("fit_score", -1),
                "matched_skills": job_data.get("matched_skills", []),
                "missing_skills": job_data.get("missing_skills", []),
                "matched_roles": job_data.get("matched_roles", []),
                "matched_fields": job_data.get("matched_fields", []),
                "notified": True,  # Already seen = already notified
                "first_seen": job_data.get("first_seen", datetime.now().isoformat()),
                "last_seen": job_data.get("last_seen", datetime.now().isoformat()),
            })
        else:
            # Old format: job_data is just a timestamp string
            rows.append({
                "id": job_id,
                "title": "Unknown (migrated)",
                "company": "Unknown",
                "link": "",
                "notified": True,
                "first_seen": job_data if isinstance(job_data, str) else datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
            })

    if supabase_upsert("jobs", rows):
        print(f"  ✅ {len(rows)} jobs uploaded.")
    else:
        print("  ❌ Failed to upload jobs.")


if __name__ == "__main__":
    print("🚀 Migrating data to Supabase...")
    print(f"   URL: {SUPABASE_URL}")
    print()
    migrate_sites()
    print()
    migrate_seen_jobs()
    print()
    print("✅ Migration complete!")
