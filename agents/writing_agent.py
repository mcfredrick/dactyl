#!/usr/bin/env python3
"""Synthesizes research.json job list into a Hugo markdown post."""

import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import httpx

from config import BLOG_URL, BLOG_NAME
from model_selector import build_candidate_list

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
RESEARCH_FILE = Path("/tmp/research.json")
SEEN_FILE = Path(__file__).parent / "seen.json"

CANDIDATE_PROFILE_SHORT = (
    "Senior ML engineer who builds production ML systems end-to-end: "
    "data pipelines, model training, model serving, safety guardrails, MLOps infrastructure. "
    "Strong Python and distributed systems. Pivoting from general ML infra into climate tech."
)

SYSTEM_PROMPT = """You are writing a daily job digest post for Pivot, a blog for senior ML engineers pivoting into climate tech.

Your reader: a senior ML engineer (5+ years, production systems) exploring climate tech careers.

Post structure — write ONLY these two sections, in this order:

## Today's Jobs

For each job in the input, write a bullet:
**[Job Title — Company](url)** — 1-2 sentences: what the company actually builds (be specific, not vague), and why this role is a strong match for someone with production ML/MLOps experience.

Be concrete. If it's a grid optimization company, say so. If the role involves model serving infrastructure, say so. Never use: "leverage", "synergy", "game-changer", "impactful", "making a difference", "saving the planet".

## Today's Pick

(This section is added separately — do NOT write it.)

Rules:
- Output ONLY the markdown body (no front matter, no "## Today's Pick" section)
- No closing remarks
- Write all jobs provided — do not skip any"""

PICK_SYSTEM_PROMPT = f"""You are writing the "Today's Pick" section for Pivot, a daily job digest for senior ML engineers pivoting into climate tech.

Candidate profile: {CANDIDATE_PROFILE_SHORT}

You will receive the single most relevant job from today's digest. Write:

1. A 2-3 sentence analysis of why this role is a strong fit — reference the candidate's specific skills (production ML, MLOps, data pipelines, model serving, safety/guardrails) and how they map to what this company actually needs.

2. A LinkedIn cold-connect message (150 words max) the candidate could send to an engineer or manager at this company. The message must:
   - Open with a specific reference to the company's mission or product (not generic)
   - Briefly mention 1-2 concrete things from the candidate's background
   - Express genuine curiosity about their ML stack or a specific challenge they face
   - Sound like a real person wrote it, not a template
   - NOT mention that it was AI-generated or that they found the role on a job board

Format the LinkedIn message as a markdown blockquote (> ...).

Output format:
<analysis paragraph>

> <linkedin message>

Output ONLY this — no headers, no "Here is...", no preamble."""


def _try_model(content: str, model: str, headers: dict, system_prompt: str = SYSTEM_PROMPT) -> str | None:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.7,
        "max_tokens": 4000,
    }
    r = httpx.post(OPENROUTER_API, json=payload, headers=headers, timeout=180)
    if r.status_code == 429:
        print(f"  {model}: rate limited", file=sys.stderr)
        return None
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    if not text:
        return None
    return text.strip()


def _has_jobs_section(body: str) -> bool:
    return bool(re.search(r'\*\*\[.+?\]\(https?://[^\s)]+\)\*\*', body))


def call_llm(content: str, preferred_model: str) -> str:
    api_key = os.environ["OPENROUTER_API_KEY"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": BLOG_URL,
        "X-Title": f"{BLOG_NAME} Writing Agent",
    }
    for candidate in build_candidate_list(preferred_model, api_key):
        print(f"  Writing trying: {candidate}", file=sys.stderr)
        for attempt in range(2):
            try:
                result = _try_model(content, candidate, headers)
                if result is None:
                    wait = 30 * (2 ** attempt)
                    print(f"  Rate limited, waiting {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                if not _has_jobs_section(result):
                    print(f"  {candidate}: response missing jobs section, skipping", file=sys.stderr)
                    time.sleep(15)
                    break
                print(f"  Success: {candidate}", file=sys.stderr)
                return result
            except httpx.HTTPStatusError as e:
                print(f"  {candidate} HTTP {e.response.status_code}, skipping", file=sys.stderr)
                break
            except Exception as e:
                print(f"  {candidate} error: {e}, skipping", file=sys.stderr)
                break

    raise RuntimeError("All writing models exhausted")


def call_pick_llm(job: dict, preferred_model: str) -> str:
    api_key = os.environ["OPENROUTER_API_KEY"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": BLOG_URL,
        "X-Title": f"{BLOG_NAME} Pick Agent",
    }
    content = (
        f"Job: {job['title']} at {job['company']}\n"
        f"URL: {job['url']}\n"
        f"Details: {job.get('text', '')}\n"
        f"Relevance reason: {job.get('relevance_reason', '')}"
    )
    for candidate in build_candidate_list(preferred_model, api_key)[:5]:
        print(f"  Pick trying: {candidate}", file=sys.stderr)
        try:
            result = _try_model(content, candidate, headers, system_prompt=PICK_SYSTEM_PROMPT)
            if result is None:
                time.sleep(15)
                continue
            if ">" not in result:
                print(f"  {candidate}: pick missing blockquote, skipping", file=sys.stderr)
                continue
            print(f"  Pick success: {candidate}", file=sys.stderr)
            return result
        except Exception as e:
            print(f"  {candidate} pick error: {e}, skipping", file=sys.stderr)

    return "_No LinkedIn message generated today._"


def build_writing_prompt(jobs: list[dict]) -> str:
    lines = [f"{len(jobs)} jobs to write about:\n"]
    for job in jobs:
        lines.append(
            f"- [{job['title']} — {job['company']}]({job['url']}) "
            f"| Score: {job['relevance_score']:.2f} "
            f"| {job.get('relevance_reason', '')} "
            f"| Context: {job.get('text', '')[:200]}"
        )
    return "\n".join(lines)


def build_description(jobs: list[dict]) -> str:
    if not jobs:
        return "Daily ML jobs in climate tech"
    titles = [f"{j['title']} at {j['company']}" for j in jobs[:2] if j.get("title")]
    if titles:
        return f"{len(jobs)} jobs today: {', '.join(titles)} and more."
    return f"{len(jobs)} ML jobs in climate tech today."


def build_tags(jobs: list[dict]) -> list[str]:
    tags = ["climate", "ml", "jobs"]
    texts = " ".join(j.get("text", "") + j.get("title", "") for j in jobs).lower()
    if any(kw in texts for kw in ["energy", "grid", "solar", "wind", "battery"]):
        tags.append("energy")
    if any(kw in texts for kw in ["mlops", "infrastructure", "platform", "serving"]):
        tags.append("mlops")
    if any(kw in texts for kw in ["data", "pipeline", "etl"]):
        tags.append("data")
    return sorted(set(tags))


def write_post(jobs: list[dict], post_date: str, preferred_model: str) -> str:
    writing_prompt = build_writing_prompt(jobs)
    bullets_body = call_llm(writing_prompt, preferred_model)

    # Generate Today's Pick for the top job
    top_job = jobs[0]
    print(f"  Generating pick for: {top_job['title']} at {top_job['company']}", file=sys.stderr)
    time.sleep(15)  # brief cooldown between LLM calls
    pick_body = call_pick_llm(top_job, preferred_model)

    pick_section = f"\n\n## Today's Pick\n\n**{top_job['title']} — {top_job['company']}**\n\n{pick_body}"

    description = build_description(jobs)
    tags = build_tags(jobs)
    tags_yaml = json.dumps(tags)

    front_matter = (
        f"---\n"
        f"title: \"{post_date}\"\n"
        f"date: {post_date}T09:00:00Z\n"
        f"draft: false\n"
        f"tags: {tags_yaml}\n"
        f"description: \"{description}\"\n"
        f"job_count: {len(jobs)}\n"
        f"---\n\n"
    )

    return front_matter + bullets_body + pick_section


def update_seen(urls: list[str], post_date: str) -> None:
    from datetime import timezone, timedelta
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
    for url in urls:
        if url and url not in existing:
            data["urls"].append({"url": url, "date": post_date})

    SEEN_FILE.write_text(json.dumps(data, indent=2))


def main() -> None:
    model = os.environ.get("WRITING_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    print(f"Writing model: {model}", file=sys.stderr)

    if not RESEARCH_FILE.exists():
        print(f"Error: {RESEARCH_FILE} not found", file=sys.stderr)
        sys.exit(1)

    research = json.loads(RESEARCH_FILE.read_text())
    post_date = research.get("date", str(date.today()))
    jobs = research.get("jobs", [])

    if not jobs:
        print("No jobs in research.json, skipping post", file=sys.stderr)
        sys.exit(0)

    print(f"Writing post from {len(jobs)} jobs...", file=sys.stderr)
    post = write_post(jobs, post_date, model)

    output_path = Path("content/posts") / f"{post_date}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(post)
    print(f"Wrote {output_path}", file=sys.stderr)

    update_seen([j["url"] for j in jobs], post_date)
    print("Updated seen.json", file=sys.stderr)


if __name__ == "__main__":
    main()
