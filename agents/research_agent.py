#!/usr/bin/env python3
"""Fetches job sources, scores relevance via LLM, writes /tmp/research.json."""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from config import BLOG_URL, BLOG_NAME, BOT_USER_AGENT
from model_selector import build_candidate_list, fetch_free_models, pick_research_model
from sources import ALL_SOURCES

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
SEEN_FILE = Path(__file__).parent / "seen.json"
OUTPUT_FILE = Path("/tmp/research.json")
RELEVANCE_THRESHOLD = 0.55

# LinkedIn serves country-specific results from ISO-code subdomains (de., uk., se.).
# A US-based, no-relocation candidate can't take those, so drop them before scoring.
# www. and us. are US/global; the LLM location gate catches non-LinkedIn stragglers.
_NON_US_LINKEDIN = re.compile(r"https?://(?!www\.|us\.)[a-z]{2}\.linkedin\.com", re.I)

# LinkedIn's f_WT=2 remote filter leaks and the search card carries no workplace-type badge
# (it's rendered client-side, absent from every unauthenticated endpoint). The job description
# prose is the only signal we can fetch, so we re-check finalists against it below.
_LINKEDIN_JOB_RE = re.compile(r"linkedin\.com/jobs/view/[^/?]*?-(\d{8,})", re.I)
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

CANDIDATE_PROFILE = """Matthew Fredrick — Senior AI/ML Software Engineer
Target: Staff Engineer / Tech Lead in Applied AI at climate tech / impact orgs
Remote only | $200k+ base target

Core strengths (4 years at Cisco Webex/Collaboration AI):
- LLM guardrails & safety: content filtering, prompt injection defense, jailbreak prevention, toxicity detection (millions of users, production scale)
- Synthetic data pipelines: ~90% reduction in data-creation effort; continuous fine-tuning across classification domains
- MLOps: Airflow, MLflow, W&B CI/CD, model lifecycle management, org-wide standards
- Computer vision: background blur, virtual backgrounds, relighting pipelines (production, diverse hardware)
- RAG: OCR + document ingestion from POC to production
- Cross-functional technical leadership; regular briefings to senior leadership; formal mentorship

Prior: audio/DSP engineering at BandLab (C++, JUCE, VST/AU); audio/ML consulting
Side projects: shipped iOS app (Noogat), runs two daily engineering digests (Tenkai, Terra)

Stack: Python primary | C++, Rust | PyTorch, TF, HuggingFace, scikit-learn | AWS, Azure, K8s, Docker | Airflow, MLflow, W&B

Best-fit roles: general applied AI engineering — any role where the primary job is building AI systems in production. Also open to FDE, Solutions Engineer, Applied AI Engineer, Technical Solutions Engineer for roles with more customer/product influence.
Poor-fit roles: pure data science / research, roles requiring deep domain expertise he lacks (power engineering, quant finance, pure Java/Go backend with no ML), junior roles, pure sales/pre-sales with no technical depth"""

SCORING_SYSTEM_PROMPT = f"""You are scoring job postings for a specific candidate pivoting into climate tech.

Candidate profile:
{CANDIDATE_PROFILE}

Score each job on THREE dimensions, then compute a weighted composite:

1. climate_score (0–1): Does the company have a genuine climate/clean energy/sustainability mission?
   - 1.0: Core climate mission (energy storage, grid software, carbon accounting, clean energy, ag-tech, climate ML)
   - 0.8: Strong climate-adjacent mission
   - 0.6: Loosely related (some sustainability angle)
   - 0.3: Peripheral / token mention of climate
   - 0.0: No climate connection

2. fit_score (0–1): How well does the role match this candidate's skills and target direction (general applied AI)?
   - 0.9–1.0: Applied AI role — primary job is building AI systems in production: LLM applications, ML models, AI agents, AI-powered product features, model fine-tuning/serving, AI infra; OR FDE/Solutions Engineer at an AI company requiring deep hands-on ML expertise
   - 0.7–0.8: Strong adjacent — MLOps/ML platform, Staff/Tech Lead with meaningful AI ownership, technical solutions engineering with ML depth, AI consulting with engineering deliverables
   - 0.5–0.6: Partial — software engineering with a real ML component, data engineering that directly enables AI, AI-adjacent architecture roles
   - 0.3–0.4: Weak — minimal AI/ML; requires deep domain expertise he lacks (power systems, quant finance, SCADA); pure backend/infra with no AI scope
   - 0.0–0.2: Poor — no AI/ML component; pure research or data science without engineering; pre-sales or account management without technical depth

3. level_score (0–1): Is the role level appropriate for a Staff/Tech Lead target?
   - 1.0: Staff, Principal, Tech Lead, Engineering Lead, Founding Engineer
   - 0.8: Senior (current level, one step below target — still worth applying)
   - 0.5: Mid-level
   - 0.1: Junior / entry level

Also judge location_ok: can a US-based candidate who will NOT relocate do this job from home in the US?
   - "yes": remote and open to US applicants — US-remote, North America remote, or global/"worldwide" remote with no country restriction
   - "no": on-site, hybrid, or remote restricted to a non-US country/region (e.g. "Remote (Germany)", "EU-based only", "Hybrid — London")
   - "unknown": work arrangement or location not stated
Base this ONLY on explicit signals in the posting. If unclear, use "unknown", never "no". "no" is disqualifying regardless of the other scores.

Composite score = 0.35 × climate_score + 0.55 × fit_score + 0.10 × level_score

Also extract comp_note: a brief text label of what compensation signals are present.
Examples: "$200k–$250k + equity", "$150k (below target)", "mentions equity + bonus, no salary", "mentions equity only", "no compensation info"
Include "unlimited PTO" in comp_note if mentioned.

Return a JSON array — one object per job, in the same order as input:
{{"index": <int>, "score": <composite 0–1>, "climate_score": <0–1>, "fit_score": <0–1>, "level_score": <0–1>, "location_ok": "<yes|no|unknown>", "level": "<Staff|Senior|Mid|Junior|Unknown>", "comp_note": "<brief string>", "reason": "<1 sentence why or why not>"}}

Return ONLY the JSON array, no preamble."""


def load_seen_urls() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    data = json.loads(SEEN_FILE.read_text())
    # ponytail: apply the same 60-day cutoff here so expiry actually works
    # without this, URLs block forever if update_seen never fires (zero-job runs)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=60)
    return {
        entry["url"] for entry in data.get("urls", [])
        if datetime.fromisoformat(entry["date"]).replace(tzinfo=timezone.utc) > cutoff
    }


def fetch_all_sources() -> list[dict]:
    jobs: list[dict] = []
    for name, fetcher in ALL_SOURCES.items():
        print(f"Fetching {name}...", file=sys.stderr)
        try:
            items = fetcher()
            print(f"  {len(items)} items", file=sys.stderr)
            for item in items:
                item["_source"] = name
            jobs.extend(items)
        except Exception as e:
            print(f"  Error fetching {name}: {e}", file=sys.stderr)
    return jobs


def score_jobs_batch(jobs: list[dict], model: str) -> list[dict] | None:
    """Score a batch of jobs. Returns list of {index, score, reason} or None on failure."""
    lines = []
    for i, job in enumerate(jobs):
        lines.append(f"[{i}] {job.get('title', '')} at {job.get('company', '')} — {job.get('text', '')[:200]}")

    content = "\n".join(lines)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SCORING_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "HTTP-Referer": BLOG_URL,
        "X-Title": f"{BLOG_NAME} Research Agent",
    }

    for attempt in range(3):
        try:
            r = httpx.post(OPENROUTER_API, json=payload, headers=headers, timeout=120)
            if r.status_code == 429:
                wait = 2 ** attempt * 10
                print(f"  Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            start = text.find("[")
            end = text.rfind("]") + 1
            if start == -1 or end == 0:
                return []
            return json.loads(text[start:end])
        except Exception as e:
            print(f"  Score batch failed (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(2 ** attempt * 3)

    return None


def score_jobs(jobs: list[dict], preferred_model: str) -> list[dict]:
    """Score jobs for relevance. Falls back to passthrough if LLM unavailable."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    candidates = build_candidate_list(preferred_model, api_key)

    # Score in batches of 20
    batch_size = 20
    all_scores: list[dict] = []

    for batch_start in range(0, len(jobs), batch_size):
        batch = jobs[batch_start:batch_start + batch_size]
        scored = None

        for model in candidates[:5]:
            print(f"  Scoring batch {batch_start//batch_size + 1} with {model}...", file=sys.stderr)
            scored = score_jobs_batch(batch, model)
            if scored is not None:
                break
            time.sleep(5)

        if scored is None:
            print("  LLM unavailable, using passthrough scores", file=sys.stderr)
            scored = [{"index": i, "score": 0.7, "reason": "Passthrough — LLM unavailable"} for i in range(len(batch))]

        # Offset indices back to global position
        for entry in scored:
            entry["_global_index"] = batch_start + entry.get("index", 0)
        all_scores.extend(scored)

    return all_scores


_ARRANGEMENT_KW = re.compile(
    r"\b(remote|hybrid|on-?site|in[- ]person|in[- ]office|in the office|relocat\w*|"
    r"work from home|wfh|must be (?:located|based)|based in)\b", re.I)


def fetch_linkedin_description(url: str) -> str | None:
    """Fetch a LinkedIn job's location-relevant excerpt via the guest endpoint.

    The work-arrangement statement can sit deep in the body (offsets past 5 KB observed), so
    head-truncation misses it. Return the head plus a window around every arrangement keyword —
    full signal coverage, small payload. None if not LinkedIn or on failure.
    """
    m = _LINKEDIN_JOB_RE.search(url)
    if not m:
        return None
    try:
        r = httpx.get(
            f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}",
            headers={"User-Agent": _BROWSER_UA},
            timeout=20,
        )
        if r.status_code != 200:
            return None
    except Exception:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text)).strip()
    if not text:
        return None
    windows, used = [], 0
    for w in _ARRANGEMENT_KW.finditer(text):
        windows.append(text[max(0, w.start() - 120): w.end() + 120])
        used += len(windows[-1])
        if used > 1000:
            break
    return text[:400] + (" … " + " … ".join(windows) if windows else "")


LOCATION_CHECK_SYSTEM = """You decide if a job is doable by a US-based candidate who will NOT relocate and must work from home in the US.

Judge location_ok from the job description:
- "no": on-site, hybrid, in-person / in-office requirement, or remote restricted to a non-US country/region. Do NOT mark "no" for text that merely says on-site is NOT required.
- "yes": fully remote and open to US applicants (US remote, North America remote, or worldwide remote).
- "unknown": work arrangement not clearly stated.

Return ONLY a JSON array: [{"index": <int>, "location_ok": "<yes|no|unknown>"}]"""


def verify_finalist_locations(jobs: list[dict], model: str) -> list[dict]:
    """Re-check LinkedIn finalists against their full description; drop stated on-site/hybrid/non-US.

    The initial scorer only sees the location string (the search card has no arrangement signal),
    so on-site/hybrid roles leak through. Here we fetch each LinkedIn finalist's description — the
    only unauthenticated signal — and let the LLM judge. Fail-open: any fetch/LLM error or an
    "unknown" verdict keeps the job.
    """
    enriched = [(j, d) for j in jobs if (d := fetch_linkedin_description(j["url"]))]
    if not enriched:
        return jobs

    lines = [f"[{i}] {j['title']} — {d}" for i, (j, d) in enumerate(enriched)]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": LOCATION_CHECK_SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "HTTP-Referer": BLOG_URL,
        "X-Title": f"{BLOG_NAME} Research Agent",
    }
    try:
        r = httpx.post(OPENROUTER_API, json=payload, headers=headers, timeout=120)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        verdicts = json.loads(text[text.find("["):text.rfind("]") + 1])
    except Exception as e:
        print(f"  Location verify failed, keeping all finalists: {e}", file=sys.stderr)
        return jobs

    drop = set()
    for v in verdicts:
        idx = v.get("index", -1)
        if v.get("location_ok") == "no" and 0 <= idx < len(enriched):
            drop.add(id(enriched[idx][0]))
    kept = [j for j in jobs if id(j) not in drop]
    print(f"  Location verify: dropped {len(jobs) - len(kept)} on-site/hybrid finalist(s)", file=sys.stderr)
    return kept


def update_seen(new_urls: list[str], post_date: str) -> None:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=60)

    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text())
    else:
        data = {"urls": []}

    data["urls"] = [
        entry for entry in data["urls"]
        if datetime.fromisoformat(entry["date"]).replace(tzinfo=timezone.utc) > cutoff
    ]

    existing = {e["url"] for e in data["urls"]}
    for url in new_urls:
        if url and url not in existing:
            data["urls"].append({"url": url, "date": post_date})

    SEEN_FILE.write_text(json.dumps(data, indent=2))


def main() -> None:
    model = os.environ.get("RESEARCH_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    print(f"Research model: {model}", file=sys.stderr)

    seen_urls = load_seen_urls()
    print(f"Loaded {len(seen_urls)} seen URLs", file=sys.stderr)

    all_jobs = fetch_all_sources()
    print(f"Total jobs fetched: {len(all_jobs)}", file=sys.stderr)

    # Deduplicate by URL before scoring
    deduped: list[dict] = []
    seen_now: set[str] = set()
    for job in all_jobs:
        url = job.get("url", "")
        if not url or url in seen_urls or url in seen_now:
            continue
        if _NON_US_LINKEDIN.match(url):
            continue
        seen_now.add(url)
        deduped.append(job)

    print(f"After deduplication: {len(deduped)} new jobs", file=sys.stderr)

    if not deduped:
        print("No new jobs to score", file=sys.stderr)
        post_date = str(date.today())
        OUTPUT_FILE.write_text(json.dumps({"date": post_date, "jobs": []}, indent=2))
        sys.exit(0)

    scores = score_jobs(deduped, model)

    # Build scored job list
    post_date = str(date.today())
    scored_map = {s.get("_global_index", s.get("index", 0)): s for s in scores}

    result_jobs = []
    for i, job in enumerate(deduped):
        score_entry = scored_map.get(i, {})
        score = float(score_entry.get("score", 0.0))
        if score < RELEVANCE_THRESHOLD:
            continue
        if score_entry.get("location_ok") == "no":
            continue
        result_jobs.append({
            "title": job.get("title", ""),
            "url": job.get("url", ""),
            "company": job.get("company", ""),
            "text": job.get("text", ""),
            "relevance_score": score,
            "relevance_reason": score_entry.get("reason", ""),
            "fit_score": float(score_entry.get("fit_score", 0.0)),
            "level": score_entry.get("level", "Unknown"),
            "comp_note": score_entry.get("comp_note", "no compensation info"),
        })

    result_jobs = verify_finalist_locations(result_jobs, model)
    result_jobs.sort(key=lambda j: j["relevance_score"], reverse=True)

    print(f"Relevant jobs: {len(result_jobs)}", file=sys.stderr)

    output = {"date": post_date, "jobs": result_jobs}
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUTPUT_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
