"""
Comparator — parallel bot match runner
Runs N engine matches concurrently, reports wins and bankroll.
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import time
import concurrent.futures
import threading
from typing import Dict, Optional

from config import BOT_1_NAME, BOT_2_NAME, PYTHON_CMD


def parse_bankrolls(output: str) -> Optional[Dict[str, int]]:
    """Extract bot name → bankroll from engine output."""
    bankrolls: Dict[str, int] = {}
    current_bot: Optional[str] = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Stats for ") and line.endswith(":"):
            current_bot = line[len("Stats for "):-1].strip()
        elif current_bot and line.startswith("Total Bankroll:"):
            bankrolls[current_bot] = int(line.split(":", 1)[1].strip())
            current_bot = None
    if BOT_1_NAME in bankrolls and BOT_2_NAME in bankrolls:
        return bankrolls
    return None


def run_one_match(match_id: int, project_root: str) -> Optional[Dict[str, int]]:
    result = subprocess.run(
        [PYTHON_CMD, "engine.py", "--small_log"],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return parse_bankrolls(result.stdout)


def cleanup_logs(logs_dir: str, pre_glogs: set, pre_plogs: set):
    for path in set(glob.glob(os.path.join(logs_dir, "*.glog"))) - pre_glogs:
        try: os.remove(path)
        except OSError: pass
    for path in set(glob.glob(os.path.join(logs_dir, "*.plog"))) - pre_plogs:
        try: os.remove(path)
        except OSError: pass


def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}h {m:02d}m {s:02d}s" if h else f"{m:02d}m {s:02d}s"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two bots over many matches.")
    parser.add_argument("--count", type=int, default=100, help="Number of matches (default: 100)")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers (default: 8)")
    parser.add_argument("--progress-every", type=int, default=10, help="Progress interval (default: 10)")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(project_root, "logs")

    print(f"Running {args.count} matches ({args.workers} workers): {BOT_1_NAME} vs {BOT_2_NAME}\n")
    start = time.perf_counter()

    results = []
    done = 0
    lock = threading.Lock()

    def _on_done(future):
        nonlocal done
        r = future.result()
        with lock:
            if r:
                results.append(r)
            done += 1
            if done % args.progress_every == 0 or done == args.count:
                b1w = sum(1 for x in results if x[BOT_1_NAME] > x[BOT_2_NAME])
                b2w = sum(1 for x in results if x[BOT_2_NAME] > x[BOT_1_NAME])
                print(f"  {done}/{args.count} | {fmt_time(time.perf_counter() - start)} | "
                      f"{BOT_1_NAME}: {b1w}  {BOT_2_NAME}: {b2w}")

    pre_glogs = set(glob.glob(os.path.join(logs_dir, "*.glog")))
    pre_plogs = set(glob.glob(os.path.join(logs_dir, "*.plog")))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(run_one_match, i, project_root) for i in range(args.count)]
        for f in futures:
            f.add_done_callback(_on_done)
        concurrent.futures.wait(futures)

    cleanup_logs(logs_dir, pre_glogs, pre_plogs)

    elapsed = time.perf_counter() - start
    total = len(results)
    if not total:
        print("No successful matches!")
        sys.exit(1)

    b1_wins = sum(1 for r in results if r[BOT_1_NAME] > r[BOT_2_NAME])
    b2_wins = sum(1 for r in results if r[BOT_2_NAME] > r[BOT_1_NAME])
    ties = total - b1_wins - b2_wins

    sep = "=" * 50
    print(f"\n{sep}")
    print(f"  {BOT_1_NAME} vs {BOT_2_NAME}  ({total} matches, {fmt_time(elapsed)})")
    print(f"{sep}")
    print(f"  {BOT_1_NAME} Wins:  {b1_wins}/{total}  ({b1_wins/total*100:.1f}%)")
    print(f"  {BOT_2_NAME} Wins:  {b2_wins}/{total}  ({b2_wins/total*100:.1f}%)")
    print(f"  Ties:         {ties}/{total}")
    print(f"{sep}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
