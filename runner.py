import json
import os
import datetime
import re
import subprocess
import time
from pathlib import Path

import feedparser
from dotenv import load_dotenv
from google import genai

load_dotenv()

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "channels.json"
PROMPT_PATH = ROOT / "prompt.txt"
RELATIONSHIPS_PROMPT_PATH = ROOT / "relationships_prompt.txt"
OUTPUT_PATH = ROOT / "data" / "videos.json"
SKIPPED_PATH = ROOT / "data" / "skipped.json"
RELATIONSHIPS_PATH = ROOT / "data" / "relationships.json"

# OpenClaw youtube-watcher skill — installed via `openclaw skills install youtube-watcher`
SKILL_SCRIPT = Path.home() / ".openclaw" / "workspace" / "skills" / "youtube-watcher" / "scripts" / "get_transcript.py"

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
MAX_TRANSCRIPT_CHARS = 30000
GEMINI_INTER_CALL_DELAY = 7   # ~8.5 RPM, safely under 10 RPM free-tier limit
GEMINI_MAX_RETRIES = 3

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL_NAME = "gemini-2.5-flash-lite"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_prompt():
    with open(PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


def load_existing():
    if not OUTPUT_PATH.exists():
        return set(), []
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    seen_ids = {item.get("video_id") for item in items if item.get("video_id")}
    return seen_ids, items


def load_skipped():
    if not SKIPPED_PATH.exists():
        return set()
    with open(SKIPPED_PATH, encoding="utf-8") as f:
        return set(json.load(f).get("video_ids", []))


def save_skipped(skipped_ids):
    SKIPPED_PATH.parent.mkdir(exist_ok=True)
    with open(SKIPPED_PATH, "w", encoding="utf-8") as f:
        json.dump({"video_ids": sorted(skipped_ids)}, f, indent=2)


def fetch_new_videos(channel_id, seen_ids, lookback_days, max_per_channel):
    feed = feedparser.parse(RSS_URL.format(channel_id=channel_id))
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)
    videos = []
    for entry in feed.entries:
        if entry.yt_videoid in seen_ids:
            continue
        published = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
        if published < cutoff:
            continue
        videos.append({
            "video_id": entry.yt_videoid,
            "title": entry.title,
            "url": entry.link,
            "published": published.isoformat(),
        })
        if len(videos) >= max_per_channel:
            break
    return videos


def fetch_transcript(video_url):
    """Invokes the OpenClaw youtube-watcher skill's get_transcript.py."""
    try:
        result = subprocess.run(
            ["python", str(SKILL_SCRIPT), video_url],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip().splitlines()
            print(f"  no transcript ({err[-1] if err else 'unknown error'})")
            return None
        return result.stdout[:MAX_TRANSCRIPT_CHARS]
    except Exception as e:
        print(f"  transcript error: {e}")
        return None


def compute_relationships(items):
    """Second-pass: feed all summaries to Gemini to find cross-video relationships."""
    if len(items) < 2:
        print("\nSkipping relationships (need at least 2 videos)")
        return None
    with open(RELATIONSHIPS_PROMPT_PATH, encoding="utf-8") as f:
        template = f.read()
    payload = [
        {
            "video_id": it["video_id"],
            "channel": it["channel"],
            "title": it["title"],
            "summary": it["summary"],
        }
        for it in items
    ]
    prompt = template.replace("{summaries}", json.dumps(payload, indent=2))
    try:
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        text = response.text.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception as e:
        print(f"  relationships pass failed: {e}")
        return None


def summarize(transcript, prompt_template):
    prompt = prompt_template.replace("{transcript}", transcript)
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
            text = response.text.strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
            return json.loads(text)
        except Exception as e:
            err = str(e)
            is_rate_limit = "429" in err or "RESOURCE_EXHAUSTED" in err or "503" in err
            is_daily_quota = "PerDay" in err or "GenerateRequestsPerDayPerProjectPerModel" in err
            if is_daily_quota:
                raise RuntimeError("daily quota exhausted — retry tomorrow or switch model") from e
            if not is_rate_limit or attempt == GEMINI_MAX_RETRIES - 1:
                raise
            match = re.search(r"retry in ([\d.]+)s", err)
            wait = float(match.group(1)) + 2 if match else 30
            print(f"    rate-limited, waiting {wait:.0f}s (retry {attempt + 1}/{GEMINI_MAX_RETRIES})")
            time.sleep(wait)


def run_watcher():
    config = load_config()
    prompt_template = load_prompt()
    seen_ids, existing_items = load_existing()
    skipped_ids = load_skipped()
    dedup_ids = seen_ids | skipped_ids
    is_first_run = len(existing_items) == 0
    print(
        f"{'First run — backfilling last ' + str(config['lookback_days']) + ' days' if is_first_run else f'Incremental — {len(seen_ids)} kept, {len(skipped_ids)} skipped'}"
    )

    new_items = []
    new_skips = 0
    for channel in config["channels"]:
        print(f"\n[{channel['name']}]")
        videos = fetch_new_videos(
            channel["id"],
            dedup_ids,
            config["lookback_days"],
            config["max_videos_per_channel"],
        )
        if not videos:
            print("  no new videos in window")
            continue
        for v in videos:
            print(f"  {v['title'][:60]}")
            transcript = fetch_transcript(v["url"])
            if not transcript:
                continue
            try:
                summary = summarize(transcript, prompt_template)
            except Exception as e:
                print(f"  summarize failed: {e}")
                continue
            if not summary.get("is_llm_related", True):
                print("    skipped — not LLM-related")
                skipped_ids.add(v["video_id"])
                new_skips += 1
                continue
            new_items.append({
                "channel": channel["name"],
                "video_id": v["video_id"],
                "title": v["title"],
                "url": v["url"],
                "published": v["published"],
                "summary": summary,
            })
            time.sleep(GEMINI_INTER_CALL_DELAY)

    all_items = sorted(
        new_items + existing_items,
        key=lambda x: x.get("published", ""),
        reverse=True,
    )
    output = {
        "items": all_items,
        "last_updated": datetime.datetime.utcnow().isoformat(),
    }
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    save_skipped(skipped_ids)
    print(f"\nAdded {len(new_items)} new videos, skipped {new_skips} non-LLM. Total in dashboard: {len(all_items)}")

    # Second pass: cross-video relationships
    print("\nComputing cross-video relationships...")
    relationships = compute_relationships(all_items)
    if relationships:
        with open(RELATIONSHIPS_PATH, "w", encoding="utf-8") as f:
            json.dump({"data": relationships, "last_updated": datetime.datetime.utcnow().isoformat()}, f, indent=2)
        print(f"  saved {len(relationships.get('topic_clusters', []))} clusters, "
              f"{len(relationships.get('trending_topics', []))} trending topics, "
              f"{len(relationships.get('contrasts', []))} contrasts")


if __name__ == "__main__":
    run_watcher()
