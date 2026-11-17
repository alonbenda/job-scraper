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
    matches_candidate,
    classify_by_description,
    _categorize_job,
    matches_location,
    job_id,
    canonical_link,
    assign_job_ids,
    extract_jobs_from_html,
    _is_useful_search_result,
    _build_discovered_entries,
    _normalize_company_name,
    _company_already_covered,
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

def test_api_job_ignores_israeli_source_url():
    # The API lists a company's jobs worldwide; "?location=Israel" in the page URL doesn't filter it
    foreign = {"title": "Design Engineer", "link": "https://x.com/1", "location": "New York, Hybrid",
               "site": "vega", "source_url": "https://www.comeet.com/jobs/vega/C9.009?location=Israel"}
    assert matches_location(foreign, ["israel", "tel aviv"]) is True  # scraped HTML: the URL filter applies
    assert matches_location({**foreign, "via_api": True}, ["israel", "tel aviv"]) is False

def test_api_job_with_israeli_location_still_accepted():
    job = {"title": "Backend Engineer", "link": "https://x.com/1", "location": "Tel Aviv, Israel", "site": "vega",
           "source_url": "https://www.comeet.com/jobs/vega/C9.009?location=Israel", "via_api": True}
    assert matches_location(job, ["israel", "tel aviv"]) is True

def test_spelled_out_us_states_and_region_labels_rejected():
    for loc in ("Raleigh, RTP, North Carolina", "Waco, Texas (REMOTE)", "Remote (US)", "Kyiv", "APAC",
                "Reykjavík, Iceland - Reykjavik", "Nordics (Remote)", "IN-IN-Bengaluru", "Florida"):
        job = {"title": "Software Engineer", "link": "", "location": loc, "site": "X", "source_url": ""}
        assert matches_location(job, ["israel", "tel aviv"]) is False, loc

def test_israeli_regions_and_cities_not_rejected():
    for loc in ("North", "Center", "Sharon", "Shfela", "South", "Kiryat Gat,ISR", "Mig Ha'emek,ISR", "Kinneret",
                "Tel Aviv / London", "Herzliya, Israel"):
        job = {"title": "Software Engineer", "link": "", "location": loc, "site": "X", "source_url": ""}
        assert matches_location(job, ["israel", "tel aviv", "herzliya"]) is True, loc

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

    @property
    def ok(self):
        return self.status_code < 400

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
# Description-based junior detection (ordered rules)
# ===========================================================================

def test_candidate_needs_field_but_no_role_and_no_exclude():
    roles, fields, excl = ["junior", "intern"], ["software", "engineer"], ["senior", "lead"]
    assert matches_candidate("Software Engineer", roles, fields, excl) == ["software", "engineer"]
    assert matches_candidate("Junior Software Engineer", roles, fields, excl) == []  # matches_job handles it
    assert matches_candidate("Senior Software Engineer", roles, fields, excl) == []
    assert matches_candidate("Account Manager", roles, fields, excl) == []

def test_classify_rejects_three_plus_years():
    assert classify_by_description("Requires 5+ years of experience in backend") == "reject"
    assert classify_by_description("3-5 years experience with Python") == "reject"

def test_classify_junior_by_years():
    assert classify_by_description("0-2 years of experience") == "junior"
    assert classify_by_description("1+ years of relevant experience") == "junior"

def test_classify_range_must_top_out_at_three_to_be_junior():
    assert classify_by_description("0-3 years of relevant experience") == "junior"
    assert classify_by_description("1-3 years of experience") == "junior"
    assert classify_by_description("0-5 years of experience") == "maybe"

def test_classify_rejects_two_plus_years():
    assert classify_by_description("2+ years of experience") == "reject"
    assert classify_by_description("2 years of relevant experience") == "reject"
    assert classify_by_description("2-4 years of experience in DevOps") == "reject"
    assert classify_by_description("2–4 years of experience in DevOps") == "reject"  # en dash
    assert classify_by_description("שנתיים ניסיון בפיתוח") == "reject"

def test_classify_years_with_experience_far_after_the_number():
    text = "- 3+ years of non-internship professional software development experience - Bachelor's degree"
    assert classify_by_description(text) == "reject"

def test_classify_junior_by_phrase():
    assert classify_by_description("Great for a recent graduate. Entry level role.") == "junior"
    assert classify_by_description("מחפשים בוגרים טריים להצטרף לצוות") == "junior"
    assert classify_by_description("ללא ניסיון קודם") == "junior"

def test_classify_hebrew_degree_requirement_is_not_junior():
    # "בוגר.ת תואר ראשון בהנדסת חשמל" is a standard requirement on senior roles too
    assert classify_by_description("דרישות התפקיד: בוגר.ת תואר ראשון לפחות בהנדסת חשמל") == "maybe"
    assert classify_by_description("בוגר/ת תואר ראשון בהנדסה") == "maybe"

def test_classify_reject_beats_junior():
    # Rules are ordered: a 5+ years requirement wins over a stray "graduate" phrase
    assert classify_by_description("New grads welcome. 5+ years of experience required.") == "reject"

def test_classify_senior_word_in_description_is_not_a_reject():
    text = "Join our team and learn from our senior engineers. Entry level."
    assert classify_by_description(text) == "junior"

def test_classify_reject_on_lead_roles():
    assert classify_by_description("You will act as the team lead for 4 developers") == "reject"
    assert classify_by_description("Software Engineer III responsibilities") == "reject"

def test_classify_maybe_when_nothing_decides():
    assert classify_by_description("We build great products. Python, SQL.") == "maybe"
    assert classify_by_description("") == "maybe"

def test_classify_ignores_years_not_about_experience():
    assert classify_by_description("Founded 20 years ago, we build products.") == "maybe"

def test_classify_engineer_i_is_case_sensitive():
    assert classify_by_description("Software Engineer I") == "junior"
    assert classify_by_description("the engineer i spoke with") == "maybe"

def test_fetch_job_description_prefers_api_description():
    from job_scraper import fetch_job_description
    job = {"title": "DevOps Engineer", "link": "https://example.com/x", "description": "6+ years of experience"}
    assert fetch_job_description(job, {}) == "6+ years of experience"

def test_html_to_text_strips_tags_and_entities():
    from api_fetchers import _html_to_text
    assert _html_to_text("<p>6+&nbsp;years of <strong>experience</strong></p>") == "6+ years of experience"

def test_comeet_job_carries_description(monkeypatch):
    import api_fetchers
    class _Page:
        text = '{"token": "tok"}'
        def raise_for_status(self): pass
    class _Api:
        def raise_for_status(self): pass
        def json(self):
            return [{"name": "DevOps Engineer", "uid": "BB.1", "url_active_page": "https://c/x",
                     "location": {"city": "Tel Aviv"}, "time_updated": "2026-11-01T00:00:00",
                     "details": [{"name": "Requirements", "value": "<li>6+ years of experience</li>"}]}]
    monkeypatch.setattr(api_fetchers.requests, "get", lambda *a, **k: _Page())
    seen = {}
    def _fake_request(method, url, **k):
        seen["params"] = k.get("params")
        return _Api()
    monkeypatch.setattr(api_fetchers, "_request", _fake_request)
    jobs = api_fetchers._fetch_comeet("https://www.comeet.com/jobs/Claroty/F2.004", "Claroty")
    assert seen["params"]["details"] == "true"
    assert jobs[0]["description"] == "6+ years of experience"
    assert classify_by_description(jobs[0]["description"], jobs[0]["title"]) == "reject"

def test_eightfold_jobs_carry_description_ref(monkeypatch):
    import api_fetchers
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"data": {"count": 1, "positions": [
                {"id": 790317089826, "name": "Devops Engineer", "positionUrl": "/careers/job/790317089826",
                 "locations": ["Rehovot,ISR"], "postedTs": 1793836800}]}}
    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: _Resp())
    jobs = api_fetchers._eightfold("https://careers.appliedmaterials.com", "appliedmaterials.com", "", "Applied Materials")
    assert jobs[0]["desc_ref"] == {"type": "eightfold", "base": "https://careers.appliedmaterials.com",
                                   "domain": "appliedmaterials.com", "id": 790317089826}

def test_fetch_description_eightfold(monkeypatch):
    import api_fetchers
    seen = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"data": {"jobDescription": "<p>5+ years of <b>experience</b></p>"}}
    def _fake(method, url, **k):
        seen.update(url=url, params=k["params"]); return _Resp()
    monkeypatch.setattr(api_fetchers, "_request", _fake)
    text = api_fetchers.fetch_description({"type": "eightfold", "base": "https://x.com", "domain": "x.com", "id": 7})
    assert text == "5+ years of experience"
    assert seen["url"].endswith("/api/pcsx/position_details") and seen["params"]["position_id"] == 7

def test_fetch_description_returns_empty_on_failure(monkeypatch):
    import api_fetchers
    def _boom(*a, **k): raise ConnectionError("x")
    monkeypatch.setattr(api_fetchers, "_request", _boom)
    assert api_fetchers.fetch_description({"type": "eightfold", "base": "b", "domain": "d", "id": 1}) == ""
    assert api_fetchers.fetch_description({"type": "unknown"}) == ""

def test_fetch_job_description_uses_desc_ref(monkeypatch):
    import job_scraper
    monkeypatch.setattr(job_scraper, "fetch_description", lambda ref: "from api " + str(ref["id"]))
    assert job_scraper.fetch_job_description({"title": "t", "link": "https://x", "desc_ref": {"id": 5}}, {}) == "from api 5"

def test_desc_ref_from_workday_link():
    from api_fetchers import desc_ref_from_link
    ref = desc_ref_from_link("https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/Caesarea-Israel/Logic-Design-Engineer_2012211/apply")
    assert ref == {"type": "workday", "base": "https://cisco.wd5.myworkdayjobs.com", "tenant": "cisco",
                   "board": "Cisco_Careers", "path": "job/Caesarea-Israel/Logic-Design-Engineer_2012211"}
    # the locale prefix the Workday fetcher puts in links is skipped too
    ref2 = desc_ref_from_link("https://intel.wd1.myworkdayjobs.com/en-US/External/job/Haifa/Eng_JR1")
    assert ref2["board"] == "External" and ref2["path"] == "job/Haifa/Eng_JR1"
    assert desc_ref_from_link("https://example.com/job/1") is None
    assert desc_ref_from_link("") is None

def test_fetch_description_workday(monkeypatch):
    import api_fetchers
    seen = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"jobPostingInfo": {"jobDescription": "<p>5+ years of experience</p>"}}
    def _fake(method, url, **k):
        seen["url"] = url; return _Resp()
    monkeypatch.setattr(api_fetchers, "_request", _fake)
    ref = api_fetchers.desc_ref_from_link("https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/X/Y_1/apply")
    assert api_fetchers.fetch_description(ref) == "5+ years of experience"
    assert seen["url"] == "https://cisco.wd5.myworkdayjobs.com/wday/cxs/cisco/Cisco_Careers/job/X/Y_1"

def test_fetch_job_description_detects_workday_link(monkeypatch):
    import job_scraper
    monkeypatch.setattr(job_scraper, "fetch_description", lambda ref: "wd:" + ref["type"])
    job = {"title": "t", "link": "https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/X/Y_1/apply"}
    assert job_scraper.fetch_job_description(job, {}) == "wd:workday"

def test_html_to_text_handles_escaped_markup():
    from api_fetchers import _html_to_text
    assert _html_to_text("&lt;div&gt;&lt;p&gt;3+ years &amp;amp; more&lt;/p&gt;&lt;/div&gt;") == "3+ years & more"

def test_greenhouse_spec_requests_content_and_keeps_description(monkeypatch):
    import api_fetchers
    seen = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"jobs": [{"title": "Software Engineer", "absolute_url": "https://x/1", "id": 1,
                              "location": {"name": "Tel Aviv"}, "updated_at": "2026-11-01T00:00:00",
                              "content": "&lt;p&gt;6+ years of experience&lt;/p&gt;"}]}
    def _fake(method, url, **k):
        seen["params"] = k.get("params"); return _Resp()
    monkeypatch.setattr(api_fetchers, "_request", _fake)
    spec = {"type": "json", "url": "https://boards-api.greenhouse.io/v1/boards/catonetworks/jobs",
            "list": "jobs", "title": "title", "link": "absolute_url", "location": "location.name",
            "date": "updated_at", "id": "id"}
    jobs = api_fetchers._fetch_json(spec, "Cato Networks")
    assert seen["params"]["content"] == "true"
    assert jobs[0]["description"] == "6+ years of experience"
    assert "description" not in spec  # caller's spec isn't mutated

def test_amazon_job_description_uses_basic_but_not_preferred_qualifications(monkeypatch):
    import api_fetchers
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"hits": 1, "jobs": [{
                "title": "Embedded Software Engineer", "job_path": "/en/jobs/1/x", "id_icims": "1",
                "description": "<p>Build firmware.</p>",
                "basic_qualifications": "- 1+ years of experience<br/>- BSc",
                "preferred_qualifications": "- 7+ years of experience"}]}
    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: _Resp())
    job = api_fetchers._fetch_amazon("https://www.amazon.jobs/x", "Amazon")[0]
    assert "1+ years" in job["description"] and "7+ years" not in job["description"]
    assert classify_by_description(job["description"], job["title"]) == "junior"

def test_short_page_text_is_not_a_description(monkeypatch):
    import job_scraper
    nav = "Home Teams Locations Job categories My career " * 4  # a JS app's nav, ~190 chars
    monkeypatch.setattr(job_scraper, "try_static_fetch", lambda url: f"<html><body><main>{nav}</main></body></html>")
    assert job_scraper.fetch_job_description({"title": "t", "link": "https://www.amazon.jobs/en/jobs/1"}, {}) == ""

def test_json_spec_description_from_several_paths(monkeypatch):
    import api_fetchers
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return [{"jobId": 1, "jobTitle": "Algorithm Developer", "area": "North", "openDate": "2026-11-01T00:00:00",
                     "description": "Join us.&lt;div&gt;3+ years of experience&lt;/div&gt;",
                     "requirements": "&lt;br&gt;BSc"}]
    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: _Resp())
    spec = {"type": "json", "url": "https://elbitsystemscareer.com/cron/jobs.json", "list": "",
            "title": "jobTitle", "link_template": "https://x/?jobId={jobId}", "location": "area",
            "date": "openDate", "id": "jobId", "description": ["description", "requirements"]}
    job = api_fetchers._fetch_json(spec, "Elbit")[0]
    assert job["description"] == "Join us. 3+ years of experience BSc"
    assert classify_by_description(job["description"], job["title"]) == "reject"

def test_comeet_json_spec_requests_details_and_joins_sections(monkeypatch):
    import api_fetchers
    seen = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return [{"name": "DevOps Engineer", "url_active_page": "https://c/x", "uid": "1",
                     "location": {"city": "Tel Aviv"}, "time_updated": "2026-11-01T00:00:00",
                     "details": [{"name": "Description", "value": "<p>Build things</p>"},
                                 {"name": "Requirements", "value": "<li>5+ years of experience</li>"}]}]
    def _fake(method, url, **k):
        seen["params"] = k.get("params"); return _Resp()
    monkeypatch.setattr(api_fetchers, "_request", _fake)
    spec = {"type": "json", "url": "https://www.comeet.co/careers-api/2.0/company/43.001/positions",
            "params": {"token": "t", "details": "false"}, "list": "", "title": "name", "link": "url_active_page",
            "location": ["location.city"], "date": "time_updated", "id": "uid"}
    job = api_fetchers._fetch_json(spec, "VAST Data")[0]
    assert seen["params"]["details"] == "true" and seen["params"]["token"] == "t"
    assert job["description"] == "Build things 5+ years of experience"

def test_ashby_spec_reads_plain_description(monkeypatch):
    import api_fetchers
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"jobs": [{"id": "1", "title": "Software Engineer", "jobUrl": "https://jobs.ashbyhq.com/x/1",
                              "location": "Tel Aviv", "publishedAt": "2026-11-01T00:00:00",
                              "descriptionPlain": "You need 5+ years of experience."}]}
    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: _Resp())
    job = api_fetchers._fetch_from_spec({"type": "ashby", "board": "snyk"}, "Snyk")[0]
    assert job["description"] == "You need 5+ years of experience."

def test_oracle_jobs_carry_ref_and_link_is_recognised():
    import api_fetchers
    ref = api_fetchers.desc_ref_from_link(
        "https://hdjq.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26013084")
    assert ref == {"type": "oracle", "base": "https://hdjq.fa.us2.oraclecloud.com", "site": "CX_1", "id": "26013084"}

def test_oracle_list_attaches_desc_ref(monkeypatch):
    import api_fetchers
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"items": [{"TotalJobsCount": 1, "requisitionList": [
                {"Id": "99", "Title": "DevOps Engineer", "PrimaryLocation": "Israel", "PostedDate": "2026-11-01"}]}]}
    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: _Resp())
    job = api_fetchers._oracle("https://x.oraclecloud.com", "CX_1", None, None, "Dell")[0]
    assert job["desc_ref"] == {"type": "oracle", "base": "https://x.oraclecloud.com", "site": "CX_1", "id": "99"}

def test_fetch_description_oracle(monkeypatch):
    import api_fetchers
    seen = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"items": [{"ExternalDescriptionStr": "<p>Build things</p>",
                               "ExternalQualificationsStr": "<li>4+ years of experience</li>"}]}
    def _fake(method, url, **k):
        seen.update(url=url, params=k["params"]); return _Resp()
    monkeypatch.setattr(api_fetchers, "_request", _fake)
    text = api_fetchers.fetch_description({"type": "oracle", "base": "https://x", "site": "CX_1", "id": "5"})
    assert text == "Build things 4+ years of experience"
    assert seen["params"]["finder"] == 'ById;Id="5",siteNumber=CX_1'

def test_taleo_description_is_decoded_from_the_page():
    import api_fetchers
    enc = ("%3Cp%3EAt%20least%203%20years%20industry-relevant%20work%20experience%3C/p%3E"
           "%3Cli%3EHands-on%20%26amp%3B%20L2/L3%20switching%3C/li%3E")
    page = f"<html>...!|!260000AK!|!!*!{enc}!|!x!|!...!*!{enc}!|!y</html>"  # repeated in a printable copy
    text = api_fetchers._taleo_description(page)
    assert text == "At least 3 years industry-relevant work experience Hands-on & L2/L3 switching"
    assert api_fetchers._taleo_description("<html>nothing here</html>") == ""

def test_desc_ref_recognises_taleo_and_apple_links():
    from api_fetchers import desc_ref_from_link
    t = desc_ref_from_link("https://radware.taleo.net/careersection/ex/jobdetail.ftl?job=260000AK")
    assert t == {"type": "taleo", "url": "https://radware.taleo.net/careersection/ex/jobdetail.ftl?job=260000AK"}
    a = desc_ref_from_link("https://jobs.apple.com/en-il/details/200694733-1451/embedded-engineer?team=HRDWR")
    assert a["type"] == "apple"
    assert desc_ref_from_link("https://radware.taleo.net/careersection/ex/jobsearch.ftl") is None

def test_apple_description_skips_preferred_qualifications():
    import api_fetchers, json as _json
    job = {"jobSummary": "Build SW.", "description": "Write drivers.",
           "minimumQualifications": "1+ years of experience in C", "preferredQualifications": "10 years of experience"}
    data = {"loaderData": {"jobDetails": {"jobsData": job}}}
    # the page holds JSON inside a JS string: JSON.parse("<escaped json>")
    page = 'x window.__staticRouterHydrationData = JSON.parse(' + _json.dumps(_json.dumps(data)) + '); y'
    text = api_fetchers._apple_description(page)
    assert "1+ years" in text and "10 years" not in text
    assert api_fetchers._apple_description("<html></html>") == ""

def test_json_spec_desc_ref_is_attached_with_the_job_id(monkeypatch):
    import api_fetchers
    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"count": 1, "positions": [{"id": 563602813922996, "name": "QA Engineer", "location": "Tel Aviv"}]}
    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: _Resp())
    spec = {"type": "json", "url": "https://www.careers.teva/api/apply/v2/jobs", "list": "positions",
            "title": "name", "link_template": "https://www.careers.teva/careers/job/{id}", "location": "location",
            "id": "id", "desc_ref": {"type": "eightfold", "base": "https://www.careers.teva", "domain": "tevapharm.com"}}
    job = api_fetchers._fetch_json(spec, "Teva")[0]
    assert job["desc_ref"] == {"type": "eightfold", "base": "https://www.careers.teva",
                               "domain": "tevapharm.com", "id": 563602813922996}

def test_job_link_heuristic():
    from job_scraper import _looks_like_job_link
    for good in ("https://jobs.intuit.com/job/san-diego/software-engineer/27595/103261394672",
                 "https://orca.security/about/careers/6161068004/?gh_jid=6161068004",
                 "https://drivenets.com/career/2C.37B/",
                 "https://www.amazon.jobs/en/jobs/10605544/verification-engineer"):
        assert _looks_like_job_link(good), good
    for bad in ("https://www.silicom-usa.com/cats/edge-ai-solutions/",
                "https://www.intuit.com/careers/teams/software-engineering/",
                "https://youtu.be/Tp0hjWKzBKw", "https://www.abra-it.com#jobs", ""):
        assert not _looks_like_job_link(bad), bad

def test_dedupe_across_sources_drops_the_second_source_only():
    from job_scraper import dedupe_across_sources
    seen = {}
    phenom = [{"title": "Backend Intern", "location": "Herzliya, Israel"},
              {"title": "Networking PBM", "location": "Ra'anana, Israel"}]
    workday = [{"title": "Backend Intern", "location": "Herzliya, Israel"},      # same job, other source
               {"title": "Storage Lead", "location": "Ra'anana, Israel"}]          # only on Workday
    assert dedupe_across_sources(phenom, "HPE", seen) == phenom
    kept = dedupe_across_sources(workday, "HPE (Workday)", seen)
    assert [j["title"] for j in kept] == ["Storage Lead"]

def test_dedupe_never_drops_a_sources_own_jobs():
    from job_scraper import dedupe_across_sources
    own = [{"title": "QA Engineer", "location": "Tel Aviv"}, {"title": "QA Engineer", "location": "Tel Aviv"},
           {"title": "QA Engineer", "location": "Haifa"}]
    assert len(dedupe_across_sources(own, "Acme", {})) == 3

def test_dedupe_keeps_different_companies_apart():
    from job_scraper import dedupe_across_sources
    seen = {}
    dedupe_across_sources([{"title": "Backend Intern", "location": "Tel Aviv"}], "Wix", seen)
    kept = dedupe_across_sources([{"title": "Backend Intern", "location": "Tel Aviv"}], "Monday", seen)
    assert len(kept) == 1
    # same company, different Oracle instances in brackets: names differ only in the suffix
    assert len(dedupe_across_sources([{"title": "X", "location": "Y"}], "Oracle Cloud (a)", {})) == 1

def test_run_scraper_end_to_end_with_fake_sources(monkeypatch):
    """Whole pipeline on made-up sources: title match, description rules, API-vs-HTML
    gating, location handling for API jobs, and cross-source dedup."""
    import job_scraper

    class FakeStore:
        backend_name = "fake"
        def __init__(self): self.saved = []; self.seen = set()
        def get_all_sites(self):
            return [{"name": "Acme", "url": "https://acme.example/careers?location=Israel"},
                    {"name": "Acme (Workday)", "url": "https://acme.wd1.example/jobs"},
                    {"name": "Blog Co", "url": "https://blog.example/careers"}]
        def get_search_queries(self): return []
        def get_seen_job_ids(self): return set(self.seen)
        def update_site_status(self, *a, **k): return None
        def save_new_jobs(self, jobs): self.saved += jobs
        def rename_job_ids(self, m): pass
        def update_last_seen(self, ids): pass
        def delete_stale_jobs(self, keep): pass

    def job(title, link, site, loc, **extra):
        return {"title": title, "link": link, "site": site, "location": loc, "posted_date": "", **extra}

    sources = {
        # API source: the page URL says "location=Israel" but the API lists the whole world
        "Acme": [job("Junior Backend Engineer", "https://acme.example/job/1", "Acme", "Tel Aviv", via_api=True),
                 job("Junior Backend Engineer", "https://acme.example/job/2", "Acme", "New York", via_api=True),
                 job("Backend Engineer", "https://acme.example/job/3", "Acme", "Tel Aviv",
                     via_api=True, description="Requires 6+ years of experience."),
                 job("DevOps Engineer", "https://acme.example/job/4", "Acme", "Tel Aviv",
                     via_api=True, description="Entry level, 0-2 years of experience."),
                 job("Platform Engineer", "https://acme.example/job/5", "Acme", "Haifa",
                     via_api=True, description="We build platforms.")],
        # second source of the same company: repeats one job
        "Acme (Workday)": [job("DevOps Engineer", "https://acme.wd1.example/job/9", "Acme (Workday)", "Tel Aviv",
                               via_api=True, description="Entry level, 0-2 years of experience.")],
        # scraped HTML: a blog post whose title contains a field keyword must not become a "maybe"
        "Blog Co": [job("Software Engineering at Blog Co", "https://blog.example/post/why-we-love-code", "Blog Co", "")],
    }
    store = FakeStore()
    cfg = {"role_keywords": ["junior", "intern"], "field_keywords": ["engineer", "software"],
           "exclude_keywords": ["senior"], "location_filter": ["israel", "tel aviv", "haifa"]}
    monkeypatch.setattr(job_scraper, "load_config", lambda: cfg)
    monkeypatch.setattr(job_scraper, "get_store", lambda c: store)
    monkeypatch.setattr(job_scraper, "scrape_site", lambda entry, c: [dict(j) for j in sources[entry["name"]]])
    monkeypatch.setattr(job_scraper, "load_cv_skills", lambda c: {})
    monkeypatch.setattr(job_scraper, "send_email", lambda *a, **k: None)
    monkeypatch.setattr(job_scraper, "time", type("T", (), {"sleep": staticmethod(lambda s: None), "time": staticmethod(__import__("time").time)}))

    new = job_scraper.run_scraper()
    got = {j["title"] + "|" + j["location"]: j.get("category") for j in new}
    assert got == {
        "Junior Backend Engineer|Tel Aviv": None,       # matched by title
        "DevOps Engineer|Tel Aviv": "junior",           # no role in title, description says 0-2 years
        "Platform Engineer|Haifa": "maybe",             # nothing decisive in the description
    }
    # rejected by description (6+ years) -> remembered as a hidden row, not shown
    assert any(j["title"] == "Backend Engineer" and j["category"] == "rejected" for j in store.saved)
    assert not any(j["title"] == "Backend Engineer" for j in new)
    # the New York API job is dropped despite "?location=Israel" in the page URL
    assert not any(j["location"] == "New York" for j in new)
    # the Workday copy of the DevOps job is a duplicate of the other source's
    assert sum(1 for j in new if j["title"] == "DevOps Engineer") == 1
    # the blog post (scraped, non-job link) never reaches the description step
    assert not any("Blog Co" in j["title"] for j in store.saved)

def test_categorize_job_maybe():
    assert _categorize_job({"category": "maybe", "matched_roles": []}) == "maybe"
    assert _categorize_job({"category": "junior", "matched_roles": []}) == "junior"
    assert _categorize_job({"matched_roles": ["intern"]}) == "student"


def test_dismiss_job_patches_dismissed_flag(monkeypatch):
    captured = {}
    def _fake_patch(url, headers=None, params=None, json=None, **k):
        captured.update(url=url, params=params, json=json)
        return _FakeResponse({}, status_code=204)
    monkeypatch.setattr(db.requests, "patch", _fake_patch)
    assert db.dismiss_job("abc123") is True
    assert captured["params"] == {"id": "eq.abc123"}
    assert captured["json"] == {"dismissed": True}

def test_dismiss_job_returns_false_on_error(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("boom")
    monkeypatch.setattr(db.requests, "patch", _raise)
    assert db.dismiss_job("abc123") is False

def test_get_jobs_falls_back_when_dismissed_column_missing(monkeypatch):
    calls = []
    def _fake_get(url, headers=None, params=None, **k):
        calls.append(params)
        if "or" in params:
            return _FakeResponse([], status_code=400)
        return _FakeResponse([{"id": "1"}])
    monkeypatch.setattr(db.requests, "get", _fake_get)
    assert db.get_jobs() == [{"id": "1"}]
    assert "or" in calls[0] and "or" not in calls[1]

def test_save_new_jobs_includes_category_only_when_set(monkeypatch):
    sent = []
    monkeypatch.setattr(db.requests, "post",
                        lambda url, headers=None, json=None, **k: (sent.append(json), _FakeResponse({}, 201))[1])
    db.save_new_jobs([
        {"id": "a", "title": "t", "site": "s", "category": "maybe"},
        {"id": "b", "title": "t", "site": "s"},
    ])
    rows = {r["id"]: r for batch in sent for r in batch}
    assert rows["a"]["category"] == "maybe"
    assert "category" not in rows["b"]
    # PostgREST rejects bulk inserts with differing keys, so each shape is its own request
    assert len(sent) == 2 and all(len({tuple(sorted(r)) for r in batch}) == 1 for batch in sent)

def test_save_new_jobs_rejected_rows_are_hidden(monkeypatch):
    sent = []
    monkeypatch.setattr(db.requests, "post",
                        lambda url, headers=None, json=None, **k: (sent.append(json), _FakeResponse({}, 201))[1])
    db.save_new_jobs([{"id": "r", "title": "t", "site": "s", "category": "rejected"}])
    assert sent[0][0]["category"] == "rejected"
    assert sent[0][0]["dismissed"] is True

def test_save_new_jobs_mixed_rows_never_share_a_batch(monkeypatch):
    sent = []
    monkeypatch.setattr(db.requests, "post",
                        lambda url, headers=None, json=None, **k: (sent.append(json), _FakeResponse({}, 201))[1])
    db.save_new_jobs([
        {"id": "r", "title": "t", "site": "s", "category": "rejected"},
        {"id": "m", "title": "t", "site": "s", "category": "maybe"},
        {"id": "p", "title": "t", "site": "s"},
    ])
    assert len(sent) == 3
    assert {r["id"] for batch in sent for r in batch if r.get("dismissed")} == {"r"}


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


# ===========================================================================
# Aggregator company attribution — fixtures mirror real markup verified live
# ===========================================================================

def test_extract_finds_company_on_linkedin_shaped_page():
    html = '''
    <div class="base-search-card__info">
      <h3 class="base-search-card__title"><a href="/jobs/view/123">Automation Student</a></h3>
      <h4 class="base-search-card__subtitle"><a href="/company/radware">Radware</a></h4>
    </div>'''
    jobs = extract_jobs_from_html(html, "LinkedIn", "https://www.linkedin.com", is_aggregator=True)
    assert any(j["site"] == "Radware" for j in jobs)

def test_extract_finds_company_on_indeed_shaped_page():
    html = '''
    <div>
      <h2><a href="/rc/clk?jk=abc123">QA Student</a></h2>
      <span data-testid="company-name">Amdocs</span>
    </div>'''
    jobs = extract_jobs_from_html(html, "Indeed", "https://il.indeed.com", is_aggregator=True)
    assert any(j["site"] == "Amdocs" for j in jobs)

def test_extract_finds_company_on_glassdoor_shaped_page():
    html = '''
    <div>
      <a href="/job/view/456">Junior Backend Engineer</a>
      <span class="EmployerProfile_compactEmployerName__9MGcV">Applied Materials</span>
    </div>'''
    jobs = extract_jobs_from_html(html, "GlassDoor", "https://www.glassdoor.com", is_aggregator=True)
    assert any(j["site"] == "Applied Materials" for j in jobs)

def test_extract_falls_back_to_site_name_when_no_company_found():
    html = '<a href="/jobs/view/789">Backend Intern</a>'
    jobs = extract_jobs_from_html(html, "LinkedIn", "https://www.linkedin.com", is_aggregator=True)
    assert any(j["site"] == "LinkedIn" for j in jobs)

def test_extract_ignores_company_markup_when_not_an_aggregator():
    """A company's own career page shouldn't have its job's 'site' overridden by stray markup."""
    html = '''
    <div>
      <a href="/careers/123">Backend Intern</a>
      <span class="subtitle">Some unrelated widget text</span>
    </div>'''
    jobs = extract_jobs_from_html(html, "Wiz", "https://www.wiz.io")  # is_aggregator defaults False
    assert any(j["site"] == "Wiz" for j in jobs)


# ===========================================================================
# Tracking-param extension: ephemeral LinkedIn/Indeed params
# ===========================================================================

def test_linkedin_position_and_pagenum_stripped():
    a = canonical_link("https://il.linkedin.com/jobs/view/automation-student-at-radware-4452294600?position=60&pageNum=0&refId=x&trackingId=y")
    b = canonical_link("https://il.linkedin.com/jobs/view/automation-student-at-radware-4452294600?position=57&pageNum=0&refId=z&trackingId=w")
    assert a == b

def test_indeed_bb_and_xkcb_stripped():
    a = canonical_link("https://il.indeed.com/rc/clk?jk=8ceedeece4e45355&bb=aaa&xkcb=bbb&fccid=be72bcf1")
    b = canonical_link("https://il.indeed.com/rc/clk?jk=8ceedeece4e45355&bb=ccc&xkcb=ddd&fccid=be72bcf1")
    assert a == b

def test_indeed_different_jk_still_differs():
    a = canonical_link("https://il.indeed.com/rc/clk?jk=aaaaaaaaaaaaaaaa&bb=x&xkcb=y")
    b = canonical_link("https://il.indeed.com/rc/clk?jk=bbbbbbbbbbbbbbbb&bb=x&xkcb=y")
    assert a != b


# ===========================================================================
# Company name normalization / already-covered matching
# ===========================================================================

def test_normalize_strips_suffix_and_punctuation():
    assert _normalize_company_name("Check Point Software Technologies") == "check point"
    assert _normalize_company_name("Wiz, Inc.") == "wiz"

def test_company_already_covered_exact_match():
    assert _company_already_covered("Radware", {"Radware", "SAP"}) is True

def test_company_already_covered_substring_match():
    assert _company_already_covered("Amazon Web Services", {"Amazon"}) is True
    assert _company_already_covered("Check Point", {"Check Point Software Technologies"}) is True

def test_company_not_covered_when_unrelated():
    assert _company_already_covered("Some Random Startup", {"Radware", "SAP", "Wiz"}) is False

def test_company_short_names_dont_false_match():
    """Guards against a short name trivially substring-matching everything."""
    assert _company_already_covered("AU", {"Australian Something Corp"}) is False

def test_company_already_covered_empty_known_set():
    assert _company_already_covered("Radware", set()) is False


# ===========================================================================
# API fetchers
# ===========================================================================

def test_api_handler_dispatch():
    from api_fetchers import _handler_for
    assert _handler_for("https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers").__name__ == "_fetch_workday"
    assert _handler_for("https://job-boards.greenhouse.io/fireblocks").__name__ == "_fetch_greenhouse"
    assert _handler_for("https://www.comeet.com/jobs/claroty/F2.004").__name__ == "_fetch_comeet"
    assert _handler_for("https://www.linkedin.com/jobs/search/?keywords=x").__name__ == "_fetch_linkedin"
    assert _handler_for("https://www.cyberark.com/careers/all-job-openings/") is None


def test_fetch_via_api_unsupported_returns_none():
    from api_fetchers import fetch_via_api
    assert fetch_via_api("https://example.com/careers", "Example") is None


def test_fetch_via_api_failure_returns_none(monkeypatch):
    import api_fetchers
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(api_fetchers, "_request", boom)
    assert api_fetchers.fetch_via_api("https://x.wd1.myworkdayjobs.com/Ext", "X") is None


def test_fetch_greenhouse_parses_jobs(monkeypatch):
    import api_fetchers

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"jobs": [{"title": "Intern", "absolute_url": "https://x/1",
                              "location": {"name": "Tel Aviv"}, "updated_at": "2026-01-02T03:04:05Z"}]}

    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: Resp())
    jobs = api_fetchers.fetch_via_api("https://job-boards.greenhouse.io/acme", "Acme")
    assert jobs == [{"title": "Intern", "link": "https://x/1", "site": "Acme",
                     "location": "Tel Aviv", "posted_date": "2026-01-02"}]


def test_dig_dotted_paths():
    from api_fetchers import _dig
    assert _dig({"a": {"b": [{"c": 5}]}}, "a.b.0.c") == 5
    assert _dig({"a": None}, "a.b") is None
    assert _dig([1, 2], "") == [1, 2]


def test_fetch_json_spec_parses_and_formats_link(monkeypatch):
    import api_fetchers

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"data": [{"id": 7, "title": "R&amp;D Intern", "loc": {"city": "Haifa"}}]}

    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: Resp())
    spec = {"type": "json", "url": "https://x/api", "list": "data", "title": "title",
            "link_template": "https://x/job/{id}", "location": ["loc.city"]}
    jobs = api_fetchers.fetch_via_api("", "Acme", spec)
    assert jobs == [{"title": "R&D Intern", "link": "https://x/job/7", "site": "Acme",
                     "location": "Haifa", "posted_date": ""}]


def test_fetch_taleo_parses_columns(monkeypatch):
    import api_fetchers

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"requisitionList": [{"contestNo": "26A", "column": ["QA Intern", '["IL-Haifa"]', "Nov 1, 2026"]}],
                    "pagingData": {"pageSize": 25, "totalCount": 1}}

    monkeypatch.setattr(api_fetchers, "_request", lambda *a, **k: Resp())
    jobs = api_fetchers.fetch_via_api("", "Acme", {"type": "taleo", "base": "https://a.taleo.net"})
    assert jobs == [{"title": "QA Intern", "link": "https://a.taleo.net/careersection/ex/jobdetail.ftl?job=26A",
                     "site": "Acme", "location": "IL-Haifa", "posted_date": "Nov 1, 2026", "external_id": "26A"}]


def test_scrape_site_falls_back_to_scraping_on_empty_api(monkeypatch):
    import job_scraper
    monkeypatch.setattr(job_scraper, "fetch_via_api", lambda *a, **k: [])
    monkeypatch.setattr(job_scraper, "try_static_fetch", lambda url: "<html></html>")
    monkeypatch.setattr(job_scraper, "extract_jobs_from_html",
                        lambda *a, **k: [{"title": "A"}, {"title": "B"}])
    jobs = job_scraper.scrape_site({"url": "https://x.example/careers", "name": "X"}, {})
    assert [j["title"] for j in jobs] == ["A", "B"]


def test_scrape_site_uses_api_jobs_when_present(monkeypatch):
    import job_scraper
    monkeypatch.setattr(job_scraper, "fetch_via_api", lambda *a, **k: [{"title": "API"}])
    monkeypatch.setattr(job_scraper, "try_static_fetch",
                        lambda url: (_ for _ in ()).throw(AssertionError("should not scrape")))
    jobs = job_scraper.scrape_site({"url": "https://x.example/careers", "name": "X"}, {})
    assert [j["title"] for j in jobs] == ["API"]
    assert jobs[0]["via_api"] is True  # lets matches_location ignore the page URL's location filter


# --- sharp-drop detection ---

def test_detect_count_drop_flags_sharp_fall():
    import db
    assert db.detect_count_drop([20, 22, 18, 21], 6) == {"baseline": 20, "count": 6}


def test_detect_count_drop_ignores_normal_small_zero_and_short_history():
    import db
    assert db.detect_count_drop([20, 22, 18, 21], 15) is None   # mild dip
    assert db.detect_count_drop([20, 22, 18, 21], 0) is None    # empty is reported separately
    assert db.detect_count_drop([20, 22], 2) is None            # not enough history
    assert db.detect_count_drop([4, 4, 4, 4], 1) is None        # tiny site


# ---------------------------------------------------------------------------
# External (ATS) job IDs
# ---------------------------------------------------------------------------

def test_external_id_scoped_by_company():
    a = {"title": "Dev", "link": "https://x.com/1", "site": "Acme", "external_id": "42"}
    b = {"title": "Dev", "link": "https://x.com/1", "site": "Globex", "external_id": "42"}
    assign_job_ids([a, b])
    assert a["id"] != b["id"]  # same external ID, different company


def test_external_id_survives_link_and_title_change():
    a = {"title": "Dev", "link": "https://x.com/old", "site": "Acme", "external_id": "42"}
    b = {"title": "Senior Dev", "link": "https://x.com/new", "site": "Acme ", "external_id": "42"}
    assign_job_ids([a]); assign_job_ids([b])
    assert a["id"] == b["id"]


def test_external_id_keeps_legacy_link_id():
    j = {"title": "Dev", "link": "https://x.com/1", "site": "Acme", "external_id": "42"}
    plain = {"title": "Dev", "link": "https://x.com/1", "site": "Acme"}
    assign_job_ids([j]); assign_job_ids([plain])
    assert j["legacy_id"] == plain["id"] and j["id"] != plain["id"]


def test_job_helper_includes_external_id_only_when_given():
    from api_fetchers import _job
    assert _job("t", "l", "s", external_id=7)["external_id"] == "7"
    assert "external_id" not in _job("t", "l", "s")


def test_local_store_rename_job_ids(tmp_path, monkeypatch):
    from job_store import LocalFileJobStore
    store = LocalFileJobStore({})
    monkeypatch.setattr(store, "_save_seen_jobs", lambda: None)
    store._seen_jobs = {"old": {"title": "x"}}
    store.rename_job_ids({"old": "new"})
    assert list(store._seen_jobs) == ["new"]


# --- listing pages, 429, empty descriptions ---------------------------------

def test_is_listing_title_detects_category_pages():
    from job_scraper import is_listing_title
    assert is_listing_title("משרות DevOps")
    assert is_listing_title("Backend jobs")
    assert not is_listing_title("DevOps Engineer")

def test_fetch_via_api_raises_when_rate_limited(monkeypatch):
    import api_fetchers
    def _boom(url, name):
        raise RuntimeError("429 Client Error: TOO MANY REQUESTS")
    monkeypatch.setattr(api_fetchers, "_handler_for", lambda url: _boom)
    import pytest
    with pytest.raises(RuntimeError, match="rate-limited"):
        api_fetchers.fetch_via_api("https://x.example/jobs", "Nope", None)


def test_jobposting_description_from_json_ld():
    import job_scraper
    body = "Build things. " * 40
    html = ('<html><script type="application/ld+json">{"@type":"WebSite"}</script>'
            '<script type="application/ld+json">{"@type":"JobPosting","description":"<p>' + body + '</p>"}</script></html>')
    assert job_scraper._jobposting_description(html).startswith("Build things.")
    assert job_scraper._jobposting_description("<html></html>") == ""


def test_salesforce_description_falls_back_to_selenium(monkeypatch):
    import job_scraper
    seen = []
    monkeypatch.setattr(job_scraper, "try_static_fetch", lambda url: None)
    monkeypatch.setattr(job_scraper, "_selenium_description", lambda link: (seen.append(link), "8+ years of experience")[1])
    link = "https://careers.salesforce.com/company/careers/jobs/JR368460/lead-backend-software-engineer/"
    assert job_scraper.fetch_job_description({"link": link, "title": "x"}, {}) == "8+ years of experience"
    other = job_scraper.fetch_job_description({"link": "https://example.com/job/1", "title": "x"}, {})
    assert other == "" and len(seen) == 1  # other hosts never launch a browser
