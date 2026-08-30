"""
Unit tests for Job Scraper.
Run with: python -m pytest tests.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import db
import job_store
from job_store import LocalFileJobStore

from job_scraper import (
    _whole_word_match,
    matches_role,
    matches_field,
    matches_exclude,
    matches_job,
    matches_location,
    job_id,
    canonical_link,
    assign_job_ids,
    extract_jobs_from_html,
    _is_useful_search_result,
    _build_discovered_entries,
)


# ===========================================================================
# Whole-word matching
# ===========================================================================

def test_exact_match():
    assert _whole_word_match("Software Intern", "intern") is True

def test_no_substring_internal():
    assert _whole_word_match("Internal Tools Engineer", "intern") is False

def test_no_substring_international():
    assert _whole_word_match("International Sales Manager", "intern") is False

def test_case_insensitive():
    assert _whole_word_match("SOFTWARE INTERN", "intern") is True

def test_with_punctuation():
    assert _whole_word_match("Software Intern - Summer 2026", "intern") is True

def test_with_parentheses():
    assert _whole_word_match("Software Intern (Malshab)", "intern") is True


# ===========================================================================
# Role keyword filtering
# ===========================================================================

def test_intern_role_matches():
    result = matches_role("Software Engineering Intern", ["student", "intern", "internship"])
    assert "intern" in result

def test_student_role_matches():
    result = matches_role("Backend Developer Student", ["student", "intern", "internship"])
    assert "student" in result

def test_no_role_match():
    result = matches_role("Senior Software Engineer", ["student", "intern", "internship"])
    assert result == []

def test_internship_role_matches():
    result = matches_role("Summer Internship Program", ["student", "intern", "internship"])
    assert "internship" in result


# ===========================================================================
# Field keyword filtering
# ===========================================================================

def test_software_field_matches():
    result = matches_field("Software Intern", ["software", "engineer", "developer"])
    assert "software" in result

def test_marketing_no_field_match():
    result = matches_field("Marketing Intern", ["software", "engineer", "developer"])
    assert result == []

def test_devops_field_matches():
    result = matches_field("DevOps Student", ["software", "devops", "engineer"])
    assert "devops" in result

def test_data_field_matches():
    result = matches_field("Data Science Intern", ["software", "data", "engineer"])
    assert "data" in result


# ===========================================================================
# Exclude keyword filtering
# ===========================================================================

def test_senior_excluded():
    result = matches_exclude("Senior Software Engineer Intern", ["senior", "manager", "lead"])
    assert "senior" in result

def test_manager_excluded():
    result = matches_exclude("Engineering Manager - Student Program", ["senior", "manager", "lead"])
    assert "manager" in result

def test_lead_excluded():
    result = matches_exclude("Lead QA Intern", ["senior", "manager", "lead"])
    assert "lead" in result

def test_no_exclude_match():
    result = matches_exclude("Software Engineering Intern", ["senior", "manager", "lead"])
    assert result == []

def test_director_excluded():
    result = matches_exclude("Director of Engineering", ["senior", "manager", "director", "lead"])
    assert "director" in result


# ===========================================================================
# Full job matching (role + field + exclude combined)
# ===========================================================================

def test_valid_job_matches():
    result = matches_job(
        "Software Engineering Intern",
        ["student", "intern"], ["software", "engineer"], ["senior", "manager"]
    )
    assert result is not None
    assert "intern" in result["roles"]
    assert len(result["fields"]) > 0

def test_marketing_intern_rejected():
    result = matches_job(
        "Marketing Intern",
        ["student", "intern"], ["software", "engineer", "developer"], ["senior"]
    )
    assert result is None

def test_senior_intern_rejected():
    result = matches_job(
        "Senior Software Engineer Intern",
        ["student", "intern"], ["software", "engineer"], ["senior", "manager"]
    )
    assert result is None

def test_student_developer_matches():
    result = matches_job(
        "Student - Backend Developer",
        ["student", "intern"], ["software", "developer", "backend"], ["senior"]
    )
    assert result is not None

def test_no_role_keyword_rejected():
    result = matches_job(
        "Software Engineer",
        ["student", "intern"], ["software", "engineer"], ["senior"]
    )
    assert result is None

def test_lead_qa_intern_rejected():
    result = matches_job(
        "Lead QA Intern",
        ["student", "intern"], ["qa", "software"], ["senior", "manager", "lead"]
    )
    assert result is None


# ===========================================================================
# Location filtering
# ===========================================================================

def test_tel_aviv_accepted():
    job = {"title": "Software Intern", "link": "", "location": "Tel Aviv", "site": "Wiz", "source_url": ""}
    assert matches_location(job, ["israel", "tel aviv", "herzliya"]) is True

def test_israel_in_url_accepted():
    job = {"title": "Software Intern", "link": "", "location": "", "site": "Google", "source_url": "https://careers.google.com/jobs?country=il"}
    assert matches_location(job, ["israel", "tel aviv"]) is True

def test_usa_location_rejected():
    job = {"title": "Software Intern USA - Sandy, UT", "link": "", "location": "", "site": "NICE", "source_url": ""}
    assert matches_location(job, ["israel", "tel aviv"]) is False

def test_new_york_rejected():
    job = {"title": "Software Engineer Intern - New York", "link": "", "location": "", "site": "Google", "source_url": ""}
    assert matches_location(job, ["israel", "tel aviv"]) is False

def test_london_rejected():
    job = {"title": "Software Intern - London", "link": "", "location": "", "site": "Meta", "source_url": ""}
    assert matches_location(job, ["israel", "tel aviv"]) is False

def test_unknown_location_accepted():
    job = {"title": "Software Intern", "link": "", "location": "", "site": "Startup", "source_url": ""}
    assert matches_location(job, ["israel", "tel aviv"]) is True

def test_herzliya_accepted():
    job = {"title": "Software Intern", "link": "", "location": "Herzliya", "site": "CyberArk", "source_url": ""}
    assert matches_location(job, ["israel", "tel aviv", "herzliya"]) is True

def test_berlin_rejected():
    job = {"title": "Software Intern - Berlin", "link": "", "location": "", "site": "SAP", "source_url": ""}
    assert matches_location(job, ["israel", "tel aviv"]) is False


# ===========================================================================
# Job ID generation
# ===========================================================================

def test_same_job_same_id():
    job1 = {"site": "Google", "title": "Software Intern"}
    job2 = {"site": "Google", "title": "Software Intern"}
    assert job_id(job1) == job_id(job2)

def test_different_title_different_id():
    job1 = {"site": "Google", "title": "Software Intern"}
    job2 = {"site": "Google", "title": "Data Science Intern"}
    assert job_id(job1) != job_id(job2)

def test_different_site_different_id():
    job1 = {"site": "Google", "title": "Software Intern"}
    job2 = {"site": "Meta", "title": "Software Intern"}
    assert job_id(job1) != job_id(job2)

def test_same_title_same_company_different_link_different_id():
    """Two distinct reqs with an identical title must not collide."""
    job1 = {"site": "Intel", "title": "Student Software Engineer",
            "link": "https://intel.wd1.myworkdayjobs.com/jobs/_R00123"}
    job2 = {"site": "Intel", "title": "Student Software Engineer",
            "link": "https://intel.wd1.myworkdayjobs.com/jobs/_R00456"}
    assert job_id(job1) != job_id(job2)

def test_whitespace_variation_same_id():
    job1 = {"site": "Google", "title": "Software  Intern "}
    job2 = {"site": "Google", "title": "Software Intern"}
    assert job_id(job1) == job_id(job2)


# ===========================================================================
# Link canonicalization
# ===========================================================================

def test_tracking_params_stripped():
    a = canonical_link("https://boards.greenhouse.io/acme/jobs/456?utm_source=email&gh_src=x")
    b = canonical_link("https://boards.greenhouse.io/acme/jobs/456")
    assert a == b

def test_job_id_params_preserved():
    a = canonical_link("https://il.indeed.com/viewjob?vjk=abc123")
    b = canonical_link("https://il.indeed.com/viewjob?vjk=def456")
    assert a != b

def test_www_and_trailing_slash_normalized():
    a = canonical_link("https://www.Example.com/jobs/12/")
    b = canonical_link("https://example.com/jobs/12")
    assert a == b

def test_param_order_irrelevant():
    a = canonical_link("https://example.com/j?b=2&a=1")
    b = canonical_link("https://example.com/j?a=1&b=2")
    assert a == b

def test_fragment_kept_for_hash_routing():
    a = canonical_link("https://example.com/careers#/job/123")
    b = canonical_link("https://example.com/careers#/job/456")
    assert a != b

def test_link_equal_to_source_url_is_not_identifying():
    url = "https://example.com/careers"
    assert canonical_link(url, url) == ""

def test_empty_and_non_http_links_rejected():
    assert canonical_link("") == ""
    assert canonical_link("javascript:void(0)") == ""
    assert canonical_link("mailto:jobs@example.com") == ""


# ===========================================================================
# Batch ID assignment
# ===========================================================================

def test_batch_same_link_different_titles_falls_back_to_metadata():
    """Google Jobs style: every card shares the search URL."""
    search = "https://www.google.com/search?q=jobs&udm=8"
    jobs = [
        {"site": "Wiz", "title": "Backend Engineer", "link": search, "location": "Tel Aviv"},
        {"site": "Wiz", "title": "Frontend Engineer", "link": search, "location": "Tel Aviv"},
    ]
    assign_job_ids(jobs)
    assert jobs[0]["id"] != jobs[1]["id"]

def test_batch_useless_links_still_separate_by_location():
    search = "https://www.google.com/search?q=jobs&udm=8"
    jobs = [
        {"site": "Wiz", "title": "Backend Engineer", "link": search, "location": "Tel Aviv"},
        {"site": "Wiz", "title": "Backend Engineer", "link": search, "location": "Haifa"},
        {"site": "Wiz", "title": "Data Engineer", "link": search, "location": "Haifa"},
    ]
    assign_job_ids(jobs)
    assert len({j["id"] for j in jobs}) == 3

def test_batch_same_title_same_link_collapses():
    """One anchor matched by several selectors is a real duplicate."""
    jobs = [
        {"site": "Acme", "title": "SW Intern", "link": "https://acme.com/jobs/1"},
        {"site": "Acme", "title": "SW Intern", "link": "https://acme.com/jobs/1?utm_source=x"},
    ]
    assign_job_ids(jobs)
    assert jobs[0]["id"] == jobs[1]["id"]

def test_batch_distinct_links_stay_distinct():
    jobs = [
        {"site": "Acme", "title": "SW Intern", "link": "https://acme.com/jobs/1"},
        {"site": "Acme", "title": "SW Intern", "link": "https://acme.com/jobs/2"},
    ]
    assign_job_ids(jobs)
    assert jobs[0]["id"] != jobs[1]["id"]

def test_context_jobs_expose_useless_link_when_one_job_matches():
    """A useless link must not look unique just because the filter kept one job."""
    listing = "https://acme.com/all-jobs"
    raw = [
        {"site": "Acme", "title": "SW Intern", "link": listing, "location": "Tel Aviv"},
        {"site": "Acme", "title": "VP Sales", "link": listing, "location": "Tel Aviv"},
    ]
    matching = [raw[0]]
    assign_job_ids(matching, context_jobs=raw)
    # Fell back to metadata rather than hashing the shared listing URL
    assert matching[0]["id"] == job_id({"site": "Acme", "title": "SW Intern",
                                        "location": "Tel Aviv"}, "")



# ===========================================================================
# HTML extraction
# ===========================================================================

def test_extract_from_link_tags():
    html = '<html><body><a href="/jobs/123">Software Engineering Intern</a><a href="/about">About Us</a></body></html>'
    jobs = extract_jobs_from_html(html, "TestCompany", "https://example.com")
    titles = [j["title"] for j in jobs]
    assert any("Software Engineering Intern" in t for t in titles)

def test_extract_keeps_same_title_with_different_hrefs():
    """Page-level dedup must not swallow two distinct reqs sharing a title."""
    html = ('<html><body>'
            '<a href="/jobs/123">Software Engineering Intern</a>'
            '<a href="/jobs/456">Software Engineering Intern</a>'
            '</body></html>')
    jobs = extract_jobs_from_html(html, "TestCompany", "https://example.com")
    links = {j["link"] for j in jobs if j["title"] == "Software Engineering Intern"}
    assert links == {"https://example.com/jobs/123", "https://example.com/jobs/456"}

def test_extract_collapses_identical_title_and_href():
    """The same anchor matched by several selectors is still one job."""
    html = '<html><body><h3><a href="/jobs/123">Software Engineering Intern</a></h3></body></html>'
    jobs = extract_jobs_from_html(html, "TestCompany", "https://example.com")
    assert len([j for j in jobs if j["title"] == "Software Engineering Intern"]) == 1

def test_empty_html_returns_empty():
    jobs = extract_jobs_from_html("", "TestCompany", "https://example.com")
    assert jobs == []

def test_no_jobs_in_html():
    html = "<html><body><p>Welcome to our site</p></body></html>"
    jobs = extract_jobs_from_html(html, "TestCompany", "https://example.com")
    assert jobs == []


# ===========================================================================
# General Google search: result-link filtering
# ===========================================================================

def test_company_careers_link_is_useful():
    assert _is_useful_search_result("https://careers.example-company.com/jobs/123") is True

def test_linkedin_job_link_is_useful():
    assert _is_useful_search_result("https://www.linkedin.com/jobs/view/1234567") is True

def test_google_search_link_rejected():
    assert _is_useful_search_result("https://www.google.com/search?q=x") is False

def test_google_cache_link_rejected():
    assert _is_useful_search_result("https://webcache.googleusercontent.com/search?q=cache:x") is False

def test_youtube_link_rejected():
    assert _is_useful_search_result("https://www.youtube.com/watch?v=abc") is False

def test_facebook_link_rejected():
    assert _is_useful_search_result("https://www.facebook.com/somepage") is False

def test_empty_link_rejected():
    assert _is_useful_search_result("") is False

def test_non_http_link_rejected():
    assert _is_useful_search_result("/search?q=x") is False


# ===========================================================================
# General Google search: discovered-entry construction
# ===========================================================================

def test_build_discovered_entries_basic():
    entries = _build_discovered_entries(
        "Software Student",
        ["https://a.com/jobs", "https://b.com/careers"],
        set(),
    )
    urls = [e["url"] for e in entries]
    assert "https://a.com/jobs" in urls
    assert "https://b.com/careers" in urls
    assert all(e["discovered"] is True for e in entries)
    assert all("Software Student" in e["name"] for e in entries)

def test_build_discovered_entries_dedupes_by_domain():
    entries = _build_discovered_entries(
        "Software Student",
        ["https://a.com/jobs", "https://a.com/careers"],
        set(),
    )
    assert len(entries) == 1
    assert entries[0]["url"] == "https://a.com/jobs"

def test_build_discovered_entries_skips_known_domains():
    entries = _build_discovered_entries(
        "Software Student",
        ["https://a.com/jobs", "https://b.com/careers"],
        {"a.com"},
    )
    urls = [e["url"] for e in entries]
    assert urls == ["https://b.com/careers"]

def test_build_discovered_entries_empty_links():
    assert _build_discovered_entries("Software Student", [], set()) == []


# ===========================================================================
# db.py: search queries / discovered sites (mocked requests, no live network)
# ===========================================================================

class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_get_search_queries_returns_rows(monkeypatch):
    rows = [{"name": "Software Student", "query": "software student position israel"}]
    monkeypatch.setattr(db.requests, "get", lambda *a, **k: _FakeResponse(rows))
    result = db.get_search_queries()
    assert result == rows

def test_get_search_queries_handles_error(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("boom")
    monkeypatch.setattr(db.requests, "get", _raise)
    assert db.get_search_queries() == []

def test_add_discovered_site_posts_with_source(monkeypatch):
    captured = {}
    def _fake_post(url, headers=None, json=None, **k):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({}, status_code=201)
    monkeypatch.setattr(db.requests, "post", _fake_post)
    db.add_discovered_site("example.com", "https://example.com/careers")
    assert captured["url"].endswith("/sites")
    assert captured["json"]["name"] == "example.com"
    assert captured["json"]["url"] == "https://example.com/careers"
    assert captured["json"]["source"] == "discovered"

def test_add_discovered_site_swallows_errors(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("boom")
    monkeypatch.setattr(db.requests, "post", _raise)
    db.add_discovered_site("example.com", "https://example.com/careers")  # must not raise

def test_get_all_sites_marks_discovered_flag(monkeypatch):
    rows = [
        {"name": "Wiz", "url": "https://www.wiz.io/careers", "source": "manual"},
        {"name": "example.com", "url": "https://example.com/careers", "source": "discovered"},
    ]
    monkeypatch.setattr(db.requests, "get", lambda *a, **k: _FakeResponse(rows))
    result = db.get_all_sites()
    by_name = {r["name"]: r for r in result}
    assert by_name["Wiz"]["discovered"] is False
    assert by_name["example.com"]["discovered"] is True

def test_get_all_sites_defaults_missing_source_to_manual(monkeypatch):
    rows = [{"name": "Wiz", "url": "https://www.wiz.io/careers"}]
    monkeypatch.setattr(db.requests, "get", lambda *a, **k: _FakeResponse(rows))
    result = db.get_all_sites()
    assert result[0]["discovered"] is False


# ===========================================================================
# LocalFileJobStore (job_store.py) — isolated from real seen_jobs.json/config.json
# ===========================================================================

def _make_store(tmp_path, monkeypatch, config=None):
    monkeypatch.setattr(job_store, "SEEN_JOBS_FILE", tmp_path / "seen_jobs.json")
    monkeypatch.setattr(job_store, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(job_store, "LAST_CLEANUP_FILE", tmp_path / "last_cleanup.json")
    return LocalFileJobStore(config if config is not None else {"urls": []})


def test_local_store_starts_with_no_seen_jobs(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    assert store.get_seen_job_ids() == set()

def test_local_store_save_new_jobs_creates_entry(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    job = {"id": "abc123", "title": "SW Intern", "site": "Acme",
           "link": "https://acme.com/jobs/1", "location": "Tel Aviv"}
    store.save_new_jobs([job])
    assert store.get_seen_job_ids() == {"abc123"}
    saved = job_store.json.load(open(job_store.SEEN_JOBS_FILE, encoding="utf-8"))
    assert saved["abc123"]["title"] == "SW Intern"
    assert saved["abc123"]["site"] == "Acme"
    assert saved["abc123"]["first_seen"] == saved["abc123"]["last_seen"]

def test_local_store_save_new_jobs_does_not_overwrite_existing(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    job = {"id": "abc123", "title": "SW Intern", "site": "Acme"}
    store.save_new_jobs([job])
    first_seen = store._seen_jobs["abc123"]["first_seen"]
    store.save_new_jobs([job])  # re-save same id — must not reset first_seen
    assert store._seen_jobs["abc123"]["first_seen"] == first_seen

def test_local_store_update_last_seen_bumps_existing_only(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    store.save_new_jobs([{"id": "abc123", "title": "SW Intern", "site": "Acme"}])
    old_last_seen = store._seen_jobs["abc123"]["last_seen"]
    store.update_last_seen(["abc123", "unknown-id"])  # unknown id must be ignored, not raise
    assert "unknown-id" not in store._seen_jobs
    # ISO timestamps sort lexicographically — the bump must not go backwards
    assert store._seen_jobs["abc123"]["last_seen"] >= old_last_seen

def test_local_store_get_all_sites_marks_discovered_flag():
    config = {"urls": [
        {"url": "https://wiz.io/careers", "name": "Wiz"},
        {"url": "https://found.com/jobs", "name": "found.com", "source": "discovered"},
    ]}
    store = LocalFileJobStore(config)
    by_name = {s["name"]: s for s in store.get_all_sites()}
    assert by_name["Wiz"]["discovered"] is False
    assert by_name["found.com"]["discovered"] is True

def test_local_store_add_discovered_site_persists_with_source(tmp_path, monkeypatch):
    config = {"urls": []}
    store = _make_store(tmp_path, monkeypatch, config)
    store.add_discovered_site("found.com", "https://found.com/jobs")
    assert config["urls"][0]["source"] == "discovered"
    saved = job_store.json.load(open(job_store.CONFIG_FILE, encoding="utf-8"))
    assert saved["urls"][0]["name"] == "found.com"

def test_local_store_get_search_queries_reads_from_config():
    config = {"urls": [], "general_search_queries": [{"name": "Intern", "query": "software intern israel"}]}
    store = LocalFileJobStore(config)
    assert store.get_search_queries() == [{"name": "Intern", "query": "software intern israel"}]

def test_local_store_update_site_status_is_a_noop(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    store.update_site_status("Acme", "ok", 5)  # must not raise — local mode tracks nothing here

def test_local_store_delete_stale_jobs_respects_disabled_toggle(tmp_path, monkeypatch):
    config = {"urls": [], "cleanup_stale_jobs": False}
    store = _make_store(tmp_path, monkeypatch, config)
    store.save_new_jobs([{"id": "abc", "title": "SW Intern", "site": "Acme"}])
    store.delete_stale_jobs(keep_ids=set())  # would purge everything if enabled
    assert "abc" in store._seen_jobs

def test_local_store_delete_stale_jobs_purges_when_enabled(tmp_path, monkeypatch):
    config = {"urls": [], "cleanup_stale_jobs": True}
    store = _make_store(tmp_path, monkeypatch, config)
    store.save_new_jobs([
        {"id": "keep-me", "title": "Still open", "site": "Acme"},
        {"id": "stale-one", "title": "Filled", "site": "Acme"},
    ])
    store.delete_stale_jobs(keep_ids={"keep-me"})
    assert "keep-me" in store._seen_jobs
    assert "stale-one" not in store._seen_jobs

def test_local_store_delete_stale_jobs_skips_if_run_recently(tmp_path, monkeypatch):
    config = {"urls": [], "cleanup_stale_jobs": True}
    store = _make_store(tmp_path, monkeypatch, config)
    store.save_new_jobs([
        {"id": "keep-me", "title": "Still open", "site": "Acme"},
        {"id": "stale-one", "title": "Filled", "site": "Acme"},
    ])
    store.delete_stale_jobs(keep_ids={"keep-me"})  # first call: runs, purges, writes the timestamp
    assert "stale-one" not in store._seen_jobs

    store.save_new_jobs([{"id": "stale-two", "title": "Also filled", "site": "Acme"}])
    store.delete_stale_jobs(keep_ids={"keep-me"})  # second call, same day: gated, must not purge
    assert "stale-two" in store._seen_jobs
