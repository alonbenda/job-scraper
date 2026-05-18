"""
Unit tests for Job Scraper.
Run with: python -m pytest tests.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from job_scraper import (
    _whole_word_match,
    matches_role,
    matches_field,
    matches_exclude,
    matches_job,
    matches_location,
    job_id,
    extract_jobs_from_html,
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


# ===========================================================================
# HTML extraction
# ===========================================================================

def test_extract_from_link_tags():
    html = '<html><body><a href="/jobs/123">Software Engineering Intern</a><a href="/about">About Us</a></body></html>'
    jobs = extract_jobs_from_html(html, "TestCompany", "https://example.com")
    titles = [j["title"] for j in jobs]
    assert any("Software Engineering Intern" in t for t in titles)

def test_empty_html_returns_empty():
    jobs = extract_jobs_from_html("", "TestCompany", "https://example.com")
    assert jobs == []

def test_no_jobs_in_html():
    html = "<html><body><p>Welcome to our site</p></body></html>"
    jobs = extract_jobs_from_html(html, "TestCompany", "https://example.com")
    assert jobs == []
