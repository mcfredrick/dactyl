#!/usr/bin/env python3
"""Job board scrapers. Each function returns list[dict] with title, url, text, company."""

import sys
from typing import Any

from config import BOT_USER_AGENT

TIMEOUT = 20

JOBS_SOURCES = {"greentownlabs", "linkedin", "exa", "climatebase", "hn"}

# Queries for LinkedIn job search: role term × climate/sustainability term.
# Covers the full breadth of the candidate's background — ML, audio/video, systems, backend.
# LLM scorer filters false positives; breadth here is intentional.
_LINKEDIN_QUERIES = [
    # ML / AI
    "machine learning climate",
    "machine learning clean energy",
    "machine learning renewable energy",
    "machine learning carbon",
    "machine learning sustainability",
    "MLOps climate",
    "AI engineer climate",
    # Data / backend engineering
    "data engineer climate",
    "data engineer clean energy",
    "software engineer climate tech",
    "backend engineer clean energy",
    "platform engineer sustainability",
    # Audio / video / media
    "audio engineer climate",
    "video engineer sustainability",
    # Systems / C++
    "C++ engineer clean energy",
    "systems engineer climate",
    # Broader climate tech engineering
    "software engineer renewable energy",
    "software engineer carbon",
    "engineer energy storage",
    "software engineer agriculture tech",
    "software engineer food tech",
    # Product-adjacent / FDE / solutions engineering
    "forward deployed engineer climate",
    "solutions engineer climate tech",
    "applied AI engineer climate",
    "technical solutions engineer sustainability",
    "AI solutions architect clean energy",
    "staff engineer product climate",
]


def _name(x: Any) -> str:
    """Extract a display name from a string or a {name: ...} dict."""
    return x.get("name", str(x)) if isinstance(x, dict) else str(x)


def greentownlabs_jobs() -> list[dict]:
    """Fetch remote job listings from Greentown Labs member companies via Consider API."""
    import httpx

    try:
        r = httpx.post(
            "https://jobs.greentownlabs.com/api-boards/search-jobs",
            json={
                "meta": {"size": 200},
                "board": {"id": "greentown-labs", "isParent": True},
                "query": {},
                "grouped": False,
                "parentSlug": "greentown-labs",
            },
            headers={
                "User-Agent": BOT_USER_AGENT,
                "Accept": "application/json",
                "Referer": "https://jobs.greentownlabs.com/jobs",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"  greentownlabs fetch failed: {e}", file=sys.stderr)
        return []

    results = []
    seen: set[str] = set()
    for job in r.json().get("jobs", []):
        if not job.get("remote"):
            continue
        url = job.get("applyUrl") or job.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        company = job.get("companyName", "")
        depts = ", ".join(_name(d) for d in job.get("departments", [])) if job.get("departments") else ""
        skills = ", ".join(_name(s) for s in job.get("skills", [])[:5]) if job.get("skills") else ""
        text = " — ".join(filter(None, [company, depts, skills, "Remote"]))
        results.append({
            "title": job.get("title", "").strip(),
            "url": url,
            "text": text,
            "company": company,
        })

    return results


def linkedin_jobs() -> list[dict]:
    """Search LinkedIn's guest job API across climate+ML keyword combinations.

    Uses the unauthenticated /jobs-guest endpoint — no credentials required.
    Remote filter (f_WT=2) + location=United States keep results to US-remote roles;
    both are imperfect, so the non-US-subdomain drop and LLM location gate clean up.
    """
    import httpx
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    seen: set[str] = set()
    results: list[dict] = []

    for query in _LINKEDIN_QUERIES:
        try:
            r = httpx.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                params={"keywords": query, "location": "United States", "f_WT": "2", "f_JT": "F", "start": "0"},
                headers=headers,
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                print(f"  linkedin '{query}': {r.status_code}", file=sys.stderr)
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for card in soup.select("li"):
                title_el = card.select_one("h3")
                company_el = card.select_one("h4")
                link_el = card.select_one("a[href*='linkedin.com/jobs/view']")
                loc_el = card.select_one(".job-search-card__location")
                if not title_el or not link_el:
                    continue
                url = link_el.get("href", "").split("?")[0]
                if not url or url in seen:
                    continue
                seen.add(url)
                company = company_el.get_text(strip=True) if company_el else ""
                location = loc_el.get_text(strip=True) if loc_el else ""
                text = " — ".join(filter(None, [company, location, query]))
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": url,
                    "text": text,
                    "company": company,
                })
        except Exception as e:
            print(f"  linkedin '{query}' failed: {e}", file=sys.stderr)

    return results


_CLIMATEBASE_APP_ID = "8PSNFFQTXQ"
_CLIMATEBASE_SEARCH_KEY = "d2ebe27d3cc3d35fea04da7b1b0718a8"

# Role queries for Climatebase — no climate terms needed since the whole board is climate-focused.
_CLIMATEBASE_QUERIES = [
    "machine learning",
    "software engineer backend",
    "data engineer platform",
    "audio video media",
    "C++ systems",
    "forward deployed engineer",
    "solutions engineer applied AI",
]

_HN_CLIMATE_KEYWORDS = {
    "climate", "clean energy", "renewable", "solar", "wind", "carbon",
    "sustainability", "electric vehicle", " ev ", "grid", "battery",
    "cleantech", "greentech", "net zero", "emissions", "agtech",
    "water tech", "ocean tech", "geothermal",
}


def climatebase_jobs() -> list[dict]:
    """Search Climatebase via their Algolia index (public frontend search key).

    Filters to jobs activated in the last 30 days to avoid re-flooding seen.json
    with the full historical backlog on every 60-day dedup reset.
    """
    import time
    import httpx

    cutoff_ts = int(time.time()) - (30 * 24 * 60 * 60)
    url = f"https://{_CLIMATEBASE_APP_ID}-dsn.algolia.net/1/indexes/Job_production/query"
    headers = {
        "X-Algolia-Application-Id": _CLIMATEBASE_APP_ID,
        "X-Algolia-API-Key": _CLIMATEBASE_SEARCH_KEY,
        "Content-Type": "application/json",
    }

    seen: set[str] = set()
    results: list[dict] = []

    for query in _CLIMATEBASE_QUERIES:
        try:
            r = httpx.post(
                url,
                headers=headers,
                json={
                    "query": query,
                    "hitsPerPage": 50,
                    "filters": f"remote:true AND activation_date_i>{cutoff_ts}",
                },
                timeout=TIMEOUT,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"  climatebase '{query}': {e}", file=sys.stderr)
            continue

        for h in r.json().get("hits", []):
            oid = str(h.get("objectID", ""))
            if not oid or oid in seen:
                continue
            seen.add(oid)
            company = h.get("name_of_employer", "")
            sectors = ", ".join(h.get("sectors", [])[:3])
            results.append({
                "title": h.get("title", "").strip(),
                "url": f"https://climatebase.org/job/{oid}",
                "text": " — ".join(filter(None, [company, sectors, "Remote"])),
                "company": company,
            })

    return results


def hn_who_is_hiring() -> list[dict]:
    """Search the current Ask HN: Who is Hiring? thread for climate-adjacent roles.

    Uses search_by_date to find the latest monthly thread, then filters top-level
    comments (direct children of the story) for climate/clean energy keywords.
    """
    import time
    import httpx
    from bs4 import BeautifulSoup

    # Find the latest monthly thread posted by the dedicated whoishiring account
    jan_this_year = int(time.mktime(time.strptime("2026-01-01", "%Y-%m-%d")))
    try:
        r = httpx.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": "who is hiring",
                "tags": "story,author_whoishiring",
                "hitsPerPage": 5,
                "numericFilters": f"created_at_i>{jan_this_year}",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        stories = [s for s in r.json().get("hits", []) if "who is hiring" in s.get("title", "").lower()]
    except Exception as e:
        print(f"  hn: thread fetch failed: {e}", file=sys.stderr)
        return []

    if not stories:
        print("  hn: no Who is Hiring thread found", file=sys.stderr)
        return []

    story = max(stories, key=lambda s: s.get("created_at_i", 0))
    story_id = story["objectID"]
    print(f"  hn: using '{story['title']}' (id={story_id})", file=sys.stderr)

    # Fetch all comments (Algolia caps at 1000 per request)
    try:
        r = httpx.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"tags": f"comment,story_{story_id}", "hitsPerPage": 1000},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        comments = r.json().get("hits", [])
    except Exception as e:
        print(f"  hn: comments fetch failed: {e}", file=sys.stderr)
        return []

    results = []
    for c in comments:
        # Top-level only — nested replies aren't job postings
        if str(c.get("parent_id")) != str(story_id):
            continue
        raw = c.get("comment_text", "") or ""
        text = BeautifulSoup(raw, "html.parser").get_text(" ").strip()
        if not any(kw in text.lower() for kw in _HN_CLIMATE_KEYWORDS):
            continue
        first_line = text.split("\n")[0].strip()[:160]
        company = first_line.split("|")[0].strip() if "|" in first_line else ""
        results.append({
            "title": first_line or "HN hiring",
            "url": f"https://news.ycombinator.com/item?id={c['objectID']}",
            "text": text[:400],
            "company": company,
        })

    print(f"  hn: {len(results)} climate-matched postings", file=sys.stderr)
    return results


try:
    from exa_source import exa_jobs
    _EXA_AVAILABLE = True
except ImportError:
    _EXA_AVAILABLE = False

ALL_SOURCES: dict[str, Any] = {
    "greentownlabs": greentownlabs_jobs,
    "linkedin": linkedin_jobs,
    "climatebase": climatebase_jobs,
    "hn": hn_who_is_hiring,
    # mcj: mcj.vc/jobs 404s since domain migration from mcjcollective.com
    # workonclimate: workonclimate.org/jobs 404s; no working alternative found
    **({"exa": exa_jobs} if _EXA_AVAILABLE else {}),
}
