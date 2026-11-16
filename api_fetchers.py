"""
Direct API fetchers for career sites.

Most career pages are front-ends for a handful of applicant-tracking systems
(Workday, Greenhouse, Comeet, Eightfold, Oracle HCM, ...) that expose a public
JSON endpoint. Hitting that endpoint is faster and more reliable than driving
a browser, and returns structured title / location / date fields.

fetch_via_api() returns:
  - a list of jobs (possibly empty) when a handler recognised the URL and the
    API answered — an empty list is a real "no openings" answer;
  - None when no handler matches or the API call failed, so the caller can
    fall back to HTML / Selenium scraping.

Each job has the same shape as scraper output:
  {"title", "link", "site", "location", "posted_date"}
"""

import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
_TIMEOUT = 20
_MAX_PAGES = 100  # safety cap on pagination per site (Eightfold pages hold only 10 jobs: NVIDIA alone has ~400 in Israel)


def _job(title, link, site, location="", posted_date="", external_id=None):
    """Normalized job. external_id is the ATS's own posting ID when the API
    provides one (job_scraper hashes it together with the company name)."""
    job = {
        "title": (title or "").strip(),
        "link": link or "",
        "site": site,
        "location": (location or "").strip(),
        "posted_date": posted_date or "",
    }
    if external_id not in (None, ""):
        job["external_id"] = str(external_id).strip()
    return job


def _request(method, url, **kwargs):
    """requests.request with one retry after a short pause on HTTP 429."""
    r = requests.request(method, url, **kwargs)
    if r.status_code == 429:
        time.sleep(3)
        r = requests.request(method, url, **kwargs)
    return r


def _query(url: str) -> dict:
    return {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ---------------------------------------------------------------------------
# Workday  (*.myworkdayjobs.com)
# ---------------------------------------------------------------------------

def _fetch_workday(url, site_name):
    p = urlparse(url)
    tenant = p.netloc.split(".")[0]
    # Path is /[locale/]<site>[/...]; the locale looks like "en-US".
    parts = [s for s in p.path.split("/") if s]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Za-z]{2}", parts[0]):
        parts = parts[1:]
    if not parts:
        return None
    board = parts[0]
    base = f"{p.scheme}://{p.netloc}"
    api = f"{base}/wday/cxs/{tenant}/{board}/jobs"

    # Facet filters in the page URL (e.g. ?locationCountry=<id>) map 1:1 to
    # appliedFacets. Without any, search for "Israel" so we don't page through
    # a global job list.
    q = parse_qs(p.query)
    facets = {k: v for k, v in q.items() if k != "q"}
    search = (q.get("q") or [""])[0]
    if not facets and not search:
        search = "Israel"

    jobs, offset = [], 0
    for _ in range(_MAX_PAGES):
        r = _request(
            "POST", api,
            json={"limit": 20, "offset": offset, "searchText": search, "appliedFacets": facets},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings") or []
        for j in postings:
            jobs.append(_job(
                j.get("title"),
                f"{base}/en-US/{board}{j.get('externalPath', '')}",
                site_name,
                j.get("locationsText", ""),
                j.get("postedOn", ""),
                # bulletFields[0] is the requisition number; externalPath's slug
                # changes whenever the title does
                (j.get("bulletFields") or [None])[0] or j.get("externalPath"),
            ))
        offset += len(postings)
        if not postings or offset >= data.get("total", 0):
            break
    return jobs


# ---------------------------------------------------------------------------
# Greenhouse  (boards.greenhouse.io / job-boards.greenhouse.io)
# ---------------------------------------------------------------------------

def _fetch_greenhouse(url, site_name):
    parts = [s for s in urlparse(url).path.split("/") if s]
    if not parts:
        return None
    r = _request(
        "GET",
        f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs",
        params={"content": "true"},  # full descriptions, in the same single request
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    jobs = []
    for j in r.json().get("jobs", []):
        job = _job(j.get("title"), j.get("absolute_url"), site_name,
                   (j.get("location") or {}).get("name", ""),
                   (j.get("updated_at") or "")[:10], j.get("id"))
        desc = _html_to_text(j.get("content", ""))
        if desc:
            job["description"] = desc[:5000]
        jobs.append(job)
    return jobs


# ---------------------------------------------------------------------------
# Comeet  (www.comeet.com/jobs/<company>/<uid>)
# ---------------------------------------------------------------------------

def _fetch_comeet(url, site_name):
    parts = [s for s in urlparse(url).path.split("/") if s]
    if len(parts) < 3 or parts[0] != "jobs":
        return None
    uid = parts[2]
    # The careers API needs a per-company token, which is embedded in the
    # public careers page.
    page = requests.get(url, headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=_TIMEOUT)
    page.raise_for_status()
    m = re.search(r'"token"\s*:\s*"([^"]+)"', page.text)
    if not m:
        return None
    r = _request(
        "GET",
        f"https://www.comeet.com/careers-api/2.0/company/{uid}/positions",
        # details=true: the hosted job page is JS-rendered (scraping it yields an empty
        # template), so the description has to come from the API.
        params={"token": m.group(1), "details": "true"},
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    jobs = []
    for pos in r.json():
        loc = pos.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("name")) if x)
        job = _job(
            pos.get("name"),
            pos.get("url_comeet_hosted_page") or pos.get("url_active_page"),
            site_name, location, (pos.get("time_updated") or "")[:10], pos.get("uid"),
        )
        desc = _html_to_text(" ".join(d.get("value") or "" for d in (pos.get("details") or [])))
        if desc:
            job["description"] = desc[:5000]
        jobs.append(job)
    return jobs


def desc_ref_from_link(link: str):
    """A desc_ref for jobs whose link alone identifies the posting (Workday), else None.
    Lets us read descriptions of jobs that came through other front-ends (Cisco's
    Phenom site links straight to Workday) and of jobs already stored in the DB."""
    p = urlparse(link or "")
    if p.netloc.endswith("taleo.net") and p.path.endswith("/jobdetail.ftl") and _query(link).get("job"):
        return {"type": "taleo", "url": link}
    if p.netloc == "jobs.apple.com" and "/details/" in p.path:
        return {"type": "apple", "url": link}
    m = re.search(r"^/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/([^/?#]+)", p.path)
    if m:  # Oracle HCM
        return {"type": "oracle", "base": f"{p.scheme}://{p.netloc}", "site": m.group(1), "id": m.group(2)}
    if not p.netloc.endswith("myworkdayjobs.com"):
        return None
    parts = [s for s in p.path.split("/") if s]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Za-z]{2}", parts[0]):
        parts = parts[1:]
    if parts and parts[-1] == "apply":
        parts = parts[:-1]
    if "job" not in parts or parts.index("job") == 0:
        return None
    i = parts.index("job")
    return {"type": "workday", "base": f"{p.scheme}://{p.netloc}", "tenant": p.netloc.split(".")[0],
            "board": parts[i - 1], "path": "/".join(parts[i:])}


def fetch_description(ref: dict) -> str:
    """Fetch one job's description via the ATS API, given a job's "desc_ref".
    Returns "" if the ATS isn't supported or the request fails."""
    try:
        if ref.get("type") == "apple":
            r = _request("GET", ref["url"], headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=_TIMEOUT)
            r.raise_for_status()
            return _apple_description(r.text)
        if ref.get("type") == "taleo":
            r = _request("GET", ref["url"], headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=_TIMEOUT)
            r.raise_for_status()
            return _taleo_description(r.text)
        if ref.get("type") == "oracle" and ref.get("id"):
            r = _request(
                "GET", f"{ref['base']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                params={"expand": "all", "onlyData": "true",
                        "finder": f'ById;Id="{ref["id"]}",siteNumber={ref["site"]}'},
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            r.raise_for_status()
            item = (r.json().get("items") or [{}])[0]
            return _html_to_text(" ".join(item.get(k) or "" for k in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                "ExternalQualificationsStr")))[:5000]
        if ref.get("type") == "workday":
            r = _request(
                "GET", f"{ref['base']}/wday/cxs/{ref['tenant']}/{ref['board']}/{ref['path']}",
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            r.raise_for_status()
            return _html_to_text((r.json().get("jobPostingInfo") or {}).get("jobDescription", ""))[:5000]
        if ref.get("type") == "eightfold" and ref.get("id"):
            r = _request(
                "GET", f"{ref['base']}/api/pcsx/position_details",
                params={"position_id": ref["id"], "domain": ref["domain"], "hl": "en"},
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            r.raise_for_status()
            return _html_to_text((r.json().get("data") or {}).get("jobDescription", ""))[:5000]
    except Exception:
        pass
    return ""


def _apple_description(page: str) -> str:
    """jobs.apple.com details pages carry the posting in window.__staticRouterHydrationData
    (JSON inside a JS string). Preferred qualifications are left out: they're wishes."""
    m = re.search(r'__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);', page, re.S)
    if not m:
        return ""
    try:
        data = json.loads(json.loads('"' + m.group(1) + '"'))
        job = data["loaderData"]["jobDetails"]["jobsData"]
    except (ValueError, KeyError, TypeError):
        return ""
    return " ".join(str(job.get(k) or "") for k in
                    ("jobSummary", "description", "minimumQualifications")).strip()[:5000]


def _taleo_description(page: str) -> str:
    """Taleo's jobdetail.ftl embeds the description as percent-encoded HTML following a
    '!*!' delimiter inside a JS string, not as markup (so a normal page parse sees none)."""
    from urllib.parse import unquote
    seen, parts = set(), []
    for chunk in re.findall(r"!\*!([^!'\"]*%3[Cc][^!'\"]*)", page):
        text = _html_to_text(unquote(chunk.replace("%5C", "")))
        if text and text not in seen:  # the page repeats the text in a printable copy
            seen.add(text)
            parts.append(text)
    return " ".join(parts)[:5000]


def _html_to_text(markup: str) -> str:
    """Strip tags/entities from an HTML fragment returned by an ATS API."""
    import html
    # Some APIs (Greenhouse) return the markup itself HTML-escaped ("&lt;p&gt;"), so
    # unescape once before stripping tags and once after for the remaining entities.
    markup = html.unescape(markup or "") if "&lt;" in (markup or "") else (markup or "")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


# ---------------------------------------------------------------------------
# Eightfold  (*.eightfold.ai and custom domains with /careers)
# ---------------------------------------------------------------------------

def _fetch_eightfold(url, site_name):
    p = urlparse(url)
    q = _query(url)
    if not q.get("domain"):
        return None
    return _eightfold(f"{p.scheme}://{p.netloc}", q["domain"], q.get("query", ""), site_name)


def _eightfold(base, domain, query, site_name):
    jobs, start = [], 0
    for _ in range(_MAX_PAGES):
        r = _request(
            "GET", f"{base}/api/pcsx/search",
            params={"domain": domain, "query": query,
                    "location": "Israel", "start": start},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("data") or {}
        positions = data.get("positions") or []
        for pos in positions:
            posted = ""
            if pos.get("postedTs"):
                posted = datetime.fromtimestamp(pos["postedTs"], timezone.utc).strftime("%Y-%m-%d")
            job = _job(
                pos.get("name"), f"{base}{pos.get('positionUrl', '')}", site_name,
                ", ".join(pos.get("locations") or []), posted, pos.get("id"),
            )
            # The job page is a JS app (a plain fetch yields nothing useful) and the
            # list has no description — keep a reference so the text can be pulled
            # from the details endpoint later, only for the jobs that need it.
            job["desc_ref"] = {"type": "eightfold", "base": base, "domain": domain, "id": pos.get("id")}
            jobs.append(job)
        start += len(positions)
        if not positions or start >= data.get("count", 0):
            break
    return jobs


# ---------------------------------------------------------------------------
# Oracle HCM Candidate Experience  (*.oraclecloud.com/hcmUI/CandidateExperience)
# ---------------------------------------------------------------------------

def _fetch_oracle(url, site_name):
    p = urlparse(url)
    m = re.search(r"/sites/([^/]+)", p.path)
    if not m:
        return None
    site = m.group(1)
    q = _query(url)
    return _oracle(f"{p.scheme}://{p.netloc}", site,
                   q.get("locationid") or q.get("selectedlocationsfacet"),
                   q.get("keyword"), site_name)


def _oracle(base, site, location_id, keyword, site_name):
    finder_parts = [f"siteNumber={site}", "limit=25"]
    if location_id:
        finder_parts.append(f"locationId={location_id}")
    if keyword:
        finder_parts.append(f"keyword={keyword}")
    finder_parts.append("sortBy=POSTING_DATES_DESC")

    jobs, offset = [], 0
    for _ in range(_MAX_PAGES):
        r = _request(
        "GET",
            f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
            params={"onlyData": "true", "expand": "requisitionList",
                    "finder": "findReqs;" + ",".join(finder_parts + [f"offset={offset}"])},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        items = r.json().get("items") or []
        item = items[0] if items else {}
        reqs = item.get("requisitionList") or []
        for req in reqs:
            job = _job(
                req.get("Title"),
                f"{base}/hcmUI/CandidateExperience/en/sites/{site}/job/{req.get('Id')}",
                site_name, req.get("PrimaryLocation", ""), req.get("PostedDate", ""),
                req.get("Id"),
            )
            # The list has no description (and the job page is a JS app): pull it
            # from the requisition-details endpoint later, only for jobs that need it.
            job["desc_ref"] = {"type": "oracle", "base": base, "site": site, "id": req.get("Id")}
            jobs.append(job)
        offset += len(reqs)
        if not reqs or offset >= item.get("TotalJobsCount", 0):
            break
    return jobs


# ---------------------------------------------------------------------------
# Amazon  (amazon.jobs)
# ---------------------------------------------------------------------------

def _fetch_amazon(url, site_name):
    jobs, offset = [], 0
    for _ in range(_MAX_PAGES):
        r = _request(
        "GET",
            "https://www.amazon.jobs/en/search.json",
            params={"country": "ISR", "result_limit": 100, "offset": offset,
                    "sort": "recent"},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        batch = data.get("jobs") or []
        for j in batch:
            job = _job(
                j.get("title"), f"https://www.amazon.jobs{j.get('job_path', '')}",
                site_name, j.get("normalized_location") or j.get("location", ""),
                j.get("posted_date", ""), j.get("id_icims") or j.get("id"),
            )
            # The job page is JS-rendered, but the list carries the text. Only the
            # *basic* qualifications ("3+ years of ...") are requirements; the
            # preferred ones are wishes and would wrongly reject junior roles.
            desc = _html_to_text(" ".join(
                (j.get(k) or "") for k in ("description", "basic_qualifications")))
            if desc:
                job["description"] = desc[:5000]
            jobs.append(job)
        offset += len(batch)
        if not batch or offset >= data.get("hits", 0):
            break
    return jobs


# ---------------------------------------------------------------------------
# LinkedIn  (public guest endpoint, no login)
# ---------------------------------------------------------------------------

def _fetch_linkedin(url, site_name):
    from bs4 import BeautifulSoup

    q = _query(url)
    params = {k: q[k] for k in ("keywords", "geoid", "f_c", "f_tpr", "f_e") if k in q}
    # LinkedIn's parameter names are case-sensitive.
    params = {{"geoid": "geoId", "f_c": "f_C", "f_tpr": "f_TPR", "f_e": "f_E"}.get(k, k): v
              for k, v in params.items()}
    jobs, seen = [], set()
    for page in range(3):
        params["start"] = page * 10
        r = requests.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            params=params, headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=_TIMEOUT,
        )
        if r.status_code == 429:
            break  # rate limited — keep what we have
        r.raise_for_status()
        cards = BeautifulSoup(r.text, "html.parser").select("li")
        if not cards:
            break
        for card in cards:
            a = card.select_one("a.base-card__full-link")
            title = card.select_one(".base-search-card__title")
            if not a or not title:
                continue
            link = a.get("href", "").split("?")[0]
            if link in seen:
                continue
            seen.add(link)
            company = card.select_one(".base-search-card__subtitle")
            loc = card.select_one(".job-search-card__location")
            when = card.select_one("time")
            jobs.append(_job(
                title.get_text(strip=True), link,
                company.get_text(strip=True) if company else site_name,
                loc.get_text(strip=True) if loc else "",
                when.get("datetime", "") if when else "",
                (re.search(r"(\d{6,})/?$", link) or [None, None])[1],
            ))
    if not jobs:
        return None  # nothing parsed — let the browser scraper try
    return jobs


# ---------------------------------------------------------------------------
# Lever  (jobs.lever.co / jobs.eu.lever.co)
# ---------------------------------------------------------------------------

def _fetch_lever(url, site_name):
    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]
    if not parts:
        return None
    api = "api.eu.lever.co" if ".eu." in p.netloc else "api.lever.co"
    r = _request("GET", f"https://{api}/v0/postings/{parts[0]}", params={"mode": "json"},
                 headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    return [
        _job(j.get("text"), j.get("hostedUrl"), site_name,
             (j.get("categories") or {}).get("location", ""),
             datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).strftime("%Y-%m-%d")
             if j.get("createdAt") else "", j.get("id"))
        for j in r.json()
    ]


# ---------------------------------------------------------------------------
# Generic JSON endpoint  (config "api": {"type": "json", ...})
# ---------------------------------------------------------------------------

def _dig(obj, path):
    """Follow a dotted path ("location.name") through dicts / list indexes."""
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
        if obj is None:
            return None
    return obj


def _raw_text(value) -> str:
    """A text field as a string; a list of {"name","value"} sections (Comeet) is joined."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join((v.get("value") or "") if isinstance(v, dict) else str(v) for v in value)
    return str(value)


def _fetch_json(spec, site_name):
    """
    spec keys: url, [method, body, params], list (dotted path to the job array,
    "" for a top-level array), title, link | link_template ("{id}" placeholders
    filled from each job's top-level fields), location (path or list of paths),
    date, [id] (path to the posting's own ID), [site] (path to a per-job company
    name), [paginate] (offset paging).
    """
    method = spec.get("method", "GET").upper()
    page = spec.get("paginate")  # {"param": "start", "total": "count"}: offset paging
    if "boards-api.greenhouse.io" in spec["url"] and not spec.get("description"):
        # Greenhouse returns every job's full text in the list when asked
        spec = {**spec, "params": {**(spec.get("params") or {}), "content": "true"},
                "description": "content"}
    if "careers-api/2.0/company" in spec["url"] and not spec.get("description"):
        # Comeet only includes the job text with details=true (and its hosted pages are JS-rendered)
        spec = {**spec, "params": {**(spec.get("params") or {}), "details": "true"},
                "description": "details"}
    items, offset = [], 0
    for _ in range(_MAX_PAGES if page else 1):
        kwargs = {"headers": _HEADERS, "timeout": _TIMEOUT}
        params = dict(spec.get("params") or {})
        if page:
            params[page["param"]] = offset
        if params:
            kwargs["params"] = params
        if method == "POST":
            kwargs["json"] = spec.get("body", {})
        r = _request(method, spec["url"], **kwargs)
        r.raise_for_status()
        data = r.json()
        batch = _dig(data, spec.get("list", ""))
        if not isinstance(batch, list):
            return None
        items += batch
        offset += len(batch)
        if not page or not batch or offset >= (_dig(data, page["total"]) or 0):
            break
    locs = spec.get("location", [])
    locs = [locs] if isinstance(locs, str) else locs
    jobs = []
    for it in items:
        if spec.get("link_template"):
            link = spec["link_template"].format_map(
                {k: v for k, v in it.items() if isinstance(v, (str, int))})
        else:
            link = _dig(it, spec.get("link", "")) or ""
        loc = ", ".join(str(v) for v in (_dig(it, p) for p in locs) if v)
        job = _job(
            html.unescape(str(_dig(it, spec["title"]) or "")), link,
            (_dig(it, spec["site"]) if spec.get("site") else None) or site_name,
            loc, str(_dig(it, spec["date"]) or "")[:10] if spec.get("date") else "",
            _dig(it, spec["id"]) if spec.get("id") else None,
        )
        if spec.get("desc_ref") and spec.get("id") and _dig(it, spec["id"]):
            # the list has no text; point at the ATS's details endpoint (see fetch_description)
            job["desc_ref"] = {**spec["desc_ref"], "id": _dig(it, spec["id"])}
        if spec.get("description"):  # optional path (or list of paths) to the posting's text
            paths = spec["description"]
            paths = [paths] if isinstance(paths, str) else paths
            desc = _html_to_text(" ".join(_raw_text(_dig(it, p)) for p in paths))
            if desc:
                job["description"] = desc[:5000]
        jobs.append(job)
    return jobs


def _fetch_phenom(page_url, site_name):
    """Phenom People career sites: POST <site>/widgets with ddoKey=refineSearch."""
    p = urlparse(page_url)
    base = f"{p.scheme}://{p.netloc}"
    html_text = _request("GET", page_url, headers={"User-Agent": _HEADERS["User-Agent"]},
                         timeout=_TIMEOUT).text

    def pick(key):
        m = re.search(r'"%s"\s*:\s*"([^"]*)"' % key, html_text)
        return m.group(1) if m else None

    ref, lang, page_id = pick("refNum"), pick("locale"), pick("pageId")
    if not (ref and lang and page_id):
        return None
    jobs, offset = [], 0
    for _ in range(_MAX_PAGES):
        body = {
            "lang": lang, "deviceType": "desktop", "country": lang.split("_")[-1],
            "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "",
            "subsearch": "", "from": offset, "irs": False, "jobs": True, "counts": True,
            "all_fields": ["category", "country", "state", "city", "type"], "size": 50,
            "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
            "pageId": page_id, "siteType": "external", "keywords": "", "global": True,
            "selected_fields": {"country": ["Israel"]}, "locationData": {}, "refNum": ref,
        }
        r = _request("POST", f"{base}/widgets", json=body,
                     headers={**_HEADERS, "Content-Type": "application/json"}, timeout=_TIMEOUT)
        r.raise_for_status()
        res = r.json().get("refineSearch") or {}
        batch = (res.get("data") or {}).get("jobs") or []
        for j in batch:
            jobs.append(_job(j.get("title"), j.get("applyUrl"), site_name,
                             j.get("cityStateCountry") or j.get("location", ""),
                             (j.get("postedDate") or "")[:10],
                             j.get("jobId") or j.get("jobSeqNo")))
        offset += len(batch)
        if not batch or offset >= (res.get("totalHits") or 0):
            break
    return jobs


def _greenhouse_spec(token):
    return {"type": "json", "url": f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
            "list": "jobs", "title": "title", "link": "absolute_url",
            "location": "location.name", "date": "updated_at", "id": "id"}


def _comeet_spec(uid, token):
    return {"type": "json", "url": f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions",
            "params": {"token": token, "details": "false"}, "list": "", "title": "name",
            "link": "url_active_page", "location": ["location.city", "location.name"],
            "date": "time_updated", "id": "uid"}


def _ashby_spec(board):
    return {"type": "json", "url": f"https://api.ashbyhq.com/posting-api/job-board/{board}",
            "list": "jobs", "title": "title", "link": "jobUrl", "location": "location",
            "date": "publishedAt", "id": "id", "description": "descriptionPlain"}


def _fetch_taleo(spec, site_name):
    """Oracle Taleo career sections: POST /careersection/rest/jobboard/searchjobs."""
    base, section = spec["base"], spec.get("section", "ex")
    body = {
        "multilineEnabled": False,
        "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
        "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
        "filterSelectionParam": {"searchFilterSelections": [{"id": "LOCATION", "selectedValues": []}]},
        "advancedSearchFiltersSelectionParam": {"searchFilterSelections": [{"id": "LOCATION", "selectedValues": []}]},
    }
    jobs, page = [], 1
    for _ in range(_MAX_PAGES):
        r = _request("POST", f"{base}/careersection/rest/jobboard/searchjobs",
                     params={"lang": "en", "portal": spec.get("portal", "101430233")},
                     json={**body, "pageNo": page},
                     headers={**_HEADERS, "Content-Type": "application/json", "tz": "GMT+02:00"},
                     timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        reqs = data.get("requisitionList")
        if reqs is None:
            return None
        for req in reqs:
            col = req.get("column") or ["", "", ""]
            loc = col[1].strip('[]').replace('"', '') if len(col) > 1 else ""
            jobs.append(_job(col[0], f"{base}/careersection/{section}/jobdetail.ftl?job={req.get('contestNo')}",
                             site_name, loc, col[2] if len(col) > 2 else "",
                             req.get("contestNo")))
        paging = data.get("pagingData") or {}
        if not reqs or page * paging.get("pageSize", 25) >= paging.get("totalCount", 0):
            break
        page += 1
    return jobs


def _fetch_csod(spec, site_name):
    """Cornerstone OnDemand career sites: anonymous token from the page + rec-job-search."""
    base, site_id = spec["base"], spec.get("site_id", 2)
    page = _request("GET", f"{base}/ux/ats/careersite/{site_id}/home?c={spec['corp']}",
                    headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=_TIMEOUT).text
    m = re.search(r'"token":"(eyJ[\w.-]+)"', page)
    if not m:
        return None
    jobs, number = [], 1
    for _ in range(_MAX_PAGES):
        r = _request("POST", "https://us-galaxy.api.csod.com/rec-job-search/external/jobs",
                     json={"careerSiteId": site_id, "careerSitePageId": site_id,
                           "pageNumber": number, "pageSize": 25, "cultureId": 1,
                           "searchText": "", "cultureName": "en-US", "states": [],
                           "countryCodes": spec.get("countries", ["il"]), "cities": [],
                           "placeID": "", "radius": None, "postingsWithinDays": None,
                           "customCSSEnabled": False, "units": "mi",
                           "customFieldCheckboxKeys": [], "customFieldDropdowns": [],
                           "customFieldRadios": []},
                     headers={**_HEADERS, "Authorization": "Bearer " + m.group(1),
                              "Content-Type": "application/json"}, timeout=_TIMEOUT)
        r.raise_for_status()
        data = (r.json().get("data") or {})
        reqs = data.get("requisitions") or []
        for req in reqs:
            loc = ", ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x)
                            for l in req.get("locations") or [])
            jobs.append(_job(
                req.get("displayJobTitle"),
                f"{base}/ux/ats/careersite/{site_id}/home/requisition/{req.get('requisitionId')}?c={spec['corp']}",
                site_name, loc, req.get("postingEffectiveDate", ""),
                req.get("requisitionId")))
        if not reqs or number * 25 >= data.get("totalCount", 0):
            break
        number += 1
    return jobs


def _fetch_awsm(spec, site_name):
    """WordPress "WP Job Openings" plugin: admin-ajax action=jobfilter returns HTML cards."""
    from bs4 import BeautifulSoup
    p = urlparse(spec["url"])
    r = _request("POST", f"{p.scheme}://{p.netloc}/wp-admin/admin-ajax.php",
                 data={"action": "jobfilter", "listings_per_page": "200",
                       "awsm_job_spec[job-type]": ""},
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=_TIMEOUT)  # WAF rejects the longer UA
    r.raise_for_status()
    jobs = []
    for card in BeautifulSoup(r.text, "html.parser").select(".awsm-job-listing-item"):
        a = card.select_one(".awsm-job-post-title a")
        if not a:
            continue
        loc = card.select_one(".awsm-job-specification-job-location .awsm-job-specification-term")
        jobs.append(_job(a.get_text(strip=True), a.get("href"), site_name,
                         loc.get_text(strip=True) if loc else ""))
    return jobs


def _fetch_from_spec(spec, site_name):
    kind = spec.get("type")
    if kind == "json":
        return _fetch_json(spec, site_name)
    if kind == "eightfold":
        return _eightfold(spec["base"], spec["domain"], spec.get("query", ""), site_name)
    if kind == "taleo":
        return _fetch_taleo(spec, site_name)
    if kind == "csod":
        return _fetch_csod(spec, site_name)
    if kind == "awsm":
        return _fetch_awsm(spec, site_name)
    if kind == "greenhouse":
        return _fetch_json(_greenhouse_spec(spec["token"]), site_name)
    if kind == "comeet":
        return _fetch_json(_comeet_spec(spec["uid"], spec["token"]), site_name)
    if kind == "ashby":
        return _fetch_json(_ashby_spec(spec["board"]), site_name)
    # Specs that just pin a URL-driven handler to a site (same as auto-detection)
    url_handlers = {
        "workday": _fetch_workday, "comeet_page": _fetch_comeet,
        "greenhouse_page": _fetch_greenhouse, "eightfold_page": _fetch_eightfold,
        "oracle_page": _fetch_oracle, "lever": _fetch_lever,
        "amazon": _fetch_amazon, "linkedin": _fetch_linkedin,
    }
    if kind in url_handlers:
        return url_handlers[kind](spec["url"], site_name)
    if kind == "phenom":
        return _fetch_phenom(spec["url"], site_name)
    if kind == "oracle":
        return _oracle(spec["base"], spec["site"], spec.get("location_id"),
                       spec.get("keyword"), site_name)
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _handler_for(url: str):
    host = urlparse(url).netloc.lower()
    if host.endswith(".myworkdayjobs.com"):
        return _fetch_workday
    if host.endswith("greenhouse.io"):
        return _fetch_greenhouse
    if host.endswith("comeet.com") and "/jobs/" in url:
        return _fetch_comeet
    if host.endswith("eightfold.ai") or ("domain=" in url and "/careers" in url):
        return _fetch_eightfold
    if host.endswith("oraclecloud.com") and "/CandidateExperience/" in url:
        return _fetch_oracle
    if host.endswith("lever.co"):
        return _fetch_lever
    if host.endswith("amazon.jobs"):
        return _fetch_amazon
    if host.endswith("linkedin.com") and "/jobs/search" in url:
        return _fetch_linkedin
    return None


_SITE_APIS_FILE = Path(__file__).with_name("site_apis.json")


def _load_site_apis() -> dict:
    """Per-site API specs keyed by site name (shared by Supabase and local-file modes)."""
    try:
        return json.loads(_SITE_APIS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fetch_via_api(url: str, site_name: str, api: dict | None = None) -> list[dict] | None:
    """
    Fetch jobs via the site's ATS / internal API, or None if unsupported or
    failed. The spec comes from `api` or site_apis.json (looked up by site
    name); without one the handler is picked from the URL.
    """
    api = api or _load_site_apis().get(site_name)
    if api:
        handler = lambda u, n: _fetch_from_spec(api, n)  # noqa: E731
        label = f"{api.get('type')}-spec"
    else:
        handler = _handler_for(url)
        if handler is None:
            return None
        label = handler.__name__[7:]
    try:
        jobs = handler(url, site_name)
    except Exception as e:
        logger.warning(f"  [{site_name}] API fetch failed ({label}): {e}")
        return None
    if jobs is not None:
        logger.info(f"  [{site_name}] API ({label}): {len(jobs)} jobs")
    return jobs
