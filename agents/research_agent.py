#!/usr/bin/env python3
"""Fetches job sources, scores relevance via LLM, writes /tmp/research.json."""

import json
import os
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
RELEVANCE_THRESHOLD = 0.6

CANDIDATE_PROFILE = """Senior ML engineer with 5+ years building production ML systems end-to-end:
- Data pipelines (ingestion, transformation, feature engineering at scale)
- Model training (distributed training, experiment tracking, hyperparameter optimization)
- Model serving (low-latency inference, model versioning, A/B testing)
- Safety & guardrails (output validation, monitoring, drift detection)
- MLOps infrastructure (CI/CD for models, GPU cluster management, observability)
- Strong Python, distributed systems, reliability engineering

Looking for: remote roles at climate tech, clean energy, sustainability, environmental monitoring,
ag-tech, grid/energy, carbon accounting, or climate modeling companies. Must involve building or
owning ML/AI systems in production (not research-only, not pure data science, not pure SWE)."""

SCORING_SYSTEM_PROMPT = f"""You are a job relevance scorer for a senior ML engineer pivoting into climate tech.

Candidate profile:
{CANDIDATE_PROFILE}

You will receive a list of job postings. For each job, score its relevance to this candidate on a 0.0-1.0 scale:
- 1.0: Perfect match — remote, production ML/MLOps, climate/energy company
- 0.8: Strong match — remote, strong ML component, climate-adjacent company
- 0.6: Decent match — remote, some ML, climate sector
- Below 0.4: Not a match — research-only, pure SWE, no ML, not remote, or not climate-related

Return a JSON array with one object per job:
{{"index": <original_index>, "score": <float 0.0-1.0>, "reason": "<1 sentence why>"}}

Return ONLY the JSON array, no preamble."""


def load_seen_urls() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    data = json.loads(SEEN_FILE.read_text())
    return {entry["url"] for entry in data.get("urls", [])}


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
        result_jobs.append({
            "title": job.get("title", ""),
            "url": job.get("url", ""),
            "company": job.get("company", ""),
            "text": job.get("text", ""),
            "relevance_score": score,
            "relevance_reason": score_entry.get("reason", ""),
        })

    # Sort by relevance descending
    result_jobs.sort(key=lambda j: j["relevance_score"], reverse=True)

    print(f"Relevant jobs: {len(result_jobs)}", file=sys.stderr)

    output = {"date": post_date, "jobs": result_jobs}
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUTPUT_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
