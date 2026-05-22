# LLM YouTube Channel Tracker

A self-updating dashboard that watches popular LLM-focused YouTube channels,
transcribes each new video, and surfaces what creators actually say about
LLM topics — plus how the channels relate to each other on shared themes.

🔗 **Live dashboard:** https://ntmphg-youtube-tracker.up.railway.app/
📄 **Full report:** [REPORT.md](./REPORT.md)

---

## What it does

- Polls 8 curated LLM-focused YouTube channels every 6 hours via RSS
- Fetches each new video's transcript with `yt-dlp` (vendored OpenClaw `youtube-watcher` skill)
- Summarises with **Gemini 2.5 Flash-Lite** into structured JSON: topics,
  creator stance, key claims, related creators, novelty
- Runs a second cross-video pass that surfaces topic clusters, trending
  themes, contrasts and cross-references across channels
- Serves everything as a public dashboard that auto-refreshes every 5 min

## Architecture in one line

```
Local machine (runner.py) ──► git push ──► GitHub ──► Railway redeploy ──► public dashboard
```

The watcher runs locally because YouTube blocks `yt-dlp` from cloud
datacenter IPs (see [REPORT.md §3.1](./REPORT.md#31-youtube-cloud-ip-block--the-architectural-pivot)).
Railway is intentionally read-only.

## Quick start (local)

```powershell
git clone https://github.com/ntmphg/youtubellm
cd youtubellm
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Free Gemini API key at https://aistudio.google.com/apikey
echo "GEMINI_API_KEY=AIza..." > .env

# Run the watcher once (~10-15 min on first cold run)
python runner.py

# Serve the dashboard
uvicorn main:app --reload
# → http://127.0.0.1:8000
```

## Automating the refresh

Schedule `python sync.py` every 6 hours via Windows Task Scheduler or
`cron`. Each run polls channels, summarises new videos, commits the
updated JSON files, and pushes to GitHub. Railway redeploys
automatically on every push.

## File layout

```
youtubellm/
├── runner.py                  # The watcher pipeline
├── sync.py                    # Wrapper: run watcher → git commit data → push
├── main.py                    # FastAPI dashboard (read-only)
├── channels.json              # Channel list + lookback config
├── prompt.txt                 # Per-video summarisation prompt
├── relationships_prompt.txt   # Cross-video summarisation prompt
├── data/                      # Generated JSON state (committed to git)
│   ├── videos.json
│   ├── skipped.json
│   └── relationships.json
├── scripts/get_transcript.py  # Vendored OpenClaw skill (yt-dlp wrapper)
├── eval/                      # Hand-labelled ground truth + eval script
├── templates/index.html       # Dashboard
└── REPORT.md                  # Full methodology + evaluation results
```

## Tech stack

Python 3.11 · FastAPI · Jinja2 · Gemini 2.5 Flash-Lite (via `google-genai`)
· `feedparser` · `yt-dlp` · Railway

## Evaluation

A 9-video hand-labelled ground truth set in `eval/ground_truth.json`,
with `eval/run_eval.py` computing binary classifier metrics, strict
Jaccard + fuzzy precision/recall on topic lists, and a manual inspection
of the cross-video relationships output. See [REPORT.md §6](./REPORT.md#6-experimental-results)
for the numbers.
