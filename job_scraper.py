"""
Job Scraper v2 - Monitors 90+ career pages for jobs matching your keywords.
Sends email notifications when new matching jobs are found.

Usage:
    python job_scraper.py              # Run a scan
    python job_scraper.py --setup      # Interactive setup wizard
    python job_scraper.py --test-email # Send a test email
    python job_scraper.py --reset      # Clear seen jobs and rescan
    python job_scraper.py --list       # Show all configured sites
"""

import json
import os
import sys
import time
import logging
import argparse
import smtplib
import hashlib
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
SEEN_JOBS_FILE = SCRIPT_DIR / "seen_jobs.json"
LAST_CLEANUP_FILE = SCRIPT_DIR / "last_cleanup.json"
CV_FILE = SCRIPT_DIR / "cv_skills.json"  # Cached parsed CV skills
LOG_FILE = SCRIPT_DIR / "scraper.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    logger.error(f"Config file not found: {CONFIG_FILE}")
    sys.exit(1)


def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    logger.info(f"Config saved to {CONFIG_FILE}")


def load_seen_jobs() -> dict:
    if SEEN_JOBS_FILE.exists():
        with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen_jobs(seen: dict):
    with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)

def send_email(config: dict, subject: str, body_html: str):
    email_cfg = config["email"]
    if not email_cfg.get("enabled"):
        logger.info("Email disabled – skipping.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_cfg["sender_email"]
    msg["To"] = email_cfg["recipient_email"]
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(email_cfg["sender_email"], email_cfg["sender_password"])
            server.sendmail(email_cfg["sender_email"], email_cfg["recipient_email"], msg.as_string())
        logger.info(f"Email sent to {email_cfg['recipient_email']}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")

# ---------------------------------------------------------------------------
# Selenium driver
# ---------------------------------------------------------------------------

_driver_pool = []


def get_driver(headless: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except ImportError:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(30)
    return driver

# ---------------------------------------------------------------------------
# Scraping strategies
# ---------------------------------------------------------------------------

def try_static_fetch(url: str) -> str | None:
    """Try fetching page with requests first (faster than Selenium)."""
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def extract_jobs_from_html(html: str, site_name: str, base_url: str) -> list[dict]:
    """Extract job listings from raw HTML using BeautifulSoup."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    seen_titles = set()

    # Date-related patterns for extracting posting dates
    _date_patterns = [
        # ISO dates: 2025-01-15
        r'\b(\d{4}-\d{1,2}-\d{1,2})\b',
        # US dates: Jan 15, 2025 or January 15, 2025
        r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b',
        # European dates: 15 Jan 2025
        r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b',
        # Relative: "2 days ago", "1 week ago", "Posted today"
        r'\b(\d+\s+(?:day|week|month|hour)s?\s+ago)\b',
        r'\b(today|yesterday)\b',
        # Slash dates: 01/15/2025 or 15/01/2025
        r'\b(\d{1,2}/\d{1,2}/\d{4})\b',
        # Dot dates: 15.01.2025
        r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b',
    ]

    def _find_date_near(element):
        """Try to find a posting date near a job element."""
        # Check common date/location containers near the job link
        containers_to_check = []

        # Check parent, grandparent, great-grandparent
        parent = element.parent
        for _ in range(4):
            if parent:
                containers_to_check.append(parent)
                parent = parent.parent

        # Check sibling elements
        if element.parent:
            containers_to_check.extend(element.parent.find_all(True, recursive=False))

        for container in containers_to_check:
            if not container:
                continue
            # Look for date-specific elements
            date_els = container.select(
                '[class*="date"], [class*="time"], [class*="posted"], '
                '[class*="publish"], [class*="created"], '
                'time, [datetime], [data-date], '
                'span[class*="meta"], div[class*="meta"]'
            )
            for date_el in date_els:
                # Check datetime attribute first
                dt_attr = date_el.get("datetime") or date_el.get("data-date") or ""
                if dt_attr:
                    return dt_attr.strip()
                text = date_el.get_text(strip=True)
                if text and len(text) < 60:
                    for pattern in _date_patterns:
                        m = re.search(pattern, text, re.IGNORECASE)
                        if m:
                            return m.group(1)

            # Also check raw text of the container (but keep it tight)
            container_text = container.get_text(" ", strip=True)
            if len(container_text) < 300:
                for pattern in _date_patterns:
                    m = re.search(pattern, container_text, re.IGNORECASE)
                    if m:
                        return m.group(1)

        return ""

    def _find_location_near(element):
        """Try to find a location near a job element."""
        containers_to_check = []
        parent = element.parent
        for _ in range(4):
            if parent:
                containers_to_check.append(parent)
                parent = parent.parent
        if element.parent:
            containers_to_check.extend(element.parent.find_all(True, recursive=False))

        for container in containers_to_check:
            if not container:
                continue
            loc_els = container.select(
                '[class*="location"], [class*="place"], [class*="city"], '
                '[class*="region"], [class*="office"], [data-location]'
            )
            for loc_el in loc_els:
                loc_attr = loc_el.get("data-location") or ""
                if loc_attr:
                    return loc_attr.strip()
                text = loc_el.get_text(strip=True)
                if text and len(text) < 100:
                    return text
        return ""

    # Common selectors for job listings across many ATS platforms
    selectors = [
        # Greenhouse
        "div.opening a",
        "table.job-board a",
        # Lever
        ".posting-title a", ".posting a.posting-title",
        # Comeet
        ".position-name a", ".career-listing a",
        # TalentBrew (Palo Alto, Intuit, etc.)
        "#search-results-list a[href*='job']",
        "li[class*='job'] a",
        # General patterns
        "a[href*='/job/']", "a[href*='/jobs/']",
        "a[href*='/position/']", "a[href*='/positions/']",
        "a[href*='/career']",
        "a[href*='apply']",
        "[class*='job-title'] a",
        "[class*='job-listing'] a",
        "[class*='position'] a",
        "[class*='opening'] a",
        "[class*='vacancy'] a",
        "h2 a", "h3 a", "h4 a",
    ]

    for selector in selectors:
        try:
            elements = soup.select(selector)
            for el in elements:
                title = el.get_text(strip=True)
                link = el.get("href", "")

                # Make relative URLs absolute
                if link and not link.startswith("http"):
                    parsed = urlparse(base_url)
                    link = f"{parsed.scheme}://{parsed.netloc}{link}"

                # Filter: must have a reasonable title
                if title and 5 < len(title) < 200 and title not in seen_titles:
                    # Skip navigation / non-job links
                    skip_words = ["blog", "about", "contact", "privacy", "terms",
                                  "login", "sign up", "cookie", "home", "menu",
                                  "learn more", "read more", "view all", "see all",
                                  "follow us", "powered by"]
                    if any(sw in title.lower() for sw in skip_words):
                        continue
                    seen_titles.add(title)

                    # Extract date and location from nearby elements
                    posted_date = _find_date_near(el)
                    location = _find_location_near(el)

                    jobs.append({
                        "title": title,
                        "link": link,
                        "site": site_name,
                        "posted_date": posted_date,
                        "location": location,
                    })
        except Exception:
            continue

    return jobs


def scrape_with_selenium(url: str, site_name: str, headless: bool = True, timeout: int = 15) -> list[dict]:
    """Use Selenium for JS-rendered pages."""
    from selenium.webdriver.common.by import By

    driver = None
    jobs = []

    try:
        driver = get_driver(headless)
        driver.get(url)
        time.sleep(timeout)

        # Scroll to load lazy content
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 800);")
            time.sleep(1)

        # Check for Comeet iframe
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "comeet" in src.lower():
                logger.debug(f"  [{site_name}] Switching to Comeet iframe")
                driver.switch_to.frame(iframe)
                time.sleep(3)
                break

        # Try to click "Show More" / "Load More" buttons
        try:
            for btn_text in ["Show More", "Load More", "See All", "View All", "show more"]:
                buttons = driver.find_elements(By.XPATH, f"//*[contains(text(), '{btn_text}')]")
                for btn in buttons:
                    try:
                        btn.click()
                        time.sleep(2)
                    except Exception:
                        pass
        except Exception:
            pass

        # Get full page HTML and parse it
        page_html = driver.page_source
        jobs = extract_jobs_from_html(page_html, site_name, url)

        # Fallback: grab all visible text links
        if not jobs:
            all_links = driver.find_elements(By.TAG_NAME, "a")
            seen = set()
            for el in all_links:
                try:
                    title = el.text.strip()
                    link = el.get_attribute("href") or ""
                    if title and 10 < len(title) < 200 and title not in seen:
                        seen.add(title)
                        jobs.append({"title": title, "link": link, "site": site_name})
                except Exception:
                    continue

    except Exception as e:
        logger.warning(f"  [{site_name}] Selenium error: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return jobs


def scrape_google_jobs(url: str, site_name: str, headless: bool = True, timeout: int = 15) -> list[dict]:
    """Special scraper for Google Jobs results (requires Selenium)."""
    from selenium.webdriver.common.by import By

    driver = None
    jobs = []

    try:
        driver = get_driver(headless)
        driver.get(url)
        time.sleep(timeout)

        # Scroll to load more results
        for _ in range(5):
            driver.execute_script("window.scrollBy(0, 600);")
            time.sleep(1)

        # Google Jobs uses li elements with job cards
        # Try multiple selectors for Google Jobs
        job_elements = driver.find_elements(By.CSS_SELECTOR, "li.iFjolb")
        if not job_elements:
            job_elements = driver.find_elements(By.CSS_SELECTOR, "[data-ved] .BjJfJf")
        if not job_elements:
            job_elements = driver.find_elements(By.CSS_SELECTOR, ".gws-plugins-horizon-jobs__li-ed")

        seen_titles = set()
        for el in job_elements:
            try:
                # Try to get title
                title_el = el.find_elements(By.CSS_SELECTOR, ".BjJfJf, .sH3zFd, h2, .tJ9zfc")
                title = title_el[0].text.strip() if title_el else ""

                # Try to get company
                company_el = el.find_elements(By.CSS_SELECTOR, ".vNEEBe, .nJlQNd, .company_location .companyName")
                company = company_el[0].text.strip() if company_el else ""

                # Try to get location
                location_el = el.find_elements(By.CSS_SELECTOR, ".Qk80Jf, .nJlQNd:nth-child(2), .companyLocation")
                location = location_el[0].text.strip() if location_el else ""

                # Try to get date
                date_el = el.find_elements(By.CSS_SELECTOR, ".SuWscb, .LL4CDc")
                posted_date = date_el[0].text.strip() if date_el else ""

                if title and title not in seen_titles:
                    seen_titles.add(title)
                    # Build a display name with company
                    display_site = f"{company}" if company else site_name
                    jobs.append({
                        "title": title,
                        "link": url,  # Google Jobs doesn't easily expose direct links
                        "site": display_site,
                        "location": location,
                        "posted_date": posted_date,
                    })
            except Exception:
                continue

        # Fallback: parse the page source
        if not jobs:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(driver.page_source, "html.parser")

            # Look for job title patterns in Google Jobs
            for el in soup.select("li .BjJfJf, li .sH3zFd, [role='treeitem'] div"):
                title = el.get_text(strip=True)
                if title and 5 < len(title) < 150 and title not in seen_titles:
                    seen_titles.add(title)
                    jobs.append({
                        "title": title,
                        "link": url,
                        "site": site_name,
                        "location": "",
                        "posted_date": "",
                    })

    except Exception as e:
        logger.warning(f"  [{site_name}] Google Jobs scraping error: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return jobs


def scrape_site(url_entry: dict, config: dict) -> list[dict]:
    """Scrape a single site using the best available method."""
    url = url_entry["url"]
    site_name = url_entry.get("name", urlparse(url).netloc)
    headless = config.get("headless", True)
    timeout = config.get("page_load_timeout", 15)

    logger.info(f"Scraping: {site_name}")

    # Special handler for Google Jobs
    if "google.com/search" in url and "udm=8" in url:
        jobs = scrape_google_jobs(url, site_name, headless, timeout)
        logger.info(f"  [{site_name}] Found {len(jobs)} raw job entries")
        return jobs

    # Strategy 1: Try static fetch first (much faster)
    html = try_static_fetch(url)
    jobs = []
    if html:
        jobs = extract_jobs_from_html(html, site_name, url)

    # Strategy 2: If static didn't work well, use Selenium
    if len(jobs) < 2:
        selenium_jobs = scrape_with_selenium(url, site_name, headless, timeout)
        if len(selenium_jobs) > len(jobs):
            jobs = selenium_jobs

    logger.info(f"  [{site_name}] Found {len(jobs)} raw job entries")
    return jobs

# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _whole_word_match(text: str, keyword: str) -> bool:
    """
    Check if keyword appears as a whole word in text.
    'intern' matches 'Software Intern' but NOT 'Internal Tools'.
    Uses word boundaries (\\b) so punctuation/hyphens/spaces count as boundaries.
    """
    pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
    return bool(re.search(pattern, text.lower()))


def matches_role(title: str, role_keywords: list[str]) -> list[str]:
    """
    Check if a job title contains a ROLE keyword (student/intern) as a whole word.
    The job MUST match at least one of these — this is mandatory.
    """
    return [kw for kw in role_keywords if _whole_word_match(title, kw)]


def matches_field(title: str, field_keywords: list[str]) -> list[str]:
    """
    Check if a job title is engineering-related (whole word match).
    The job MUST match at least one of these — this is mandatory.
    """
    return [kw for kw in field_keywords if _whole_word_match(title, kw)]


def matches_exclude(title: str, exclude_keywords: list[str]) -> list[str]:
    """
    Check if a job title contains any EXCLUDED keywords (whole word match).
    If any match, the job should be REJECTED.
    """
    return [kw for kw in exclude_keywords if _whole_word_match(title, kw)]


def matches_job(title: str, role_keywords: list[str], field_keywords: list[str],
                exclude_keywords: list[str] = None) -> dict | None:
    """
    A job matches ONLY if ALL conditions are met:
      1. Title contains a role keyword (student / intern / internship)  — whole word
      2. Title contains an engineering field keyword                    — whole word
      3. Title does NOT contain any exclude keyword (senior, manager)   — whole word
    Returns matched keywords dict or None.
    """
    # Check exclusions first (fast reject)
    if exclude_keywords:
        excluded = matches_exclude(title, exclude_keywords)
        if excluded:
            return None

    matched_roles = matches_role(title, role_keywords)
    matched_fields = matches_field(title, field_keywords)

    if matched_roles and matched_fields:
        return {
            "roles": matched_roles,
            "fields": matched_fields,
            "all": matched_roles + matched_fields,
        }
    return None


def matches_location(job: dict, location_filters: list[str]) -> bool:
    """
    Check if a job is in Israel.
    - If confirmed Israel → accept
    - If explicitly foreign (USA, UK, etc.) → reject
    - If unknown → accept (permissive)
    """
    if not location_filters:
        return True

    # Check all available text from the job
    raw_text = " ".join([
        job.get("title", ""),
        job.get("link", ""),
        job.get("location", ""),
        job.get("site", ""),
    ]).lower()
    # Normalize: replace hyphens, underscores, URL encoding with spaces
    # so "United-Kingdom" and "New%20York" become "united kingdom" and "new york"
    text_to_check = raw_text.replace("-", " ").replace("_", " ").replace("%20", " ").replace("+", " ")

    # If the source URL already has Israel/location filter, assume it's relevant
    url_indicators = [
        "israel", "tel-aviv", "tel%20aviv", "herzliya",
        "/il/", "/he/", "country=il", "geoId=101620260",
        "/en-il/", "haifa", "petach", "petah", "hod-hasharon",
        "hod%20hasharon", "beer-sheva", "jerusalem", "ramat-gan",
        "kfar-saba", "kefar-sava", "yokneam", "ra'anana", "raanana",
        "rehovot", "netanya", "modiin", "locationId=30000000",
        "region%5B%5D=TA", "location=telaviv",
    ]
    source_url_lower = job.get("source_url", "").lower()
    if any(ind in source_url_lower for ind in url_indicators):
        return True

    # Check if any Israeli location appears in job info
    for loc in location_filters:
        if loc.lower() in text_to_check:
            return True

    # REJECT if explicitly foreign — known non-Israel locations
    foreign_indicators = [
        # Countries
        "usa", "united states", "united kingdom", "uk", "canada", "germany",
        "france", "india", "china", "japan", "australia", "brazil", "spain",
        "italy", "netherlands", "ireland", "singapore", "south korea", "mexico",
        "poland", "romania", "czech", "hungary", "portugal", "sweden", "norway",
        "finland", "denmark", "switzerland", "austria", "belgium", "greece",
        "turkey", "argentina", "colombia", "chile", "philippines", "vietnam",
        "thailand", "malaysia", "indonesia", "taiwan", "hong kong",
        # US states (abbreviations after comma — e.g. "Sandy, UT")
        ", al", ", ak", ", az", ", ar", ", ca", ", co", ", ct", ", de", ", fl",
        ", ga", ", hi", ", id", ", il", ", in", ", ia", ", ks", ", ky", ", la",
        ", me", ", md", ", ma", ", mi", ", mn", ", ms", ", mo", ", mt", ", ne",
        ", nv", ", nh", ", nj", ", nm", ", ny", ", nc", ", nd", ", oh", ", ok",
        ", or", ", pa", ", ri", ", sc", ", sd", ", tn", ", tx", ", ut", ", vt",
        ", va", ", wa", ", wv", ", wi", ", wy",
        # Major foreign cities
        "new york", "san francisco", "los angeles", "seattle", "austin",
        "boston", "chicago", "denver", "atlanta", "dallas", "houston",
        "miami", "phoenix", "san jose", "san diego", "portland", "detroit",
        "london", "manchester", "berlin", "munich", "paris", "amsterdam",
        "dublin", "barcelona", "madrid", "milan", "rome", "vienna",
        "zurich", "geneva", "stockholm", "oslo", "copenhagen", "helsinki",
        "warsaw", "prague", "budapest", "bucharest", "lisbon", "brussels",
        "toronto", "vancouver", "montreal", "sydney", "melbourne",
        "bangalore", "hyderabad", "mumbai", "delhi", "pune", "chennai",
        "singapore", "tokyo", "shanghai", "beijing", "seoul",
        "sao paulo", "mexico city", "buenos aires",
    ]
    for foreign in foreign_indicators:
        if foreign in text_to_check:
            return False

    # PERMISSIVE: if we can't determine location, include it (better to include than miss)
    return True


def job_id(job: dict) -> str:
    raw = f"{job['site']}|{job['title']}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()

# ---------------------------------------------------------------------------
# CV Parsing & Job Fit Scoring
# ---------------------------------------------------------------------------

# Common tech skills and keywords to look for in CVs and job descriptions
_TECH_SKILLS = {
    # Programming languages
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang",
    "rust", "ruby", "php", "swift", "kotlin", "scala", "r", "matlab", "perl",
    "bash", "shell", "powershell", "sql", "html", "css", "assembly",
    # Frameworks & libraries
    "react", "angular", "vue", "node.js", "nodejs", "express", "django", "flask",
    "fastapi", "spring", "spring boot", ".net", "asp.net", "rails", "laravel",
    "next.js", "nextjs", "nuxt", "svelte", "tailwind", "bootstrap",
    "tensorflow", "pytorch", "keras", "scikit-learn", "pandas", "numpy",
    "matplotlib", "opencv", "selenium",
    # Databases
    "mysql", "postgresql", "postgres", "mongodb", "redis", "elasticsearch",
    "dynamodb", "cassandra", "sqlite", "oracle", "sql server", "neo4j",
    "firebase", "supabase",
    # Cloud & DevOps
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "k8s",
    "terraform", "ansible", "jenkins", "ci/cd", "cicd", "github actions",
    "gitlab", "circleci", "helm", "prometheus", "grafana", "datadog",
    "cloudformation", "serverless", "lambda", "ec2", "s3",
    # Tools & concepts
    "git", "linux", "unix", "rest", "restful", "graphql", "grpc",
    "microservices", "api", "apis", "agile", "scrum", "jira", "confluence",
    "kafka", "rabbitmq", "celery", "nginx", "apache",
    # Data & ML
    "machine learning", "deep learning", "nlp", "natural language processing",
    "computer vision", "data science", "data engineering", "data analysis",
    "big data", "spark", "hadoop", "airflow", "etl", "data pipeline",
    "statistics", "a/b testing", "ab testing",
    # Security
    "cybersecurity", "penetration testing", "siem", "soc", "firewall",
    "encryption", "oauth", "jwt", "ssl", "tls",
    # Soft skills & concepts
    "algorithms", "data structures", "oop", "object oriented",
    "design patterns", "system design", "distributed systems",
    "multithreading", "concurrency", "networking", "tcp/ip",
    "operating systems", "embedded", "firmware", "rtos",
    "qa", "testing", "unit testing", "integration testing", "automation",
    # Degrees & fields
    "computer science", "software engineering", "electrical engineering",
    "information systems", "mathematics", "physics", "data science",
    "bsc", "b.sc", "msc", "m.sc", "phd", "bachelor", "master",
}


def parse_cv(cv_path: str) -> dict:
    """
    Parse a CV file (PDF, DOCX, or TXT) and extract skills and keywords.
    Returns a dict with 'skills' (set), 'raw_text' (str), and 'file' (str).
    """
    cv_path = Path(cv_path)
    if not cv_path.exists():
        logger.warning(f"CV file not found: {cv_path}")
        return {"skills": set(), "raw_text": "", "file": str(cv_path)}

    raw_text = ""
    ext = cv_path.suffix.lower()

    try:
        if ext == ".pdf":
            raw_text = _read_pdf(cv_path)
        elif ext in (".docx", ".doc"):
            raw_text = _read_docx(cv_path)
        elif ext == ".txt":
            raw_text = cv_path.read_text(encoding="utf-8", errors="replace")
        else:
            logger.warning(f"Unsupported CV format: {ext}. Use PDF, DOCX, or TXT.")
            return {"skills": set(), "raw_text": "", "file": str(cv_path)}
    except Exception as e:
        logger.error(f"Error reading CV: {e}")
        return {"skills": set(), "raw_text": "", "file": str(cv_path)}

    # Extract skills from CV text
    skills = _extract_skills(raw_text)
    logger.info(f"📄 CV parsed: {len(skills)} skills found from {cv_path.name}")
    logger.info(f"   Skills: {', '.join(sorted(skills)[:20])}{'...' if len(skills) > 20 else ''}")

    return {"skills": skills, "raw_text": raw_text, "file": str(cv_path)}


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF file."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except ImportError:
        pass

    # Fallback: pdfplumber
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text
    except ImportError:
        logger.error("Install PyMuPDF or pdfplumber to parse PDFs: pip install PyMuPDF --break-system-packages")
        return ""


def _read_docx(path: Path) -> str:
    """Extract text from a DOCX file."""
    try:
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    except ImportError:
        logger.error("Install python-docx to parse DOCX: pip install python-docx --break-system-packages")
        return ""


def _extract_skills(text: str) -> set:
    """Extract recognized skills/technologies from text."""
    text_lower = text.lower()
    found = set()
    for skill in _TECH_SKILLS:
        # Use word boundary matching for short skills to avoid false positives
        if len(skill) <= 2:
            if re.search(r'\b' + re.escape(skill) + r'\b', text_lower):
                found.add(skill)
        else:
            if skill in text_lower:
                found.add(skill)
    return found


def load_cv_skills(config: dict) -> dict | None:
    """
    Load CV skills — from cache if available, otherwise parse the CV.
    Returns None if no CV is configured.
    """
    cv_path = config.get("cv_path", "")
    if not cv_path:
        return None

    cv_full_path = Path(cv_path)
    if not cv_full_path.is_absolute():
        cv_full_path = SCRIPT_DIR / cv_path

    if not cv_full_path.exists():
        logger.warning(f"CV file not found: {cv_full_path}")
        return None

    # Check if we have a cached version that's still fresh
    if CV_FILE.exists():
        try:
            with open(CV_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            # Re-parse if CV file changed
            cv_mtime = cv_full_path.stat().st_mtime
            if cached.get("cv_mtime") == cv_mtime:
                cached["skills"] = set(cached["skills"])
                return cached
        except Exception:
            pass

    # Parse CV fresh
    cv_data = parse_cv(cv_full_path)
    if not cv_data["skills"]:
        return cv_data

    # Cache it
    cache = {
        "skills": list(cv_data["skills"]),
        "raw_text": cv_data["raw_text"],
        "file": cv_data["file"],
        "cv_mtime": cv_full_path.stat().st_mtime,
    }
    with open(CV_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    return cv_data


def fetch_job_description(job: dict, config: dict) -> str:
    """
    Fetch the full job description from the job's link.
    Tries static fetch first, then Selenium if needed.
    """
    link = job.get("link", "")
    if not link or not link.startswith("http"):
        return ""

    try:
        # Try static fetch (fast)
        html = try_static_fetch(link)
        if html:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Remove nav, header, footer, scripts
            for tag in soup.select("nav, header, footer, script, style, noscript"):
                tag.decompose()

            # Try to find the job description container
            desc_selectors = [
                "[class*='job-description']", "[class*='jobDescription']",
                "[class*='job-detail']", "[class*='jobDetail']",
                "[class*='description']", "[class*='posting-']",
                "[class*='content']", "[id*='job-description']",
                "[id*='jobDescription']", "[data-automation*='jobDescription']",
                "article", "main", ".job-post", ".posting-page",
            ]
            for sel in desc_selectors:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(" ", strip=True)
                    if len(text) > 100:
                        return text[:5000]  # Cap at 5000 chars

            # Fallback: get body text
            body = soup.find("body")
            if body:
                text = body.get_text(" ", strip=True)
                if len(text) > 100:
                    return text[:5000]

    except Exception as e:
        logger.debug(f"  Could not fetch job description from {link}: {e}")

    return ""


def calculate_fit_score(cv_data: dict, job_description: str, job_title: str) -> dict:
    """
    Calculate how well a CV matches a job description.
    Returns dict with 'score' (0-100), 'matched_skills', 'missing_skills'.
    """
    if not cv_data or not cv_data.get("skills"):
        return {"score": 0, "matched_skills": [], "missing_skills": [], "total_job_skills": 0}

    cv_skills = cv_data["skills"]
    job_text = (job_description + " " + job_title).lower()

    # Extract skills from job description
    job_skills = _extract_skills(job_text)

    if not job_skills:
        return {"score": 0, "matched_skills": [], "missing_skills": [], "total_job_skills": 0}

    # Find overlap
    matched = cv_skills & job_skills
    missing = job_skills - cv_skills

    # Score: percentage of job skills that appear in CV
    score = round((len(matched) / len(job_skills)) * 100)

    return {
        "score": score,
        "matched_skills": sorted(matched),
        "missing_skills": sorted(missing),
        "total_job_skills": len(job_skills),
    }


def score_jobs(jobs: list[dict], cv_data: dict, config: dict) -> list[dict]:
    """
    Fetch job descriptions and calculate fit scores for a list of jobs.
    Adds 'fit_score', 'matched_skills', 'missing_skills' to each job dict.
    """
    if not cv_data or not cv_data.get("skills"):
        logger.info("No CV configured — skipping fit scoring.")
        return jobs

    logger.info(f"📊 Scoring {len(jobs)} job(s) against your CV...")

    for i, job in enumerate(jobs):
        try:
            desc = fetch_job_description(job, config)
            if desc:
                fit = calculate_fit_score(cv_data, desc, job.get("title", ""))
                job["fit_score"] = fit["score"]
                job["matched_skills"] = fit["matched_skills"]
                job["missing_skills"] = fit["missing_skills"]
                job["total_job_skills"] = fit["total_job_skills"]
                logger.info(f"  [{i+1}/{len(jobs)}] {job['title']} @ {job['site']} → {fit['score']}% fit ({len(fit['matched_skills'])}/{fit['total_job_skills']} skills)")
            else:
                job["fit_score"] = -1  # Couldn't fetch description
                job["matched_skills"] = []
                job["missing_skills"] = []
                job["total_job_skills"] = 0
                logger.info(f"  [{i+1}/{len(jobs)}] {job['title']} @ {job['site']} → N/A (couldn't read job page)")

            # Small delay to be polite
            time.sleep(0.5)

        except Exception as e:
            job["fit_score"] = -1
            job["matched_skills"] = []
            job["missing_skills"] = []
            job["total_job_skills"] = 0
            logger.debug(f"  Error scoring {job.get('title', '?')}: {e}")

    # Sort by fit score (highest first), N/A at the end
    jobs.sort(key=lambda j: j.get("fit_score", -1), reverse=True)
    return jobs

def _categorize_job(job: dict) -> str:
    """Categorize a job as 'student' or 'junior' based on matched role keywords."""
    roles = job.get("matched_roles", [])
    for r in roles:
        if r in ("student", "intern", "internship"):
            return "student"
    return "junior"


def build_email_html(new_jobs: list[dict], config: dict,
                     failed_sites: list[str] = None, empty_sites: list[str] = None) -> str:
    role_kws = config.get("role_keywords", [])
    field_kws = config.get("field_keywords", [])
    has_cv = bool(config.get("cv_path"))
    failed_sites = failed_sites or []
    empty_sites = empty_sites or []

    def _score_badge(score):
        """Return an HTML badge for the fit score with color coding."""
        if score < 0:
            return '<span style="background:#f3f4f6;padding:2px 8px;border-radius:4px;font-size:11px;color:#6b7280;">N/A</span>'
        if score >= 75:
            color, bg = "#166534", "#dcfce7"  # Green
        elif score >= 50:
            color, bg = "#854d0e", "#fef9c3"  # Yellow
        elif score >= 25:
            color, bg = "#9a3412", "#ffedd5"  # Orange
        else:
            color, bg = "#991b1b", "#fee2e2"  # Red
        return f'<span style="background:{bg};padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;color:{color};">{score}%</span>'

    def _build_jobs_table(jobs_list, has_cv):
        """Build an HTML table for a list of jobs."""
        if not jobs_list:
            return ""

        # Sort by fit score if CV configured
        if has_cv:
            jobs_list = sorted(jobs_list, key=lambda j: j.get("fit_score", -1), reverse=True)

        fit_header = ""
        if has_cv:
            fit_header = '<th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e2e8f0;">CV Fit</th>'

        rows = ""
        for job in jobs_list:
            link_html = f'<a href="{job["link"]}" style="color:#2563eb;">Apply →</a>' if job.get("link") else "N/A"
            roles = ", ".join(job.get("matched_roles", []))
            fields = ", ".join(job.get("matched_fields", []))
            posted = job.get("posted_date", "") or "—"

            fit_html = ""
            if has_cv:
                score = job.get("fit_score", -1)
                matched_s = job.get("matched_skills", [])
                missing_s = job.get("missing_skills", [])
                tooltip = ""
                if matched_s or missing_s:
                    tooltip = f"✅ You have: {', '.join(matched_s[:10])}"
                    if missing_s:
                        tooltip += f"\\n❌ Missing: {', '.join(missing_s[:10])}"
                fit_html = f"""
                    <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;" title="{tooltip}">
                        {_score_badge(score)}
                    </td>"""

            rows += f"""
                <tr>
                    <td style="padding:8px 12px;border-bottom:1px solid #eee;">{job['title']}</td>
                    <td style="padding:8px 12px;border-bottom:1px solid #eee;">{job['site']}</td>
                    {fit_html}
                    <td style="padding:8px 12px;border-bottom:1px solid #eee;white-space:nowrap;">
                        <span style="background:#fef3c7;padding:2px 6px;border-radius:4px;font-size:11px;color:#92400e;">{posted}</span>
                    </td>
                    <td style="padding:8px 12px;border-bottom:1px solid #eee;">
                        <span style="background:#dcfce7;padding:2px 6px;border-radius:4px;font-size:11px;color:#166534;">{roles}</span>
                        <span style="background:#e0f2fe;padding:2px 6px;border-radius:4px;font-size:11px;color:#1e40af;">{fields}</span>
                    </td>
                    <td style="padding:8px 12px;border-bottom:1px solid #eee;">{link_html}</td>
                </tr>"""

        return f"""
        <table style="border-collapse:collapse;width:100%;font-size:14px;">
            <thead>
                <tr style="background:#f8fafc;">
                    <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Job Title</th>
                    <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Company</th>
                    {fit_header}
                    <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Posted</th>
                    <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Keywords</th>
                    <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Link</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>"""

    # Split jobs into categories
    student_jobs = [j for j in new_jobs if _categorize_job(j) == "student"]
    junior_jobs = [j for j in new_jobs if _categorize_job(j) == "junior"]

    # Build sections
    jobs_section = ""

    if student_jobs:
        jobs_section += f"""
        <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;margin-bottom:20px;">
            <h2 style="margin:0 0 12px;font-size:18px;color:#166534;">🎓 Student / Intern Positions ({len(student_jobs)})</h2>
            {_build_jobs_table(student_jobs, has_cv)}
        </div>"""

    if junior_jobs:
        jobs_section += f"""
        <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:16px;margin-bottom:20px;">
            <h2 style="margin:0 0 12px;font-size:18px;color:#1e40af;">🚀 Junior / Entry-Level Positions ({len(junior_jobs)})</h2>
            {_build_jobs_table(junior_jobs, has_cv)}
        </div>"""

    # Skills breakdown for all jobs with CV
    skills_detail = ""
    if has_cv and new_jobs:
        all_sorted = sorted(new_jobs, key=lambda j: j.get("fit_score", -1), reverse=True)
        detail_rows = ""
        for job in all_sorted:
            score = job.get("fit_score", -1)
            if score < 0:
                continue
            matched_s = job.get("matched_skills", [])
            missing_s = job.get("missing_skills", [])
            matched_html = ", ".join(f'<span style="background:#dcfce7;padding:1px 4px;border-radius:3px;font-size:10px;color:#166534;">{s}</span>' for s in matched_s[:8])
            missing_html = ", ".join(f'<span style="background:#fee2e2;padding:1px 4px;border-radius:3px;font-size:10px;color:#991b1b;">{s}</span>' for s in missing_s[:8])
            if not matched_html and not missing_html:
                continue
            category_icon = "🎓" if _categorize_job(job) == "student" else "🚀"
            detail_rows += f"""
            <tr>
                <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;font-size:12px;">{category_icon} {job['title'][:40]}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;font-size:12px;">{_score_badge(score)}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;font-size:11px;">{matched_html or "—"}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;font-size:11px;">{missing_html or "—"}</td>
            </tr>"""

        if detail_rows:
            skills_detail = f"""
            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-top:16px;">
                <h3 style="margin:0 0 12px;font-size:15px;color:#334155;">📊 Skills Breakdown</h3>
                <table style="border-collapse:collapse;width:100%;font-size:12px;">
                    <thead>
                        <tr>
                            <th style="padding:6px 8px;text-align:left;border-bottom:1px solid #cbd5e1;">Job</th>
                            <th style="padding:6px 8px;text-align:center;border-bottom:1px solid #cbd5e1;">Score</th>
                            <th style="padding:6px 8px;text-align:left;border-bottom:1px solid #cbd5e1;">✅ Your Skills</th>
                            <th style="padding:6px 8px;text-align:left;border-bottom:1px solid #cbd5e1;">❌ Missing</th>
                        </tr>
                    </thead>
                    <tbody>{detail_rows}</tbody>
                </table>
            </div>"""

    if jobs_section:
        jobs_section += skills_detail
        jobs_section += f"""
        <p style="margin-top:12px;color:#999;font-size:11px;">
            ℹ️ Posted date shown when available. "—" means the site doesn't display a date.
            {' CV Fit shows % of job skills matching your CV.' if has_cv else ''}
        </p>"""

    # Build problem sites section
    problem_section = ""
    if failed_sites or empty_sites:
        problem_rows = ""
        for name in failed_sites:
            problem_rows += f"""
            <tr>
                <td style="padding:6px 12px;border-bottom:1px solid #fee2e2;">{name}</td>
                <td style="padding:6px 12px;border-bottom:1px solid #fee2e2;">
                    <span style="background:#fee2e2;padding:2px 8px;border-radius:4px;font-size:11px;color:#991b1b;">❌ Crashed / Error</span>
                </td>
            </tr>"""
        for name in empty_sites:
            problem_rows += f"""
            <tr>
                <td style="padding:6px 12px;border-bottom:1px solid #fef3c7;">{name}</td>
                <td style="padding:6px 12px;border-bottom:1px solid #fef3c7;">
                    <span style="background:#fef3c7;padding:2px 8px;border-radius:4px;font-size:11px;color:#92400e;">⚠️ 0 jobs found</span>
                </td>
            </tr>"""

        total_problems = len(failed_sites) + len(empty_sites)
        problem_section = f"""
        <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;margin-top:24px;">
            <h3 style="margin:0 0 12px;font-size:16px;color:#991b1b;">⚠️ {total_problems} Site(s) Could Not Be Accessed</h3>
            <p style="margin:0 0 12px;font-size:13px;color:#7f1d1d;">These companies' career pages were unreachable or returned no jobs. You may want to check them manually.</p>
            <table style="border-collapse:collapse;width:100%;font-size:13px;">
                <thead>
                    <tr>
                        <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #fca5a5;">Company</th>
                        <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #fca5a5;">Issue</th>
                    </tr>
                </thead>
                <tbody>{problem_rows}</tbody>
            </table>
        </div>"""

    # Count by category for the header
    student_count = len([j for j in new_jobs if _categorize_job(j) == "student"])
    junior_count = len([j for j in new_jobs if _categorize_job(j) == "junior"])
    category_summary = []
    if student_count:
        category_summary.append(f"🎓 {student_count} Student/Intern")
    if junior_count:
        category_summary.append(f"🚀 {junior_count} Junior/Entry-Level")
    category_text = " | ".join(category_summary) if category_summary else ""

    return f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a1a;max-width:950px;margin:0 auto;padding:20px;">
        <div style="background:linear-gradient(135deg,#2563eb,#7c3aed);color:white;padding:24px;border-radius:12px;margin-bottom:20px;">
            <h1 style="margin:0;font-size:24px;">🔔 {len(new_jobs)} New Engineering Job(s) in Israel!</h1>
            <p style="margin:8px 0 0;opacity:0.9;">{category_text}</p>
            <p style="margin:4px 0 0;opacity:0.7;font-size:13px;">{datetime.now().strftime('%B %d, %Y %H:%M')}</p>
        </div>
        {jobs_section}
        {problem_section}
        <p style="margin-top:16px;color:#999;font-size:11px;">
            Sent by Job Scraper v2 • {datetime.now().strftime('%Y-%m-%d %H:%M')}
        </p>
    </body>
    </html>
    """

# ---------------------------------------------------------------------------
# Stale job cleanup (once per day)
# ---------------------------------------------------------------------------

def should_run_cleanup(interval_hours: int = 24) -> bool:
    """Check if enough time has passed since last cleanup."""
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


def save_cleanup_timestamp():
    with open(LAST_CLEANUP_FILE, "w") as f:
        json.dump({"last_cleanup": datetime.now().isoformat()}, f)


def cleanup_stale_jobs(seen_jobs: dict, currently_live_ids: set, config: dict):
    """
    Remove jobs from seen_jobs that are no longer found on any career page.
    Only runs once per day (controlled by cleanup_interval_hours).
    This prevents re-notifying about jobs that reappear, while keeping
    memory clean of jobs that have been filled/removed.
    """
    interval = config.get("cleanup_interval_hours", 24)
    if not should_run_cleanup(interval):
        return

    logger.info("🧹 Running daily stale job cleanup...")

    if not currently_live_ids:
        logger.info("  No live jobs found this scan — skipping cleanup to avoid false purge.")
        save_cleanup_timestamp()
        return

    stale_ids = []
    for jid, info in list(seen_jobs.items()):
        if jid not in currently_live_ids:
            stale_ids.append(jid)

    if stale_ids:
        for jid in stale_ids:
            title = seen_jobs[jid].get("title", "?")
            site = seen_jobs[jid].get("site", "?")
            logger.info(f"  🗑️ Removing stale: {title} @ {site}")
            del seen_jobs[jid]
        save_seen_jobs(seen_jobs)
        logger.info(f"  Cleaned up {len(stale_ids)} stale job(s). {len(seen_jobs)} remaining.")
    else:
        logger.info("  No stale jobs to clean up.")

    save_cleanup_timestamp()


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def run_scraper():
    config = load_config()
    role_keywords = config.get("role_keywords", ["student", "intern", "internship"])
    field_keywords = config.get("field_keywords", ["software", "engineer"])
    exclude_keywords = config.get("exclude_keywords", [])
    location_filters = config.get("location_filter", [])

    # Decide data source: Supabase or local files
    try:
        import db
        use_db = db.is_configured()
    except ImportError:
        use_db = False

    if use_db:
        logger.info("📦 Using Supabase database")
        urls = db.get_all_sites()
        seen_job_ids = db.get_seen_job_ids()
        if not urls:
            logger.error("No sites found in database! Run migrate_to_supabase.py first.")
            return []
    else:
        logger.info("📁 Using local files")
        urls = config["urls"]
        seen_jobs = load_seen_jobs()
        seen_job_ids = set(seen_jobs.keys())

    logger.info("=" * 60)
    logger.info(f"Job Scraper v2 — Scanning {len(urls)} sites")
    logger.info(f"Role filter (must match ≥1): {role_keywords}")
    logger.info(f"Field filter (must match ≥1): {field_keywords}")
    logger.info(f"Exclude filter (reject if any): {exclude_keywords}")
    logger.info(f"Location filter: Israel")
    logger.info("=" * 60)

    all_new_jobs = []
    total_raw = 0
    total_matching = 0
    errors = 0
    failed_sites = []       # Sites that threw exceptions
    empty_sites = []        # Sites that returned 0 raw jobs (likely broken)
    currently_live_ids = set()  # Track job IDs found in this scan
    existing_seen_ids = []  # Track IDs of jobs we've already seen

    # --- Parallel scraping with ThreadPoolExecutor ---
    max_workers = config.get("max_workers", 5)
    logger.info(f"🚀 Scraping {len(urls)} sites with {max_workers} parallel workers...")

    def _scrape_one(url_entry):
        """Scrape a single site — runs in a thread."""
        try:
            jobs = scrape_site(url_entry, config)
            for j in jobs:
                j["source_url"] = url_entry["url"]
            return {"url_entry": url_entry, "jobs": jobs, "error": None}
        except Exception as e:
            return {"url_entry": url_entry, "jobs": [], "error": str(e)}

    # Run scraping in parallel
    scrape_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_scrape_one, entry): entry for entry in urls}
        for future in as_completed(futures):
            scrape_results.append(future.result())

    # --- Process results sequentially ---
    for result in scrape_results:
        url_entry = result["url_entry"]
        jobs = result["jobs"]
        error = result["error"]

        if error:
            errors += 1
            failed_sites.append(url_entry.get("name", url_entry["url"]))
            logger.error(f"  ❌ [{url_entry.get('name', '?')}] Error: {error}")
            if use_db:
                db.update_site_status(url_entry.get("name", "?"), "error", 0)
            continue

        total_raw += len(jobs)

        # Update site status in DB
        if use_db:
            status = "ok" if jobs else "empty"
            db.update_site_status(url_entry["name"], status, len(jobs))

        # Track sites that returned 0 jobs
        if len(jobs) == 0:
            empty_sites.append(url_entry.get("name", url_entry["url"]))

        # Filter: must match ROLE + FIELD + LOCATION
        matching = []
        for job in jobs:
            match = matches_job(job["title"], role_keywords, field_keywords, exclude_keywords)
            if match:
                job["matched_keywords"] = match["all"]
                job["matched_roles"] = match["roles"]
                job["matched_fields"] = match["fields"]
                if matches_location(job, location_filters):
                    matching.append(job)

        total_matching += len(matching)

        # Track all currently live job IDs (for cleanup)
        for job in matching:
            jid = job_id(job)
            job["id"] = jid  # Store ID on the job dict
            currently_live_ids.add(jid)

        # Find new jobs (not seen before)
        new_jobs = []
        for job in matching:
            jid = job["id"]
            if jid not in seen_job_ids:
                new_jobs.append(job)
                seen_job_ids.add(jid)  # Prevent duplicates within same scan
            else:
                existing_seen_ids.append(jid)

            # Update local seen_jobs dict if using files
            if not use_db:
                if jid not in seen_jobs:
                    seen_jobs[jid] = {
                        "title": job["title"],
                        "site": job["site"],
                        "first_seen": datetime.now().isoformat(),
                        "last_seen": datetime.now().isoformat(),
                    }
                else:
                    seen_jobs[jid]["last_seen"] = datetime.now().isoformat()

        if new_jobs:
            logger.info(f"  ✅ [{url_entry.get('name')}] {len(new_jobs)} NEW matching job(s)")
            all_new_jobs.extend(new_jobs)
        else:
            logger.info(f"  [{url_entry.get('name')}] {len(matching)} matching, 0 new")

    # Save data
    if use_db:
        db.save_new_jobs(all_new_jobs)
        db.update_last_seen(existing_seen_ids)
        db.delete_stale_jobs(currently_live_ids)
    else:
        save_seen_jobs(seen_jobs)
        if config.get("cleanup_stale_jobs", True):
            cleanup_stale_jobs(seen_jobs, currently_live_ids, config)

    # Summary
    logger.info("=" * 60)
    logger.info(f"SCAN COMPLETE: {total_raw} raw → {total_matching} matching → {len(all_new_jobs)} NEW")
    logger.info(f"Sites scanned: {len(urls)} | Errors: {errors} | Empty (0 jobs): {len(empty_sites)}")
    logger.info("=" * 60)

    # Report problematic sites
    if failed_sites:
        logger.warning(f"⚠️  FAILED SITES ({len(failed_sites)}) — crashed or blocked:")
        for name in failed_sites:
            logger.warning(f"     ❌ {name}")

    if empty_sites:
        logger.warning(f"⚠️  EMPTY SITES ({len(empty_sites)}) — returned 0 jobs (may be broken/blocked):")
        for name in empty_sites:
            logger.warning(f"     ⚠️  {name}")

    # --- CV Fit Scoring ---
    cv_data = load_cv_skills(config)
    if all_new_jobs and cv_data and cv_data.get("skills"):
        all_new_jobs = score_jobs(all_new_jobs, cv_data, config)

    # Send notification
    problem_sites = failed_sites + empty_sites
    if all_new_jobs or problem_sites:
        logger.info(f"🎉 Sending email with {len(all_new_jobs)} new job(s), {len(problem_sites)} problem site(s)...")
        email_html = build_email_html(all_new_jobs, config, failed_sites, empty_sites)
        subject_parts = []
        if all_new_jobs:
            subject_parts.append(f"🔔 {len(all_new_jobs)} New Job(s)")
        if problem_sites:
            subject_parts.append(f"⚠️ {len(problem_sites)} Site(s) Failed")
        send_email(config, " | ".join(subject_parts) + " – Job Scraper", email_html)
        for job in all_new_jobs:
            logger.info(f"  NEW: {job['title']} @ {job['site']}")
    else:
        logger.info("No new matching jobs found this scan.")

    return all_new_jobs


def setup_wizard():
    print("\n" + "=" * 60)
    print("  JOB SCRAPER v2 — Setup Wizard")
    print("=" * 60 + "\n")

    config = load_config()

    # Keywords
    print(f"Current keywords: {config['keywords']}")
    kw_input = input("Enter keywords (comma-separated) or press Enter to keep:\n> ").strip()
    if kw_input:
        config["keywords"] = [k.strip().lower() for k in kw_input.split(",") if k.strip()]

    # Email
    print("\n--- Email Setup ---")
    print("For Gmail: use an App Password (https://myaccount.google.com/apppasswords)\n")
    config["email"]["sender_email"] = input(f"Sender email [{config['email'].get('sender_email', '')}]: ").strip() or config["email"]["sender_email"]
    pw = input("App password (hidden): ").strip()
    if pw:
        config["email"]["sender_password"] = pw
    config["email"]["recipient_email"] = input(f"Recipient email [{config['email'].get('recipient_email', '')}]: ").strip() or config["email"]["recipient_email"]
    config["email"]["enabled"] = bool(config["email"]["sender_email"] and config["email"]["sender_password"])

    save_config(config)
    print(f"\n✅ Configuration saved! {len(config['urls'])} sites configured.")
    print("   Run: python job_scraper.py --test-email")


def test_email():
    config = load_config()
    test_jobs = [{
        "title": "Software Engineer Intern (Test)",
        "site": "Test Company",
        "link": "https://example.com/job/123",
        "matched_keywords": ["intern", "software", "engineer"],
        "matched_roles": ["intern"],
        "matched_fields": ["software", "engineer"],
        "posted_date": "Feb 10, 2026",
    }]
    html = build_email_html(test_jobs, config)
    send_email(config, "🧪 Test Email – Job Scraper v2", html)
    print("Test email sent! Check your inbox.")


def list_sites():
    config = load_config()
    print(f"\n📋 Configured Sites ({len(config['urls'])} total):\n")
    for i, u in enumerate(config["urls"], 1):
        print(f"  {i:3d}. {u.get('name', 'Unnamed'):30s}  {u['url'][:70]}")
    print()

def show_status():
    """Show failed and empty sites from the last run by reading the log."""
    log_file = Path(__file__).parent / "scraper.log"
    if not log_file.exists():
        print("No log file found. Run the scraper first.")
        return

    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()

    # Find the last scan's summary
    last_scan_start = 0
    for i, line in enumerate(lines):
        if "Job Scraper v2" in line and "Scanning" in line:
            last_scan_start = i

    recent_lines = lines[last_scan_start:]

    failed = []
    empty = []
    summary = ""
    for line in recent_lines:
        if "FAILED SITES" in line or ("❌" in line and "FAILED" not in line and "Error:" not in line):
            # Collect failed site names from the warning lines
            if "❌" in line and "FAILED" not in line:
                name = line.split("❌")[-1].strip()
                if name:
                    failed.append(name)
        if "EMPTY SITES" in line or ("⚠️" in line and "EMPTY" not in line and "FAILED" not in line):
            if "⚠️" in line and "EMPTY" not in line and "FAILED" not in line:
                name = line.split("⚠️")[-1].strip()
                if name:
                    empty.append(name)
        if "SCAN COMPLETE" in line:
            summary = line.split("]")[-1].strip() if "]" in line else line.strip()

    print("\n" + "=" * 60)
    print("  LAST SCAN STATUS")
    print("=" * 60)

    if summary:
        print(f"\n  {summary}")

    if failed:
        print(f"\n  ❌ FAILED SITES ({len(failed)}) — crashed or blocked:")
        for name in failed:
            print(f"     • {name}")
    else:
        print("\n  ✅ No crashed sites")

    if empty:
        print(f"\n  ⚠️  EMPTY SITES ({len(empty)}) — returned 0 jobs (may be broken):")
        for name in empty:
            print(f"     • {name}")
    else:
        print("\n  ✅ All sites returned jobs")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Scraper v2")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--test-email", action="store_true", help="Send test email")
    parser.add_argument("--reset", action="store_true", help="Clear seen jobs")
    parser.add_argument("--list", action="store_true", help="List all configured sites")
    parser.add_argument("--status", action="store_true", help="Show failed/empty sites from last run")
    args = parser.parse_args()

    if args.setup:
        setup_wizard()
    elif args.test_email:
        test_email()
    elif args.reset:
        if SEEN_JOBS_FILE.exists():
            SEEN_JOBS_FILE.unlink()
        print("✅ Seen jobs cleared. Next scan will treat all jobs as new.")
    elif args.list:
        list_sites()
    elif args.status:
        show_status()
    else:
        run_scraper()