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
from typing import Dict, Optional, Tuple

from config import BOT_1_NAME, BOT_2_NAME, PYTHON_CMD


def parse_auction_stats_from_glog(glog_path: str, bot_name: str) -> Tuple[int, int]:
    """
    Parse a glog file and return (auction_loss_in_losing_rounds, total_loss).
    
    auction_loss = sum of what we paid in auction in rounds we lost
    total_loss = sum of all negative awards
    """
    total_loss = 0
    auction_loss_in_losing_rounds = 0
    
    try:
        with open(glog_path, 'r') as f:
            content = f.read()
    except:
        return (0, 0)
    
    # Split into rounds
    rounds = re.split(r'\nRound #\d+', content)
    
    for round_text in rounds:
        if not round_text.strip():
            continue
            
        # Find our award in this round
        award_match = re.search(rf'{re.escape(bot_name)} awarded (-?\d+)', round_text)
        if not award_match:
            continue
        award = int(award_match.group(1))
        
        # Only care about losing rounds
        if award >= 0:
            continue
            
        total_loss += abs(award)
        
        # Check if we won the auction in this round
        if f'{bot_name} won the auction' in round_text:
            # We won auction - we paid opponent's bid (second price)
            # Find opponent's bid
            bid_matches = re.findall(r'(\w+) bids (\d+)', round_text)
            if len(bid_matches) >= 2:
                # Find our bid and opponent's bid
                our_bid = None
                opp_bid = None
                for bidder, bid_amt in bid_matches:
                    if bidder == bot_name:
                        our_bid = int(bid_amt)
                    else:
                        opp_bid = int(bid_amt)
                
                # We paid opponent's bid (second price auction)
                if opp_bid is not None:
                    auction_loss_in_losing_rounds += opp_bid
    
    return (auction_loss_in_losing_rounds, total_loss)


def parse_all_glogs_for_auction(logs_dir: str, pre_glogs: set, bot_name: str) -> Tuple[int, int]:
    """Parse all new glog files and aggregate auction stats."""
    new_glogs = set(glob.glob(os.path.join(logs_dir, "*.glog"))) - pre_glogs
    
    total_auction_loss = 0
    total_loss = 0
    
    for glog_path in new_glogs:
        auction_loss, loss = parse_auction_stats_from_glog(glog_path, bot_name)
        total_auction_loss += auction_loss
        total_loss += loss
    
    return (total_auction_loss, total_loss)


def parse_match_stats(output: str) -> Optional[Dict[str, dict]]:
    """Extract bot name → {bankroll, win_rate, wins} from engine output."""
    stats: Dict[str, dict] = {}
    current_bot: Optional[str] = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Stats for ") and line.endswith(":"):
            current_bot = line[len("Stats for "):-1].strip()
            stats[current_bot] = {}
        elif current_bot and line.startswith("Total Bankroll:"):
            stats[current_bot]['bankroll'] = int(line.split(":", 1)[1].strip())
        elif current_bot and line.startswith("Win Rate:"):
            pct = float(line.split(":", 1)[1].strip().rstrip('%'))
            stats[current_bot]['win_rate'] = pct
            stats[current_bot]['wins'] = int(round(pct / 100.0 * 1000))  # 1000 rounds per match
            current_bot = None
    if BOT_1_NAME in stats and BOT_2_NAME in stats:
        return stats
    return None


def run_one_match(match_id: int, project_root: str) -> Optional[Dict[str, dict]]:
    result = subprocess.run(
        [PYTHON_CMD, "engine.py"],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return parse_match_stats(result.stdout)


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
    parser.add_argument("--save-logs", action="store_true", help="Save match log files instead of deleting them")
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
                b1w = sum(1 for x in results if x[BOT_1_NAME]['bankroll'] > x[BOT_2_NAME]['bankroll'])
                b2w = sum(1 for x in results if x[BOT_2_NAME]['bankroll'] > x[BOT_1_NAME]['bankroll'])
                print(f"  {done}/{args.count} | {fmt_time(time.perf_counter() - start)} | "
                      f"{BOT_1_NAME}: {b1w}  {BOT_2_NAME}: {b2w}")

    pre_glogs = set(glob.glob(os.path.join(logs_dir, "*.glog")))
    pre_plogs = set(glob.glob(os.path.join(logs_dir, "*.plog")))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(run_one_match, i, project_root) for i in range(args.count)]
        for f in futures:
            f.add_done_callback(_on_done)
        concurrent.futures.wait(futures)

    # Parse auction stats BEFORE cleanup (need logs)
    b1_auction_loss, b1_total_loss = parse_all_glogs_for_auction(logs_dir, pre_glogs, BOT_1_NAME)
    b2_auction_loss, b2_total_loss = parse_all_glogs_for_auction(logs_dir, pre_glogs, BOT_2_NAME)

    if not args.save_logs:
        cleanup_logs(logs_dir, pre_glogs, pre_plogs)
    else:
        print("Logs saved (--save-logs enabled).")

    elapsed = time.perf_counter() - start
    total = len(results)
    if not total:
        print("No successful matches!")
        sys.exit(1)

    b1_match_wins = sum(1 for r in results if r[BOT_1_NAME]['bankroll'] > r[BOT_2_NAME]['bankroll'])
    b2_match_wins = sum(1 for r in results if r[BOT_2_NAME]['bankroll'] > r[BOT_1_NAME]['bankroll'])
    ties = total - b1_match_wins - b2_match_wins

    # Round-level stats
    total_rounds = total * 1000
    b1_round_wins = sum(r[BOT_1_NAME].get('wins', 0) for r in results)
    b2_round_wins = sum(r[BOT_2_NAME].get('wins', 0) for r in results)

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  {BOT_1_NAME} vs {BOT_2_NAME}  ({total} matches, {fmt_time(elapsed)})")
    print(f"{sep}")
    print(f"  MATCH WINS:")
    print(f"    {BOT_1_NAME}:  {b1_match_wins}/{total}  ({b1_match_wins/total*100:.1f}%)")
    print(f"    {BOT_2_NAME}:  {b2_match_wins}/{total}  ({b2_match_wins/total*100:.1f}%)")
    print(f"    Ties:       {ties}/{total}")
    print(f"  ROUND WINS ({total_rounds:,} total rounds):")
    print(f"    {BOT_1_NAME}:  {b1_round_wins:,}/{total_rounds:,}  ({b1_round_wins/total_rounds*100:.1f}%)")
    print(f"    {BOT_2_NAME}:  {b2_round_wins:,}/{total_rounds:,}  ({b2_round_wins/total_rounds*100:.1f}%)")
    print(f"  AUCTION LOSS (in losing rounds):")
    if b1_total_loss > 0:
        b1_pct = b1_auction_loss / b1_total_loss * 100
        print(f"    {BOT_1_NAME}:  {b1_auction_loss:,}/{b1_total_loss:,}  ({b1_pct:.1f}%)")
    else:
        print(f"    {BOT_1_NAME}:  0/0  (N/A)")
    if b2_total_loss > 0:
        b2_pct = b2_auction_loss / b2_total_loss * 100
        print(f"    {BOT_2_NAME}:  {b2_auction_loss:,}/{b2_total_loss:,}  ({b2_pct:.1f}%)")
    else:
        print(f"    {BOT_2_NAME}:  0/0  (N/A)")
    print(f"{sep}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
