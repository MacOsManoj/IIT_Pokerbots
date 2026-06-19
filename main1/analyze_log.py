#!/usr/bin/env python3
"""Analyze game log to check if the exploit is working."""
import re
import sys
import os
import glob

# Find the most recent glog
log_dir = os.path.join(os.path.dirname(__file__), "logs")
logs = sorted(glob.glob(os.path.join(log_dir, "*.glog")))
if not logs:
    print("No game logs found!")
    sys.exit(1)

logfile = logs[-1]
print(f"Analyzing: {os.path.basename(logfile)}")

with open(logfile) as f:
    lines = f.readlines()

# Parse rounds and their auction bids
round_num = 0
bids = []
current_bids = []
for line in lines:
    line = line.strip()
    if line.startswith("Round #"):
        round_num += 1
        current_bids = []
    m = re.match(r'^(\S+) A(\d+)$', line)
    if m:
        name, amount = m.group(1), int(m.group(2))
        current_bids.append((name, amount))
        if len(current_bids) == 2:
            bids.append((round_num, current_bids[0], current_bids[1]))

# Identify bot names
bot_names = set()
for rnd, (n1, b1), (n2, b2) in bids:
    bot_names.add(n1)
    bot_names.add(n2)
print(f"Bots: {bot_names}")

# Find the exploit bot name (speedbotv3 or exploit_bot)
exploit_name = None
for name in bot_names:
    if "v3" in name or "exploit" in name:
        exploit_name = name
        break
if exploit_name is None:
    exploit_name = list(bot_names)[0]  # fallback
other_name = [n for n in bot_names if n != exploit_name][0]

print(f"Exploit bot: {exploit_name}")
print(f"Other bot:   {other_name}")
print(f"Total auction rounds parsed: {len(bids)}")

# Show first 20 rounds
print(f"\n{'Rnd':>4} {'1st bidder':>14} {'1st bid':>8} {'2nd bidder':>14} {'2nd bid':>8} {'Winner':>14}")
for i, (rnd, (name1, bid1), (name2, bid2)) in enumerate(bids[:20]):
    if bid1 > bid2:
        winner = name1
    elif bid2 > bid1:
        winner = name2
    else:
        winner = "TIE"
    print(f"{rnd:>4} {name1:>14} {bid1:>8} {name2:>14} {bid2:>8} {winner:>14}")

# Stats
e_second_won = 0
e_second_lost = 0
e_first_won = 0
e_first_lost = 0
e_second_total = 0
e_first_total = 0
ties = 0

for rnd, (name1, bid1), (name2, bid2) in bids:
    if bid1 == bid2:
        ties += 1
        continue
    if name2 == exploit_name:
        e_second_total += 1
        if bid2 > bid1:
            e_second_won += 1
        else:
            e_second_lost += 1
    elif name1 == exploit_name:
        e_first_total += 1
        if bid1 > bid2:
            e_first_won += 1
        else:
            e_first_lost += 1

print(f"\n=== FULL STATS ({len(bids)} auctions, {ties} ties) ===")
print(f"\n{exploit_name} as SECOND bidder (exploit should work): {e_second_total}")
print(f"  Won: {e_second_won}  Lost: {e_second_lost}")
if e_second_total > 0:
    print(f"  Win%: {e_second_won/e_second_total*100:.1f}%")
print(f"\n{exploit_name} as FIRST bidder (no exploit): {e_first_total}")
print(f"  Won: {e_first_won}  Lost: {e_first_lost}")
if e_first_total > 0:
    print(f"  Win%: {e_first_won/e_first_total*100:.1f}%")

# Pattern analysis: when exploit bot is second bidder
print(f"\n=== {exploit_name} as SECOND bidder — Bid Pattern ===")
plus1 = 0
minus1 = 0
bid1_zero = 0
other_pattern = 0
for rnd, (name1, bid1), (name2, bid2) in bids:
    if name2 == exploit_name:
        if bid1 == 0 and bid2 == 1:
            bid1_zero += 1
        elif bid2 == bid1 + 1:
            plus1 += 1
        elif bid2 == bid1 - 1:
            minus1 += 1
        else:
            other_pattern += 1

print(f"  opp bid 0 -> bid 1 (free peek): {bid1_zero}")
print(f"  bid = opp+1 (win mode):  {plus1}")
print(f"  bid = opp-1 (drain mode): {minus1}")
print(f"  other pattern:             {other_pattern}")

# When OTHER: show some examples
if other_pattern > 0:
    print(f"\n  === 'Other' pattern examples ===")
    shown = 0
    for rnd, (name1, bid1), (name2, bid2) in bids:
        if name2 == exploit_name:
            diff = bid2 - bid1
            if diff != 1 and diff != -1 and not (bid1 == 0 and bid2 == 1):
                print(f"    Round {rnd}: opp bid {bid1}, {exploit_name} bid {bid2} (diff={diff:+d})")
                shown += 1
                if shown >= 15:
                    break
