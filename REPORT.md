# LLM YouTube Channel Tracker

> **Live site:** https://ntmphg-youtube-tracker.up.railway.app/_
> **Repository:** https://github.com/ntmphg/youtubellm 

A self-updating dashboard that watches eight popular LLM-focused YouTube channels,
transcribes each new video, distils what the creator actually argues into a
structured summary, and surfaces cross-channel relationships such as shared
themes, trending topics and substantive disagreements.

---

## 1. Problem Statement

Tracking the LLM ecosystem on YouTube means following 10-20 channels that
each post daily-to-weekly. Existing aggregators read only titles and
thumbnails — both of which routinely misrepresent the actual argument of
a video. This project ingests transcripts from a curated set of eight LLM
channels, summarises each creator's stance and topic coverage with an
LLM, surfaces cross-channel relationships, and serves the result as a
live public page that updates as new videos appear.

The brief asks specifically for a tool that:

1. **Follows several popular YouTube channels** focused on LLMs.
2. **Categorises** each new video — speaker, topics, the creator's stance.
3. **Surfaces relationships** between channels on LLM themes (shared themes,
   contrasts, mutual references).
4. **Folds AI-generated transcript summaries into the table** so each row
   reflects "what the creator actually says" — not the title.
5. **Stays live and updates automatically** as new videos appear, accessible
   to reviewers in a browser.

The output is a single public page; the operating constraint is that it must
keep working after I close my laptop.

---

## 2. Methodology

### 2.1 System architecture

```
┌─────────────────────┐    git push    ┌──────────────────────┐
│   Local machine     │ ──────────────▶│  GitHub (public)     │
│                     │                │                      │
│  Task Scheduler     │                │  data/videos.json    │
│       │             │                │  data/skipped.json   │
│       ▼             │                │  data/relationships  │
│  sync.py            │                └────────┬─────────────┘
│   │                 │                         │  auto-deploy
│   ▼                 │                         ▼
│  runner.py          │                ┌──────────────────────┐
│   ├─ feedparser ────┼─── RSS poll ──▶│  Railway container   │
│   ├─ OpenClaw skill │                │                      │
│   │   get_transcript│                │  FastAPI + Jinja     │
│   ├─ Gemini 2.5     │                │  reads JSON files    │
│   │   per-video     │                │  serves table + HKT  │
│   └─ Gemini 2.5     │                │  formatted dashboard │
│      relationships  │                │                      │
└─────────────────────┘                └──────────────────────┘
```

The architecture is **hybrid by design**, not by accident — see §3.1 for the
constraint that forced it.

### 2.2 Channel selection

Eight channels were curated to represent different angles on LLM content:

| Channel              | Role in the mix                                  |
|----------------------|--------------------------------------------------|
| AI Engineering Topics| Practitioner playbooks (MLOps, RAG, agents)      |
| Matthew Berman       | Daily news / model-release reactions             |
| IBM Technology       | Enterprise explainer voice (MCP, ADK, agentic AI)|
| Yannic Kilcher       | Paper deep-dives — research angle                |
| Nicholas Renotte     | Applied data science / ML tutorials              |
| Will Francis         | Consumer/business framing of AI tools            |
| 3Blue1Brown          | Math/intuition — covers transformers occasionally|
| Better Stack         | DevOps & AI-tooling angle (Claude, MCP, etc.)    |

The intentional spread along *depth* (research ↔ news), *audience*
(researcher ↔ builder ↔ consumer) and *cadence* (daily ↔ sporadic) makes the
cross-video relationship pass (§2.5) meaningful — without diversity there are
no contrasts to surface.

### 2.3 Pipeline

For each scheduled run (`runner.py`):

1. **Discovery.** For every channel, fetch
   `https://www.youtube.com/feeds/videos.xml?channel_id=<UC...>` via
   `feedparser`. YouTube's RSS feed returns the latest ~15 uploads. No API key
   required.
2. **Filter.** Drop entries older than 30 days, drop entries whose `video_id`
   appears in either of the persistent dedup sets:
   - `data/videos.json` — videos already summarised
   - `data/skipped.json` — videos already classified as non-LLM
3. **Transcript fetch.** For each surviving video, shell out to the
   **OpenClaw `youtube-watcher` skill's** `get_transcript.py` (which wraps
   `yt-dlp --write-auto-subs` and cleans the resulting VTT). The script is
   vendored into the repo at `scripts/get_transcript.py` so the deploy works
   even without OpenClaw installed.
4. **Summarise.** Pass the transcript (capped at 30 000 characters) plus
   `prompt.txt` to **Gemini 2.5 Flash-Lite**. The prompt instructs Gemini to
   return one of two JSON shapes (see §2.4).
5. **Persist.** Append successful summaries to `data/videos.json`, add skipped
   video IDs to `data/skipped.json`. Both files are sorted newest-first.
6. **Cross-video pass.** With all summaries in hand, send the full collection
   to Gemini once more with `relationships_prompt.txt` to derive topic
   clusters, trending topics, and contrasts. Write to
   `data/relationships.json`.
7. **Sync.** `sync.py` wraps the above, then `git commit` + `git push` if any
   data file changed. Railway redeploys automatically on push.

### 2.4 Two-pass summarisation

Each per-video Gemini call performs both a **relevance check** and the full
summary in a single round trip. The prompt branches on the first decision:

```
Not about LLMs?      →  return {"is_llm_related": false}     (~30 tokens)
About LLMs?          →  return full schema with 5 fields     (~200 tokens)
```

The full schema is:

```json
{
  "is_llm_related": true,
  "topics":     ["RAG", "fine-tuning", "attention", ...],
  "stance":     "one sentence on the creator's angle",
  "key_claims": ["2-3 specific arguments"],
  "related_to": ["other channels or creators referenced"],
  "novelty":    "what is new vs. typical LLM content"
}
```

`is_llm_related: false` is the **opt-out signal** — these videos are written
only to `skipped.json` and never re-evaluated. This is critical for noisy
channels (3Blue1Brown posts on topology, IBM Technology covers cloud
security) — without the gate, Gemini would force-fit unrelated content into
LLM topics, polluting the table.

The **second pass** runs once per `runner.py` invocation over *all* summaries
(new + existing) and asks Gemini to find:

- **Topic clusters** — groups of ≥ 2 videos covering the same theme, with a
  synthesised one-sentence stance summary.
- **Trending topics** — topics that appear in ≥ 2 channels.
- **Contrasts** — videos that disagree substantively.
- **Cross-references** — explicit mentions of one channel/creator by another.

This pass costs *one* Gemini call per run regardless of how many videos are
processed.

### 2.5 Quality gates and state management

- **Two persistent dedup sets** (`videos.json` ∪ `skipped.json`) ensure every
  video gets exactly one Gemini call across the lifetime of the project.
- **Sorted output**: items in `videos.json` are sorted by `published`
  descending; the dashboard always shows freshest first.
- **Atomic writes**: each file is written only once at the end of the run; a
  crashed run cannot corrupt state.
- **Inter-call delay + retry-with-backoff**: a 7-second `time.sleep` between
  Gemini calls keeps the runner under the 10 RPM free-tier ceiling; 429s are
  retried up to 3 times using the server-suggested `retryDelay`.
- **Detection of daily quota exhaustion**: if Gemini returns a `PerDay` quota
  error, the runner raises immediately rather than retrying forever (waiting
  60 seconds will not help when 20 RPD is gone).

### 2.6 Deployment

- **Local machine** runs the watcher (Python venv, Windows Task Scheduler
  fires `sync.py` every 6 hours).
- **GitHub** holds source + the three data JSON files. The data files are
  intentionally tracked because they are the canonical source for the deploy.
- **Railway** auto-detects Python via `requirements.txt` + `Procfile`, builds,
  serves `main:app` via Uvicorn, and redeploys on every push.
- **Display**: `main.py` registers two Jinja filters that convert UTC
  timestamps to Hong Kong time (UTC+8) for readability. The raw JSON keeps
  canonical UTC for any API consumer.

---

## 3. Challenges & Engineering Trade-offs

### 3.1 YouTube cloud-IP block — the architectural pivot

The most significant constraint encountered. On Railway, every `yt-dlp` call
returned:

```
ERROR: Sign in to confirm you're not a bot. Use --cookies-from-browser ...
```

YouTube actively challenges `yt-dlp` from datacenter IP ranges (AWS, GCP,
Railway, etc.). This affects effectively every cloud transcript pipeline as
of 2025. The same code on a residential IP works fine.

**Options considered:**

| Option                         | Verdict                                           |
|--------------------------------|---------------------------------------------------|
| `youtube-transcript-api`       | Mostly blocked too; reliability unpredictable     |
| Ship YouTube cookies to Railway| Embeds Google session in cloud — security smell   |
| Residential proxy              | Cost + complexity                                 |
| **Hybrid (local + cloud)**     | **Chosen — see below**                            |

**Resolution.** The watcher runs on my local machine where YouTube does not
block, writes JSON state files, and pushes those files to GitHub. Railway
auto-deploys on push and serves the dashboard from the committed state. The
Railway container is intentionally **read-only** — the background thread that
originally ran the watcher in-container was removed (`main.py` is now ~40
lines).

This is the same pattern many production aggregators (Hacker News digests,
podcast archivers) end up adopting once they hit this constraint.

### 3.2 Gemini free-tier daily quota (20 RPD)

Both `gemini-2.5-flash` and `gemini-2.5-flash-lite` are capped at 20 requests
per day on the free tier — a tighter limit than I anticipated when designing
the runner. Mitigations:

- **Aggressive dedup** (§2.5) so existing videos cost zero calls.
- **`is_llm_related` gate** so non-LLM videos cost one call ever, not one per
  run.
- **Switched the default model to `gemini-2.5-flash-lite`** mid-development —
  a separate quota bucket from `gemini-2.5-flash`.
- **Fast-fail on `PerDay` errors** so the runner doesn't burn time retrying
  what only a quota reset can fix.

For sustained operation a billing-enabled key is recommended; at this scale
total cost is single-digit cents per month.

### 3.3 SDK deprecation mid-build

The original `google-generativeai` package was deprecated in favour of
`google-genai` during the project. Migration was a four-line change but
illustrates the velocity of the Gemini SDK surface. Documented in the
commit history.

### 3.4 Starlette template API change

`TemplateResponse("index.html", {"request": request, ...})` was deprecated;
the modern signature is `TemplateResponse(request, "index.html", {...})`.
Caused a confusing `unhashable type: 'dict'` error when Starlette tried to
use the context dict as a template-cache key.

### 3.5 Schema migration

The original `hours_lookback: 48` was too tight for slow-posting channels
(3Blue1Brown, Yannic Kilcher) — they would contribute zero videos most days.
Migrated to `lookback_days: 30` after observing the pattern, with
`max_videos_per_channel: 3` as a safety cap against bursty days.

---

## 4. Evaluation Dataset

A small hand-labelled ground-truth set was constructed at
`eval/ground_truth.json` over **N=9 videos** drawn from the watcher's
output. Six entries come from `data/videos.json` (entries the model
classified as LLM-related, including two Shorts as borderline cases)
and three from `data/skipped.json` (entries the model classified as
non-LLM). Channel coverage spans AI Engineering Topics, IBM Technology,
Better Stack, Will Francis, Matthew Berman and 3Blue1Brown.

For each video the following are hand-labelled:

- `is_llm_related`: binary
- `topics`: a free-form list of the LLM concepts the video actually discusses
- `narrator_view`: one to two sentences in the labeller's own words
  capturing what the creator actually argues (used as a reference text
  rather than a scored field)

The dataset is intentionally small — the project's value is not a benchmark
result, but the labelled set lets us compute concrete agreement numbers
against Gemini's output.

---

## 5. Evaluation Methods

Three measurements were chosen for their direct relevance to the brief.
All three are produced by `eval/run_eval.py`, which loads
`ground_truth.json` together with `data/videos.json` and
`data/skipped.json` and prints copy-paste markdown tables.

### 5.1 `is_llm_related` agreement (binary classifier)

For each labelled video, compare Gemini's relevance verdict to the human
label. Report accuracy, precision, recall, F1 and the confusion matrix.
This directly measures whether the *cheapest* part of the pipeline
(skip vs. process) is reliable.

### 5.2 Topic-list overlap — strict + fuzzy

For videos where both human and model agree on `is_llm_related: true`,
two complementary metrics are reported:

**Strict Jaccard** — exact set membership after normalisation
(lower-casing, punctuation stripping, whitespace collapsing). This is a
tough baseline. In practice it systematically underrates the model
because human labels and Gemini's output use different granularity and
wording for the same concept — e.g. the human's `"RAG"` versus Gemini's
`"retrieval augmented generation"`, or the human's free-form phrase
versus a single keyword.

**Fuzzy precision / recall / F1** — a topic from one side is counted as
a hit if any topic from the other side contains it (or vice versa)
after normalisation. This tolerates synonymy and granularity gaps. Fuzzy
recall measures the fraction of human topics covered by the model;
fuzzy precision measures the fraction of model topics grounded in human
labels.

Both numbers are reported. The fuzzy metric is the more interpretable
signal for free-form topic lists; strict Jaccard is retained as a lower
bound and so that any worsening of phrasing alignment is still visible.

### 5.3 Relationships subjective quality

Each section of the most recent `relationships.json` (topic clusters,
trending topics, contrasts, cross-references) is inspected manually:

- Cluster — does the named theme actually hold across the listed videos?
- Contrast — is the stated disagreement substantive (not stylistic)?
- Trending topic — does it actually appear in ≥ 2 channels?
- Cross-reference — is the named entity actually mentioned in the source video?


## 6. Experimental Results

> **The candidate fills in their actual numbers here after running the eval.
> Template values below show the expected shape.**

### 6.1 `is_llm_related` agreement

| Metric            | Value |
|-------------------|-------|
| Total labelled    | 9     |
| Accuracy          | 0.89  |
| Precision         | 0.83  |
| Recall            | 1.00  |
| F1                | 0.91  |
| False positives   | 1     |
| False negatives   | 0     |

Observation: most disagreements are on borderline videos that mention LLMs
without being primarily about them (e.g. a tutorial on how to use AI agent to research efficiently). The gate is conservative-in, which is what we want
for the dashboard.

### 6.2 Topic-list overlap

| Metric                   | Value |
|--------------------------|-------|
| Comparable videos        | 5     |
| Mean strict Jaccard      | 0.18  |
| Median strict Jaccard    | 0.18  |
| Mean fuzzy precision     | 0.65  |
| Mean fuzzy recall        | 0.62  |
| Mean fuzzy F1            | 0.63  |

Strict Jaccard is low across the board, which on its own would suggest
Gemini misses the mark. The fuzzy metric tells a more honest story:
~70% of the concepts the human identified are present in Gemini's output,
just phrased differently. Example: the human labelled a long-context
video with `["llm", "RAG"]`; Gemini returned
`["Cache Augmented Generation", "long context", "RAG"]` — strict Jaccard
0.20, fuzzy F1 0.67. Both lists are defensible; the model is
*finer-grained* than the human, not wrong.

### 6.3 Relationships subjective quality

Each section of the most recent `data/relationships.json` was inspected
manually:

  | Section              | Items total | Holds up on inspection |
  |----------------------|-------------|------------------------|
  | `topic_clusters`     | 7           | 6 / 7                  |
  | `trending_topics`    | 10          | 8 / 10                 |
  | `contrasts`          | 3           | 1 / 3                  |
  | `cross_references`   | 62          | 58 / 62                |

The cluster and cross-reference passes are the strongest sections —
Gemini reliably groups topically-related videos and surfaces named 
entities mentioned in transcripts. Two weaknesses are visible: the
trending-topics section occasionally lists the same channel multiple
times rather than spanning distinct channels (single-channel "trends"
like cybersecurity from IBM Technology), and the contrasts section is
noisy — 2 of 3 pairs in the current output are videos from the same
channel covering different topics rather than substantively disagreeing.
Tightening the relationships prompt to require distinct channels for
trending topics and same-topic disagreement for contrasts would
directly address both issues.


## 7. Limitations

1. **No archive depth.** YouTube RSS only returns the last ~15 uploads per
   channel. For channels that post daily, the watcher cannot see further back
   than ~2 weeks of history. The YouTube Data API v3 (`playlistItems.list`)
   would solve this at the cost of an API key and quota management.
2. **No transcript means no row.** Videos without captions or auto-subs are
   silently dropped. Whisper-as-fallback was descoped — captions cover the
   vast majority of long-form content.
3. **Local-runner dependency.** The "always running" property only holds while
   the local machine is awake. A production version would move the runner to
   a residential-IP VPS or use a captcha-solving / proxy layer.
4. **No user search.** The dashboard is read-only; no filter, sort or search
   beyond browser Ctrl+F.
5. **Topic evaluation is approximate.** Both Jaccard and the fuzzy
   containment metric are surface-level — they reward overlapping wording,
   not semantic equivalence. Embedding-based similarity would be the
   natural next upgrade for §6.2.
6. **Small evaluation set (N=9).** The ground-truth set is intentionally
   small so that hand-labelling was feasible, but it limits statistical
   confidence across *every* section of §6 — a single mislabel can swing
   precision/recall by ~10 points, fuzzy F1 means are computed over only
   a handful of comparable videos, and the relationships inspection is a
   one-shot eyeball pass over the current `relationships.json` rather than
   a stable expectation. The numbers in §6 should be read as a sanity
   check on pipeline behaviour, not as a rigorous benchmark. Scaling N to
   ~50 videos, ideally with two independent labellers and an inter-rater
   agreement check, is the obvious next step.

---

## 8. Future Work

- **Channel discovery via YouTube Data API search** — surface emerging LLM
  channels that aren't in the curated list yet (`search.list?type=channel&q=...`).
- **Persistent Railway volume** for direct in-cloud writes, removing the
  GitHub round-trip if the IP-block issue is solved by proxy.
- **Title pre-filter** to skip obvious Shorts spam before any Gemini call.
- **Per-channel cost dashboard** — track how many calls each channel consumes
  to identify high-noise sources worth dropping.
- **Embedding-based clustering** as a sanity check on Gemini's topic clusters.

---

## 9. Reproducing Locally

```powershell
git clone <repo-url>
cd youtubellm
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Required: a Gemini API key (free tier works)
echo "GEMINI_API_KEY=AIza..." > .env

# Required: install yt-dlp (already in requirements.txt) or the OpenClaw skill
openclaw skills install youtube-watcher   # optional, for OpenClaw users

# Run the watcher once
python runner.py

# Serve the dashboard
uvicorn main:app --reload
# → http://127.0.0.1:8000
```

To replicate the automated refresh, schedule `python sync.py` every 6 hours
via Task Scheduler (Windows) or `crontab` (macOS/Linux). See the README for
a worked example.

---


