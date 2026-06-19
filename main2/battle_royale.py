"""
Battle Royale — Round-robin tournament for all bots
TRUE PARALLEL EXECUTION - Each matchup gets its own isolated directory!

ARCHITECTURE:
- Creates one directory per matchup (.matchup_0, .matchup_1, ...)
- Each matchup directory contains:
  * config.py with ABSOLUTE paths (avoids path resolution bugs)
  * engine.py (COPY, not symlink — Python resolves symlinks for imports)
  * pkbot/ symlink
- All matchups run in parallel via ThreadPoolExecutor
- Within each matchup, N matches run sequentially (same config, no races)
- No config.py race conditions — each matchup dir is exclusive
"""

import argparse
import os
import subprocess
import sys
import time
import threading
import concurrent.futures
import shutil
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURE YOUR BOTS HERE
# ═══════════════════════════════════════════════════════════════════════════
BOTS = [
    ("eqrbotv2", "eqrbotv2.py"),
    ("speedbotv2", "speedbotv2.py"),
    ("speedbotv3", "speedbotv3.py"),
    ("gtobot", "gtobot.py"),
    ("e4bot", "e4bot.py"),
    ("v3slow", "v3slow.py"),
    ("cv3slow", "cv3slow.py"),
]
# ═══════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON_PATH = os.path.join(PROJECT_ROOT, "venv", "bin", "python")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

_matchup_dirs: List[str] = []


def create_matchup_dir(idx: int, bot1: Tuple[str, str], bot2: Tuple[str, str]) -> str:
    """Create an isolated directory for one matchup with correct config."""
    mdir = os.path.join(PROJECT_ROOT, f".matchup_{idx}")
    if os.path.exists(mdir):
        shutil.rmtree(mdir)
    os.makedirs(mdir)

    # Copy engine.py (NOT symlink! Python adds script dir to sys.path[0],
    # and symlinks resolve to the target dir, importing the wrong config.)
    shutil.copy2(os.path.join(PROJECT_ROOT, "engine.py"), os.path.join(mdir, "engine.py"))

    # Symlink pkbot/ (directory symlink is fine — no config import issue)
    os.symlink(os.path.join(PROJECT_ROOT, "pkbot"), os.path.join(mdir, "pkbot"))

    # Write config.py with ABSOLUTE paths.
    # The engine does: cwd=os.path.dirname(self.file_path)
    # With absolute paths, dirname resolves correctly to PROJECT_ROOT,
    # and the bot file path is found directly.
    bot1_abs = os.path.join(PROJECT_ROOT, bot1[1])
    bot2_abs = os.path.join(PROJECT_ROOT, bot2[1])
    config_content = f"""\
PYTHON_CMD = {PYTHON_PATH!r}

BOT_1_NAME = {bot1[0]!r}
BOT_1_FILE = {bot1_abs!r}

BOT_2_NAME = {bot2[0]!r}
BOT_2_FILE = {bot2_abs!r}

GAME_LOG_FOLDER = {LOGS_DIR!r}
"""
    with open(os.path.join(mdir, "config.py"), "w") as f:
        f.write(config_content)

    _matchup_dirs.append(mdir)
    return mdir


def cleanup_matchup_dirs():
    """Remove all matchup directories."""
    for d in _matchup_dirs:
        shutil.rmtree(d, ignore_errors=True)


def parse_bankrolls(output: str, bot1_name: str, bot2_name: str) -> Optional[Dict[str, int]]:
    """Extract bot name -> bankroll from engine output."""
    bankrolls: Dict[str, int] = {}
    current_bot: Optional[str] = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Stats for ") and line.endswith(":"):
            current_bot = line[len("Stats for "):-1].strip()
        elif current_bot and line.startswith("Total Bankroll:"):
            try:
                bankrolls[current_bot] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            current_bot = None
    if bot1_name in bankrolls and bot2_name in bankrolls:
        return bankrolls
    return None


def run_one_match(matchup_dir: str, bot1_name: str, bot2_name: str) -> Optional[Dict[str, int]]:
    """Run a single match from a pre-configured matchup directory."""
    try:
        result = subprocess.run(
            [PYTHON_PATH, "engine.py", "--small_log"],
            cwd=matchup_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=180,
        )
        if result.returncode != 0:
            stderr_snip = (result.stderr or "")[:300]
            print(f"  [ERROR] {bot1_name} vs {bot2_name} RC={result.returncode}: {stderr_snip}")
            return None
        bankrolls = parse_bankrolls(result.stdout, bot1_name, bot2_name)
        if not bankrolls:
            # Debug: show last 5 lines of stdout
            tail = "\n".join(result.stdout.strip().splitlines()[-5:])
            print(f"  [WARN] Parse fail {bot1_name} vs {bot2_name}. Tail:\n{tail}")
        return bankrolls
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] {bot1_name} vs {bot2_name}")
        return None
    except Exception as e:
        print(f"  [EXCEPTION] {bot1_name} vs {bot2_name}: {e}")
        return None


def run_matchup(matchup_dir: str, bot1_name: str, bot2_name: str,
                n_matches: int, stats: dict, stats_lock: threading.Lock,
                progress: dict) -> None:
    """Run all N matches for one matchup (called from thread pool)."""
    for _ in range(n_matches):
        result = run_one_match(matchup_dir, bot1_name, bot2_name)

        with stats_lock:
            progress["done"] += 1

            if not result:
                progress["failed"] += 1
            else:
                b1 = result[bot1_name]
                b2 = result[bot2_name]

                stats[bot1_name]["matches"] += 1
                stats[bot2_name]["matches"] += 1
                stats[bot1_name]["total_bankroll"] += b1 - 5000
                stats[bot2_name]["total_bankroll"] += b2 - 5000

                if b1 > b2:
                    stats[bot1_name]["wins"] += 1
                    stats[bot2_name]["losses"] += 1
                    stats[bot1_name]["head_to_head"][bot2_name]["w"] += 1
                    stats[bot2_name]["head_to_head"][bot1_name]["l"] += 1
                elif b2 > b1:
                    stats[bot2_name]["wins"] += 1
                    stats[bot1_name]["losses"] += 1
                    stats[bot2_name]["head_to_head"][bot1_name]["w"] += 1
                    stats[bot1_name]["head_to_head"][bot2_name]["l"] += 1
                else:
                    stats[bot1_name]["ties"] += 1
                    stats[bot2_name]["ties"] += 1
                    stats[bot1_name]["head_to_head"][bot2_name]["t"] += 1
                    stats[bot2_name]["head_to_head"][bot1_name]["t"] += 1

            d = progress["done"]
            total = progress["total"]
            if d % 10 == 0 or d == total:
                f = progress["failed"]
                pct = d / total * 100
                sr = (d - f) / d * 100 if d else 0
                elapsed = time.perf_counter() - progress["start"]
                print(f"  Progress: {d}/{total} ({pct:.1f}%) | "
                      f"Success: {d-f}/{d} ({sr:.1f}%) | "
                      f"Time: {fmt_time(elapsed)}")


def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}h {m:02d}m {s:02d}s" if h else f"{m:02d}m {s:02d}s"


def main() -> None:
    parser = argparse.ArgumentParser(description="Round-robin tournament for all bots.")
    parser.add_argument("--matches-per-pair", type=int, default=50,
                        help="Matches between each pair (default: 50)")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel matchups to run at once (default: 8)")
    args = parser.parse_args()

    # Verify bot files
    print("Verifying bot files...")
    valid_bots = []
    for bot_name, bot_file in BOTS:
        path = os.path.join(PROJECT_ROOT, bot_file)
        if os.path.exists(path):
            print(f"  ✓ {bot_file}")
            valid_bots.append((bot_name, bot_file))
        else:
            print(f"  ✗ MISSING: {bot_file} — skipping {bot_name}")

    if len(valid_bots) < 2:
        print("\nNeed at least 2 bots. Exiting.")
        sys.exit(1)

    # Generate matchups
    matchups = []
    for i in range(len(valid_bots)):
        for j in range(i + 1, len(valid_bots)):
            matchups.append((valid_bots[i], valid_bots[j]))

    total_matches = len(matchups) * args.matches_per_pair

    print(f"\n{'═' * 70}")
    print("  POKER BOT BATTLE ROYALE — Round Robin Tournament")
    print(f"{'═' * 70}")
    print(f"\n  Bots: {len(valid_bots)}")
    for name, _ in valid_bots:
        print(f"    • {name}")
    print(f"\n  Matchups: {len(matchups)}")
    print(f"  Matches/pair: {args.matches_per_pair}")
    print(f"  Total matches: {total_matches}")
    print(f"  Parallel matchups: {min(args.workers, len(matchups))}")
    print(f"\n{'═' * 70}\n")

    # Create one directory per matchup
    print("Setting up matchup directories...")
    matchup_dirs = []
    for idx, (bot1, bot2) in enumerate(matchups):
        mdir = create_matchup_dir(idx, bot1, bot2)
        matchup_dirs.append(mdir)
    print(f"  Created {len(matchup_dirs)} isolated directories.\n")

    os.makedirs(LOGS_DIR, exist_ok=True)

    start = time.perf_counter()

    # Thread-safe stats
    stats = defaultdict(lambda: {
        "wins": 0, "losses": 0, "ties": 0,
        "total_bankroll": 0, "matches": 0,
        "head_to_head": defaultdict(lambda: {"w": 0, "l": 0, "t": 0}),
    })
    stats_lock = threading.Lock()
    progress = {"done": 0, "failed": 0, "total": total_matches, "start": start}

    # Run! Each matchup is a task in the thread pool.
    # Within each task, N matches run sequentially (same dir, no races).
    # Up to `workers` matchups run concurrently.
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = []
            for idx, (bot1, bot2) in enumerate(matchups):
                f = pool.submit(
                    run_matchup, matchup_dirs[idx],
                    bot1[0], bot2[0],
                    args.matches_per_pair,
                    stats, stats_lock, progress,
                )
                futures.append(f)
            concurrent.futures.wait(futures)
            # Raise any exceptions from threads
            for f in futures:
                f.result()
    finally:
        cleanup_matchup_dirs()

    elapsed = time.perf_counter() - start
    successful = sum(s["matches"] for s in stats.values()) // 2

    print(f"\n{'═' * 70}")
    print(f"  Tournament Complete!")
    print(f"  Successful: {successful}/{total_matches} ({successful/total_matches*100:.1f}%)")
    print(f"  Failed: {progress['failed']}")
    print(f"  Time: {fmt_time(elapsed)}")
    if successful:
        print(f"  Avg/match: {elapsed/total_matches:.2f}s")
    print(f"{'═' * 70}\n")

    if not successful:
        print("No successful matches! Check errors above.")
        sys.exit(1)

    # Rankings
    rankings = []
    for bot_name in [n for n, _ in valid_bots]:
        s = stats[bot_name]
        if s["matches"] > 0:
            rankings.append({
                "name": bot_name,
                "wins": s["wins"],
                "losses": s["losses"],
                "ties": s["ties"],
                "matches": s["matches"],
                "win_rate": s["wins"] / s["matches"] * 100,
                "total_bankroll": s["total_bankroll"],
                "avg_bankroll": s["total_bankroll"] / s["matches"],
                "head_to_head": s["head_to_head"],
            })

    rankings.sort(key=lambda x: (x["win_rate"], x["avg_bankroll"]), reverse=True)

    # Final Rankings Table
    print("\n" + "═" * 70)
    print("  FINAL RANKINGS")
    print("═" * 70)
    print(f"{'Rank':<6} {'Bot':<20} {'W-L-T':<15} {'Win%':<10} {'Avg BB/Match':<15}")
    print("─" * 70)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, r in enumerate(rankings, 1):
        wlt = f"{r['wins']}-{r['losses']}-{r['ties']}"
        avg_bb = r["avg_bankroll"] / 20
        medal = medals.get(i, "  ")
        print(f"{medal} {i:<4} {r['name']:<20} {wlt:<15} {r['win_rate']:>6.1f}%  "
              f"{avg_bb:>+8.1f} BB")

    print("═" * 70)

    # Head-to-Head Matrix
    bot_names = [r["name"] for r in rankings]
    max_cols = min(len(bot_names), 7)

    print("\n" + "═" * 70)
    print("  HEAD-TO-HEAD (W-L-T)")
    print("═" * 70)

    print(f"{'Bot':<20}", end="")
    for opp in bot_names[:max_cols]:
        print(f" {opp[:8]:<10}", end="")
    print()
    print("─" * 70)

    for bot in bot_names:
        print(f"{bot:<20}", end="")
        for opp in bot_names[:max_cols]:
            if bot == opp:
                print(f" {'--':<10}", end="")
            else:
                h = stats[bot]["head_to_head"][opp]
                print(f" {h['w']}-{h['l']}-{h['t']:<7}", end="")
        print()

    print("═" * 70)

    # Detailed Stats
    print("\n" + "═" * 70)
    print("  DETAILED STATISTICS")
    print("═" * 70)

    for i, r in enumerate(rankings, 1):
        print(f"\n{i}. {r['name']}")
        print(f"   Matches:   {r['matches']}")
        print(f"   Win Rate:  {r['win_rate']:.1f}%")
        print(f"   Bankroll:  {r['total_bankroll']:+,} chips ({r['total_bankroll']/20:+.1f} BB)")
        print(f"   Avg/Match: {r['avg_bankroll']:+.1f} chips ({r['avg_bankroll']/20:+.1f} BB)")

    print("\n" + "═" * 70)
    print(f"  Champion: {rankings[0]['name']} 🏆")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTournament interrupted. Cleaning up...")
        cleanup_matchup_dirs()
        sys.exit(130)
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        cleanup_matchup_dirs()
        sys.exit(1)
