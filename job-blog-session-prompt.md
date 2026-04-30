# Session Prompt: Climate Tech Job Digest Blog

## Goal

Build an autonomous daily job digest blog for a senior ML engineer looking to pivot into climate tech. The blog scrapes remote job postings from climate-focused job boards, filters them for relevance to ML/AI/MLOps engineering, and publishes a daily Hugo post. The synthesis section generates a LinkedIn cold-connect message draft tailored to the most interesting role of the day.

The blog will be deployed to GitHub Pages and run on a GHA cron schedule, exactly like the terra blog pattern.

---

## Reference repos

- **Template**: `https://github.com/mcfredrick/autonomous-blog-template` — use this as the starting point. Clone it and work from there.
- **Terra** (`https://github.com/mcfredrick/terra`, branch `main`): reference for agent pipeline patterns, Hugo theme conventions, GHA workflow structure, and the `greentownlabs_jobs` scraper (in `agents/sources.py`).

Do not copy terra wholesale — the template is the base. Use terra as a reference for implementation patterns only.

---

## Candidate profile (use this for relevance scoring)

> "I build and own production ML systems end-to-end — from data pipelines and model training to safety guardrails and MLOps infrastructure — with the engineering rigor to make AI actually reliable at scale in domains where it matters."

**Skills keywords for filtering**: ML systems, MLOps, data pipelines, model training, model serving, safety/guardrails, distributed systems, Python, production AI, reliability engineering, infrastructure.

**Match criteria for a job to be included**:
- Remote position (non-negotiable)
- Role involves building or owning ML/AI systems in production (not research-only, not pure data science, not pure SWE with no ML)
- Company operates in climate tech, clean energy, sustainability, or adjacent domains (environmental monitoring, ag-tech, grid/energy, carbon accounting, climate modeling, etc.)

---

## Job boards to scrape (first iteration)

### 1. Greentown Labs (Consider API — already solved)

The terra repo (`agents/sources.py`, function `greentownlabs_jobs`) has a working implementation. Greentown Labs uses the Consider recruiting platform with an internal API:

```python
POST https://jobs.greentownlabs.com/api-boards/search-jobs
Content-Type: application/json

{
  "meta": {"size": 200},
  "board": {"id": "greentown-labs", "isParent": True},
  "query": {},
  "grouped": False,
  "parentSlug": "greentown-labs"
}
```

Response: `{"jobs": [...], "total": N}`. Each job has fields: `title`, `applyUrl`, `url`, `companyName`, `departments`, `remote` (bool), `locations`, `skills`. Filter to `remote: True`, then pass titles/descriptions through the relevance LLM.

### 2. Climatebase

Climatebase (`climatebase.org`) is the largest dedicated climate job board. Investigate their public API or scraping approach. Try:
- `https://climatebase.org/jobs?remote=true` for HTML scraping
- Check for an undocumented JSON API (inspect network requests in the browser or check their JS bundle for an `/api/` endpoint pattern, similar to how we discovered the Consider API)
- Filter results by role type: engineering, ML, data

### 3. MCJ Collective (My Climate Journey)

MCJ has a job board at `https://www.mcjcollective.com/jobs` or similar. Investigate the structure. They often use Lever or Greenhouse for individual company listings, but the aggregate board may have its own API.

### 4. Work on Climate

`https://workonclimate.org` has a job board. Check for Airtable embed (common pattern) or scrape the listing page.

### 5. Fallback: GitHub topic search

Use the GitHub search API to find repos with topics like `climate-tech`, `carbon-accounting`, `energy-transition` that are actively hiring (look for `hiring` or `jobs` in their README). This is lower priority but surfaces early-stage companies not on the boards above.

---

## Pipeline architecture

Mirror the terra pipeline structure:

```
sources.py → research_agent.py → writing_agent.py → validate_post.py → Hugo build → gh-pages deploy
```

### sources.py
Each function returns `list[dict]` with keys `title`, `url`, `text`, `company` (additional field for jobs). Implement one function per job board. `ALL_SOURCES` dict maps name → function. Mark job sources with a `JOBS_SOURCES` set so they bypass narrative LLM processing.

### research_agent.py
Two passes:
1. **Relevance scoring**: send each job's title + company + description to the LLM with the candidate profile above. Ask it to return a relevance score (0.0–1.0) and a 1-sentence reason. Filter to score ≥ 0.6.
2. **Deduplication**: use `seen.json` (60-day rolling window on `applyUrl`) to skip already-published listings.

Output: `/tmp/research.json` with structure:
```json
{
  "date": "2026-05-01",
  "jobs": [
    {
      "title": "Senior ML Engineer",
      "url": "https://...",
      "company": "Acme Climate",
      "text": "...",
      "relevance_score": 0.85,
      "relevance_reason": "Owns model training and serving infra for grid optimization."
    }
  ]
}
```

### writing_agent.py
The post format is different from terra — it's a job digest, not a news digest. The writing LLM should produce:

**Per-job bullet format:**
```
**[Job Title — Company](url)** — 1-2 sentences: what the company actually builds, why this role matches production ML skills.
```

**Synthesis section** (replaces terra's "Today's Synthesis"):
Pick the single most relevant role. Write:
1. A 2-3 sentence description of why this role is a strong match for the candidate profile.
2. A LinkedIn cold-connect message (150 words max) the user could send to someone at the company. The message should: reference the company's mission specifically, briefly mention the candidate's ML systems background, and express genuine interest — not a copy-paste template. Format as a blockquote.

The writing system prompt should reflect this structure explicitly.

### validate_post.py
Validation gates: post must have at least one job bullet with a markdown link, and must have a "## Today's Pick" section (renamed from "Today's Synthesis") containing a blockquote (the LinkedIn message). Adapt terra's validator for this schema.

---

## Hugo theme

Use the terra theme as a reference (`themes/terra/`) but adapt it:
- The blog identity should be distinct (pick a name, e.g. "Pivot" or "Signal" or similar)
- The index page should show the job count alongside the date
- Each post page should render job bullets cleanly; the LinkedIn message blockquote should be visually distinct (styled blockquote with a subtle left border)
- Keep it minimal — no JS, no external dependencies, CSS variables for theming

---

## GHA workflow

Mirror terra's `.github/workflows/daily.yml`:
- Cron: `0 9 * * 1-5` (weekdays only — job boards don't update on weekends)
- Guard: skip if today's post already exists
- `skip_research` dispatch input for reuse of cached `research.json`
- Secrets needed: `OPENROUTER_API_KEY` (same free-tier approach as terra)
- Bot commits post + seen.json to main; deploys `public/` to `gh-pages`

---

## First iteration scope

Keep it tight:
1. Implement Greentown Labs scraper (copy from terra, it's working)
2. Attempt Climatebase — if it requires significant reverse-engineering, stub it and move on
3. Wire up the research agent with relevance scoring
4. Wire up the writing agent with the job-digest format and LinkedIn message synthesis
5. Hugo theme: minimal adaptation of terra theme
6. GHA workflow: weekday cron, same pattern as terra
7. Validate end-to-end with a test run (`python agents/research_agent.py` then `python agents/writing_agent.py`)

Do **not** implement in the first iteration:
- Research into specific people to reach out to at the company
- Company background links or "learn more" sections
- Multiple LinkedIn message variants
- Job alert deduplication across sources (just deduplicate by URL in seen.json)

---

## Key constraints

- OpenRouter free tier only (same model rotation pattern as terra)
- Remote positions only — enforce in the scraper layer, not just the LLM
- If a job board scrape returns 0 results, log and continue — never hard-fail on a single source
- No external JS in the Hugo theme
- Post filename format: `YYYY-MM-DD.md` in `content/posts/`
- Blog identity strings (name, URL, bot email) go in `agents/config.py`
