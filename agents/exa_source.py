#!/usr/bin/env python3
"""Exa-powered deep research source: two-round loop with LLM-driven follow-ups.

Budget design: 5 seed + up to 5 follow-up queries = ~10 Exa requests/run.
At 22 weekday runs/month: ~220 of the 1000 free monthly requests.
"""

import json
import os
import sys

import httpx

from config import BLOG_NAME, BLOG_URL

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
_FALLBACK_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

_CANDIDATE_SUMMARY = (
    "Senior engineer (ML/AI, backend, audio/video, C++/Python, systems). "
    "Seeking remote roles at climate tech, clean energy, sustainability, "
    "ag-tech, grid software, or carbon accounting companies."
)

_ATS_DOMAINS = ["jobs.lever.co", "boards.greenhouse.io", "jobs.ashbyhq.com", "apply.workable.com"]
_CLIMATE_BOARDS = ["wellfound.com", "climatebase.org", "terra.do", "workonclimate.org"]

# (query, include_domains or None)
_SEED_QUERIES: list[tuple[str, list[str] | None]] = [
    ("remote machine learning engineer climate tech clean energy", _ATS_DOMAINS),
    ("remote software backend systems engineer sustainability renewable energy", _ATS_DOMAINS),
    ("remote data platform engineer carbon grid energy storage", _ATS_DOMAINS),
    ("climate tech software engineer ML remote hiring", _CLIMATE_BOARDS + _ATS_DOMAINS[:2]),
    ("clean energy software engineering remote jobs", None),
]

_FOLLOW_UP_SYSTEM = f"""\
Candidate profile: {_CANDIDATE_SUMMARY}

You will receive a list of climate tech jobs found so far.
Generate 3-5 short Exa search queries to find MORE relevant jobs we may have missed.
Target:
- Other open roles at promising companies from the list
- Climate niches not yet covered (EV infrastructure, ocean tech, water tech, climate modeling)
- Specific niche job boards in the climate space

Return ONLY a JSON array of query strings: ["query1", "query2", ...]"""


def _search(exa, query: str, domains: list[str] | None) -> list[dict]:
    kwargs: dict = {
        "type": "auto",
        "num_results": 10,
        "contents": {"highlights": True},
    }
    if domains:
        kwargs["include_domains"] = domains

    try:
        results = exa.search_and_contents(query, **kwargs)
    except Exception as e:
        print(f"  exa '{query[:60]}': {e}", file=sys.stderr)
        return []

    jobs = []
    for r in results.results:
        highlights = getattr(r, "highlights", None) or []
        text = " … ".join(highlights[:3])
        jobs.append({
            "title": (r.title or "").strip(),
            "url": r.url,
            "text": text,
            "company": _company_from_url(r.url),
        })
    return jobs


def _company_from_url(url: str) -> str:
    for marker in ["lever.co/", "greenhouse.io/", "ashbyhq.com/", "workable.com/"]:
        if marker in url:
            slug = url.split(marker)[-1].split("/")[0]
            return slug.replace("-", " ").title()
    return ""


def _generate_follow_ups(jobs: list[dict], api_key: str) -> list[str]:
    if not api_key or not jobs:
        return []

    summary = "\n".join(
        f"- {j['title']} at {j['company'] or 'unknown'}"
        for j in jobs[:30]
    )
    payload = {
        "model": _FALLBACK_MODEL,
        "messages": [
            {"role": "system", "content": _FOLLOW_UP_SYSTEM},
            {"role": "user", "content": f"Jobs found so far:\n{summary}"},
        ],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": BLOG_URL,
        "X-Title": f"{BLOG_NAME} Exa Research",
    }

    try:
        r = httpx.post(OPENROUTER_API, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        start, end = text.find("["), text.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        queries = json.loads(text[start:end])
        return [q for q in queries if isinstance(q, str)][:5]
    except Exception as e:
        print(f"  exa follow-up generation failed: {e}", file=sys.stderr)
        return []


def exa_jobs() -> list[dict]:
    api_key = os.environ.get("EXA_API_KEY", "")
    if not api_key:
        print("  EXA_API_KEY not set, skipping exa source", file=sys.stderr)
        return []

    try:
        from exa_py import Exa
    except ImportError:
        print("  exa-py not installed, skipping exa source", file=sys.stderr)
        return []

    exa = Exa(api_key=api_key)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    seen: set[str] = set()
    all_jobs: list[dict] = []

    def _add(jobs: list[dict]) -> None:
        for job in jobs:
            if job["url"] and job["url"] not in seen:
                seen.add(job["url"])
                all_jobs.append(job)

    print(f"  exa round 1: {len(_SEED_QUERIES)} seed queries", file=sys.stderr)
    for query, domains in _SEED_QUERIES:
        _add(_search(exa, query, domains))
    print(f"  exa round 1 complete: {len(all_jobs)} jobs", file=sys.stderr)

    follow_ups = _generate_follow_ups(all_jobs, openrouter_key)
    if follow_ups:
        print(f"  exa round 2: {len(follow_ups)} follow-up queries", file=sys.stderr)
        for query in follow_ups:
            _add(_search(exa, query, None))
        print(f"  exa round 2 complete: {len(all_jobs)} total jobs", file=sys.stderr)

    return all_jobs
