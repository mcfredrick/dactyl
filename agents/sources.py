#!/usr/bin/env python3
"""Job board scrapers. Each function returns list[dict] with title, url, text, company."""

import sys
from typing import Any

from config import BOT_USER_AGENT

TIMEOUT = 20

JOBS_SOURCES = {"greentownlabs", "linkedin"}

# Queries for LinkedIn job search: each combines an ML/AI role term with a climate/sustainability term.
# The LLM scorer filters false positives; breadth here is intentional.
_LINKEDIN_QUERIES = [
    "machine learning climate",
    "machine learning clean energy",
    "machine learning renewable energy",
    "machine learning carbon",
    "machine learning energy storage",
    "machine learning solar",
    "machine learning agriculture",
    "machine learning food tech",
    "machine learning sustainability",
    "MLOps climate",
    "data engineer climate",
    "data engineer clean energy",
    "ML engineer sustainability",
    "AI engineer climate",
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
    Remote filter (f_WT=2) is applied but imperfect; LLM scorer handles cleanup.
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
                params={"keywords": query, "location": "Worldwide", "f_WT": "2", "f_JT": "F", "start": "0"},
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


ALL_SOURCES: dict[str, Any] = {
    "greentownlabs": greentownlabs_jobs,
    "linkedin": linkedin_jobs,
    # climatebase: fully Cloudflare-locked (403 on all API/HTML paths)
    # mcj: mcj.vc/jobs 404s since domain migration from mcjcollective.com
    # workonclimate: workonclimate.org/jobs 404s; no working alternative found
}
