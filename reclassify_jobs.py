"""
reclassify_jobs.py - one-off cleanup for jobs already stored without a category.

Jobs saved before description-based classification existed (or before the
`category` / `dismissed` columns were added) have no category and no role match,
so the dashboard shows them as "Junior". This re-checks each one:

  1. title contains an exclude keyword (senior, lead, leader, ...)  -> hidden
  2. description says 3+ years / lead-level                           -> hidden
  3. description says 0-2 years / entry level / graduate             -> 'junior'
  4. otherwise                                                       -> 'maybe'

Hidden = category 'rejected' + dismissed=true (same as the scraper does), so it
is not re-added on the next run.

Dry run by default — prints what would change. Pass --apply to write.

    python reclassify_jobs.py            # preview
    python reclassify_jobs.py --apply    # update the database
    python reclassify_jobs.py --recheck-maybe   # preview, including jobs already marked 'maybe'
"""

import argparse
import logging
import time
from collections import defaultdict

import db
import job_scraper as js
from api_fetchers import fetch_via_api

logging.basicConfig(level=logging.WARNING)


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _api_jobs(company: str, sites_by_name: dict) -> dict:
    """{link: job, normalized title: job} for the company's live jobs from its ATS API
    (jobs carry either a ready 'description' or a 'desc_ref' to fetch it on demand)."""
    entry = sites_by_name.get(company)
    if not entry:
        return {}
    try:
        jobs = fetch_via_api(entry["url"], company, entry.get("api")) or []
    except Exception as e:
        print(f"  (API fetch failed for {company}: {e})")
        return {}
    found = {}
    for j in jobs:
        if j.get("description") or j.get("desc_ref"):
            found[j.get("link", "")] = j
            found.setdefault(_norm(j.get("title", "")), j)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: preview only)")
    parser.add_argument("--recheck-maybe", action="store_true",
                        help="also re-check jobs already marked 'maybe' (e.g. after descriptions became available)")
    parser.add_argument("--company", help="only check jobs of this company (exact name)")
    args = parser.parse_args()

    if not db.is_configured():
        raise SystemExit("Supabase isn't configured (SUPABASE_URL / SUPABASE_KEY) — nothing to reclassify.")

    config = js.load_config()
    exclude_keywords = config.get("exclude_keywords", [])
    sites_by_name = {s["name"]: s for s in db.get_all_sites()}

    rows = db.get_unclassified_jobs(include_maybe=args.recheck_maybe)
    if args.company:
        rows = [r for r in rows if r.get("company") == args.company]
    print(f"{len(rows)} job(s) to check" + (" (no category, or 'maybe')" if args.recheck_maybe else " (no category)") + ".\n")
    by_company = defaultdict(list)
    for r in rows:
        by_company[r.get("company") or ""].append(r)

    plan = []  # (row, verdict, reason)
    for company, jobs in sorted(by_company.items()):
        api_jobs = _api_jobs(company, sites_by_name)
        for r in jobs:
            title = r.get("title", "")
            excluded = js.matches_exclude(title, exclude_keywords)
            if excluded:
                plan.append((r, "reject", f"title has exclude keyword {excluded}"))
                continue
            api_job = api_jobs.get(r.get("link", "")) or api_jobs.get(_norm(title))
            if not api_job and not js._looks_like_job_link(r.get("link", "")):
                plan.append((r, "reject", "link doesn't look like a job page (scraped heading/product/blog?)"))
                continue
            desc =js.fetch_job_description(api_job or {"link": r.get("link", "")}, config)
            if not (api_job and api_job.get("description")):
                time.sleep(0.3)  # a network fetch was made — be polite
            verdict = js.classify_by_description(desc, title)
            plan.append((r, verdict, "description" if desc else "no readable description"))

    counts = defaultdict(int)
    for r, verdict, reason in plan:
        counts[verdict] += 1
        print(f"  {verdict:7}  {r.get('company', '')[:22]:22}  {r.get('title', '')[:50]:50}  ({reason})")
    print(f"\nSummary: {dict(counts)}")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to update the database.")
        return

    failed = 0
    for r, verdict, _ in plan:
        if verdict == "reject":
            fields = {"category": "rejected", "dismissed": True}
        else:
            fields = {"category": verdict}
        if not db.update_job(r["id"], fields):
            failed += 1
    print(f"\nUpdated {len(plan) - failed} job(s)" + (f", {failed} failed." if failed else "."))


if __name__ == "__main__":
    main()
