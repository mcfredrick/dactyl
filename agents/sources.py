#!/usr/bin/env python3
"""Job board scrapers. Each function returns list[dict] with title, url, text, company."""

import sys
from typing import Any

from config import BOT_USER_AGENT

TIMEOUT = 20

JOBS_SOURCES = {"greentownlabs", "climatebase", "mcj", "workonclimate"}


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
        depts = ", ".join(job.get("departments", [])) if job.get("departments") else ""
        skills = ", ".join(job.get("skills", [])[:5]) if job.get("skills") else ""
        text = " — ".join(filter(None, [company, depts, skills, "Remote"]))
        results.append({
            "title": job.get("title", "").strip(),
            "url": url,
            "text": text,
            "company": company,
        })

    return results


def climatebase_jobs() -> list[dict]:
    """Fetch remote ML/AI jobs from Climatebase."""
    import httpx
    from bs4 import BeautifulSoup

    results = []
    seen: set[str] = set()

    # Try undocumented JSON API first
    try:
        r = httpx.get(
            "https://climatebase.org/api/jobs",
            params={"remote": "true", "page": 1, "per_page": 50},
            headers={"User-Agent": BOT_USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
            jobs = data.get("jobs") or data.get("results") or data.get("data") or []
            for job in jobs:
                url = job.get("apply_url") or job.get("url") or job.get("link", "")
                if not url or url in seen:
                    continue
                if not (job.get("remote") or job.get("is_remote")):
                    continue
                seen.add(url)
                company = job.get("company") or job.get("organization", {}).get("name", "")
                results.append({
                    "title": job.get("title", "").strip(),
                    "url": url,
                    "text": f"{company} — {job.get('description', '')[:300]}",
                    "company": company,
                })
            if results:
                return results[:20]
    except Exception as e:
        print(f"  climatebase API attempt failed: {e}", file=sys.stderr)

    # Fall back to HTML scraping
    try:
        r = httpx.get(
            "https://climatebase.org/jobs",
            params={"remote": "true", "roles": "engineering,data"},
            headers={"User-Agent": BOT_USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for article in soup.select("article, [data-job-id], .job-card, .job-listing")[:30]:
            title_el = article.select_one("h2, h3, .job-title, [class*='title']")
            link_el = article.select_one("a[href*='/jobs/'], a[href*='job']")
            company_el = article.select_one(".company, .organization, [class*='company']")
            remote_el = article.find(string=lambda t: t and "remote" in t.lower())

            if not title_el or not link_el:
                continue
            if not remote_el:
                continue

            href = link_el.get("href", "")
            if href.startswith("/"):
                href = f"https://climatebase.org{href}"
            if not href or href in seen:
                continue

            company = company_el.get_text(strip=True) if company_el else ""
            seen.add(href)
            results.append({
                "title": title_el.get_text(strip=True),
                "url": href,
                "text": f"{company} — Remote",
                "company": company,
            })

        return results[:20]
    except Exception as e:
        print(f"  climatebase HTML scrape failed: {e}", file=sys.stderr)
        return []


def mcj_jobs() -> list[dict]:
    """Fetch remote ML/AI jobs from MCJ Collective job board."""
    import httpx
    from bs4 import BeautifulSoup

    try:
        r = httpx.get(
            "https://www.mcjcollective.com/jobs",
            headers={"User-Agent": BOT_USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        seen: set[str] = set()

        for el in soup.select("a[href*='lever.co'], a[href*='greenhouse.io'], a[href*='ashbyhq.com'], a[href*='jobs']")[:40]:
            href = el.get("href", "")
            if not href or href in seen:
                continue
            title = el.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            seen.add(href)

            parent = el.find_parent(["li", "div", "article"])
            company = ""
            if parent:
                company_el = parent.find(class_=lambda c: c and "company" in c.lower() if c else False)
                if company_el:
                    company = company_el.get_text(strip=True)

            results.append({
                "title": title,
                "url": href,
                "text": f"{company} — Remote (MCJ)" if company else "Remote (MCJ)",
                "company": company,
            })

        return results[:15]
    except Exception as e:
        print(f"  mcj_jobs fetch failed: {e}", file=sys.stderr)
        return []


def workonclimate_jobs() -> list[dict]:
    """Fetch remote ML/AI jobs from Work on Climate."""
    import httpx
    from bs4 import BeautifulSoup

    try:
        r = httpx.get(
            "https://workonclimate.org/jobs",
            headers={"User-Agent": BOT_USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        seen: set[str] = set()

        for el in soup.select("a[href]")[:60]:
            href = el.get("href", "")
            if not href:
                continue
            # Look for job application links
            if not any(x in href for x in ["lever.co", "greenhouse.io", "ashby", "apply", "job"]):
                continue
            if href in seen:
                continue

            title = el.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            seen.add(href)

            results.append({
                "title": title,
                "url": href,
                "text": "Remote (Work on Climate)",
                "company": "",
            })

        return results[:15]
    except Exception as e:
        print(f"  workonclimate fetch failed: {e}", file=sys.stderr)
        return []


ALL_SOURCES: dict[str, Any] = {
    "greentownlabs": greentownlabs_jobs,
    "climatebase": climatebase_jobs,
    "mcj": mcj_jobs,
    "workonclimate": workonclimate_jobs,
}
