"""Run the watcher locally, then commit any data changes and push to GitHub.

Designed to be wired into Windows Task Scheduler / cron for hands-off refresh.
The Railway deploy auto-redeploys on every push, so a fresh data file lands
on the public dashboard within ~1 min of this script completing.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
TRACKED_FILES = [
    "data/videos.json",
    "data/skipped.json",
    "data/relationships.json",
]


def run(cmd, check=True):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=True, text=True)


def main():
    # 1. Run the watcher
    result = subprocess.run([sys.executable, str(ROOT / "runner.py")], cwd=ROOT)
    if result.returncode != 0:
        print("runner.py exited non-zero — aborting sync")
        sys.exit(1)

    # 2. Stage only the tracked data files that actually exist
    existing = [f for f in TRACKED_FILES if (ROOT / f).exists()]
    if not existing:
        print("no data files yet — nothing to sync")
        return
    run(["git", "add", *existing])

    # 3. Check if anything actually changed (`git diff --cached --quiet` exits 1 on diff)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT
    )
    if diff.returncode == 0:
        print("no data changes — nothing to commit")
        return

    # 4. Commit and push
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    run(["git", "commit", "-m", f"Auto-update dashboard data ({stamp})"])
    run(["git", "push"])
    print("synced.")


if __name__ == "__main__":
    main()
