"""Compare hand-labelled ground truth to the model's output.

Usage:
    python eval/run_eval.py                # run the eval
    python eval/run_eval.py --worksheet    # print a rating worksheet:
                                           #   per video, shows Gemini's stance/claims/novelty
                                           #   alongside the human's narrator_view so you can
                                           #   rate each field on the 1-5 scale

Loads:
    eval/ground_truth.json   — hand labels (binary, topics, narrator_view, 1-5 ratings)
    data/videos.json         — model output marked is_llm_related: true
    data/skipped.json        — model output marked is_llm_related: false

Prints:
    §6.1  is_llm_related binary classifier (precision, recall, F1, confusion)
    §6.2  Topic-list Jaccard
    §6.3  Stance / claims / novelty / overall — mean 1-5 ratings
    Markdown tables ready to paste into REPORT.md.
"""

import argparse
import json
import re
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = ROOT / "eval" / "ground_truth.json"
VIDEOS_JSON = ROOT / "data" / "videos.json"
SKIPPED_JSON = ROOT / "data" / "skipped.json"


def normalise(topic):
    t = topic.lower().strip()
    t = re.sub(r"[^a-z0-9\s\-]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def jaccard(a, b):
    sa = {normalise(x) for x in a if x}
    sb = {normalise(x) for x in b if x}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def fuzzy_topic_metrics(human, model):
    """Loose precision / recall on topics.

    A human topic is considered 'hit' if any model topic contains it or vice
    versa after normalisation. This handles synonymy and granularity gaps
    (e.g. 'RAG' vs 'retrieval augmented generation') that strict Jaccard
    misses. Symmetric for model topics.
    """
    h = [normalise(x) for x in human if x]
    m = [normalise(x) for x in model if x]
    if not h and not m:
        return 1.0, 1.0, 1.0
    if not h or not m:
        return 0.0, 0.0, 0.0

    def hits(needles, haystack):
        n = 0
        for x in needles:
            for y in haystack:
                if x and y and (x in y or y in x):
                    n += 1
                    break
        return n

    recall = hits(h, m) / len(h)           # of human topics, what fraction is covered by the model?
    precision = hits(m, h) / len(m)        # of model topics, what fraction is grounded in human labels?
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def load_model_state():
    """Return dict: video_id -> {'is_llm_related', 'topics', 'summary': full dict}."""
    state = {}
    videos = json.load(open(VIDEOS_JSON, encoding="utf-8"))["items"]
    for item in videos:
        state[item["video_id"]] = {
            "is_llm_related": True,
            "topics": item["summary"].get("topics", []),
            "summary": item["summary"],
            "title": item["title"],
            "channel": item["channel"],
        }
    skipped = json.load(open(SKIPPED_JSON, encoding="utf-8")).get("video_ids", [])
    for vid in skipped:
        state[vid] = {"is_llm_related": False, "topics": [], "summary": None}
    return state


def worksheet():
    """Print a rating worksheet for each labelled video."""
    gt = json.load(open(GROUND_TRUTH, encoding="utf-8"))
    model = load_model_state()
    print("=" * 70)
    print("RATING WORKSHEET — open eval/ground_truth.json side-by-side")
    print("=" * 70)
    for l in gt["labels"]:
        vid = l["video_id"]
        m = model.get(vid)
        print(f"\n--- {vid}  [{l['channel']}] ---")
        print(f"Title:           {l['title']}")
        print(f"URL:             {l['url']}")
        print(f"Your narrator_view: {l.get('narrator_view') or '<EMPTY — fill in stage 1>'}")
        if m is None:
            print("Model state:     <not in videos.json or skipped.json>")
            continue
        if not m["is_llm_related"]:
            print("Model output:    skipped (is_llm_related=false). No ratings to fill.")
            continue
        s = m["summary"]
        print(f"\nGemini stance:        {s.get('stance', '')}")
        print(f"Gemini key_claims:")
        for c in s.get("key_claims", []):
            print(f"    - {c}")
        print(f"Gemini novelty:       {s.get('novelty', '')}")
        print(f"Gemini topics:        {', '.join(s.get('topics', []))}")
        print(f"\nNow rate (1-5) and put numbers in ground_truth.json:")
        print(f"  stance_rating, claims_rating, novelty_rating, overall_rating")


def main():
    gt = json.load(open(GROUND_TRUTH, encoding="utf-8"))
    labels = gt["labels"]
    model = load_model_state()

    unlabelled = [l for l in labels if l["is_llm_related"] is None]
    if unlabelled:
        print(f"WARNING: {len(unlabelled)}/{len(labels)} entries still have is_llm_related=null.")
        print("        Fill in eval/ground_truth.json first, then re-run.\n")

    # ─── §6.1  is_llm_related binary classifier ────────────────────────
    tp = fp = tn = fn = 0
    missing = []
    for l in labels:
        vid = l["video_id"]
        if vid not in model:
            missing.append(vid)
            continue
        if l["is_llm_related"] is None:
            continue
        human = bool(l["is_llm_related"])
        machine = bool(model[vid]["is_llm_related"])
        if human and machine:
            tp += 1
        elif (not human) and (not machine):
            tn += 1
        elif machine and (not human):
            fp += 1
        elif (not machine) and human:
            fn += 1

    total = tp + tn + fp + fn
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else float("nan")
    accuracy = (tp + tn) / total if total else float("nan")

    print("=" * 60)
    print("§6.1  is_llm_related — binary classifier")
    print("=" * 60)
    print(f"  Total labelled (compared):  {total}")
    print(f"  Accuracy:                   {accuracy:.2f}")
    print(f"  Precision:                  {precision:.2f}")
    print(f"  Recall:                     {recall:.2f}")
    print(f"  F1:                         {f1:.2f}")
    print()
    print("  Confusion matrix:")
    print(f"                  model: LLM   model: not")
    print(f"   human: LLM       {tp:>3}          {fn:>3}")
    print(f"   human: not       {fp:>3}          {tn:>3}")
    if missing:
        print(f"\n  Missing from model state: {missing}")
    print()

    # ─── §6.2  Topic overlap (Jaccard + fuzzy precision/recall) ────────
    print("=" * 60)
    print("§6.2  Topic-list overlap (LLM-related only)")
    print("=" * 60)
    rows = []
    for l in labels:
        vid = l["video_id"]
        if vid not in model:
            continue
        if not (l["is_llm_related"] and model[vid]["is_llm_related"]):
            continue
        j = jaccard(l["topics"], model[vid]["topics"])
        p, r, f = fuzzy_topic_metrics(l["topics"], model[vid]["topics"])
        rows.append((vid, l["title"][:50], j, p, r, f, l["topics"], model[vid]["topics"]))
    if rows:
        jaccards = [r[2] for r in rows]
        precisions = [r[3] for r in rows]
        recalls = [r[4] for r in rows]
        f1s = [r[5] for r in rows]
        print(f"  Comparable videos:   {len(rows)}\n")
        print("  STRICT (exact set match)")
        print(f"    Mean Jaccard:        {mean(jaccards):.2f}")
        print(f"    Median Jaccard:      {median(jaccards):.2f}")
        print(f"    Min / Max:           {min(jaccards):.2f} / {max(jaccards):.2f}\n")
        print("  FUZZY (substring containment — handles synonymy/granularity)")
        print(f"    Mean precision:      {mean(precisions):.2f}   (fraction of model topics grounded in human labels)")
        print(f"    Mean recall:         {mean(recalls):.2f}   (fraction of human topics covered by the model)")
        print(f"    Mean F1:             {mean(f1s):.2f}\n")
        print("  Per-video breakdown:")
        print(f"    {'Jacc':<6}{'P':<6}{'R':<6}{'F1':<6}  Title")
        for vid, title, j, p, r, f, _, _ in sorted(rows, key=lambda r: r[5]):
            print(f"    {j:<6.2f}{p:<6.2f}{r:<6.2f}{f:<6.2f}  {title}")
        print()
        print("  Pairs where fuzzy matched but Jaccard missed:")
        any_diff = False
        for vid, title, j, p, r, f, h_topics, m_topics in rows:
            if j < 0.5 and f >= 0.5:
                any_diff = True
                print(f"    [{vid}]  Jaccard {j:.2f}  →  F1 {f:.2f}")
                print(f"      human: {h_topics}")
                print(f"      model: {m_topics}")
        if not any_diff:
            print("    (none)")
        print()
    else:
        print("  No videos where both human and model agree is_llm_related=true.\n")

    # ─── §6.3  Stance / claims / novelty / overall ratings ─────────────
    print("=" * 60)
    print("§6.3  Stance / claims / novelty / overall (1-5 human ratings)")
    print("=" * 60)
    fields = ["stance_rating", "claims_rating", "novelty_rating", "overall_rating"]
    aggregated = {f: [] for f in fields}
    for l in labels:
        for f in fields:
            v = l.get(f)
            if isinstance(v, (int, float)):
                aggregated[f].append(v)
    if any(aggregated.values()):
        for f in fields:
            vals = aggregated[f]
            if vals:
                print(f"  {f:<18}  n={len(vals):>2}   mean={mean(vals):.2f}   median={median(vals):.1f}   min={min(vals)}  max={max(vals)}")
            else:
                print(f"  {f:<18}  no ratings yet")
        print()
    else:
        print("  No ratings filled in yet.")
        print("  Tip: run `python eval/run_eval.py --worksheet` to see Gemini's output per video,\n        then fill in the *_rating fields in ground_truth.json.\n")

    # ─── Markdown blocks for REPORT.md ─────────────────────────────────
    print("=" * 60)
    print("PASTE INTO REPORT.md §6.1")
    print("=" * 60)
    print()
    print("| Metric            | Value |")
    print("|-------------------|-------|")
    print(f"| Total labelled    | {total} |")
    print(f"| Accuracy          | {accuracy:.2f} |")
    print(f"| Precision         | {precision:.2f} |")
    print(f"| Recall            | {recall:.2f} |")
    print(f"| F1                | {f1:.2f} |")
    print(f"| False positives   | {fp} |")
    print(f"| False negatives   | {fn} |")
    print()

    if rows:
        print("=" * 60)
        print("PASTE INTO REPORT.md §6.2")
        print("=" * 60)
        print()
        print("| Metric                   | Value |")
        print("|--------------------------|-------|")
        print(f"| Comparable videos        | {len(rows)} |")
        print(f"| Mean strict Jaccard      | {mean(jaccards):.2f} |")
        print(f"| Median strict Jaccard    | {median(jaccards):.2f} |")
        print(f"| Mean fuzzy precision     | {mean(precisions):.2f} |")
        print(f"| Mean fuzzy recall        | {mean(recalls):.2f} |")
        print(f"| Mean fuzzy F1            | {mean(f1s):.2f} |")
        print()

    if any(aggregated.values()):
        print("=" * 60)
        print("PASTE INTO REPORT.md §6.3")
        print("=" * 60)
        print()
        print("| Field            | n  | Mean | Median | Min | Max |")
        print("|------------------|----|------|--------|-----|-----|")
        for f in fields:
            vals = aggregated[f]
            if vals:
                print(f"| {f:<16} | {len(vals)} | {mean(vals):.2f} | {median(vals):.1f}    | {min(vals)}   | {max(vals)}   |")
        print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--worksheet", action="store_true", help="Print rating worksheet for each video")
    args = p.parse_args()
    if args.worksheet:
        worksheet()
    else:
        main()
