"""
Analyze VPIP/PFR patterns from game logs to evaluate tdv6 threshold settings.
"""
import re
import glob
import os

logs_dir = "logs"
glog_files = sorted(glob.glob(os.path.join(logs_dir, "*.glog")))[-100:]  # Recent 100

# Aggregate stats
bot1_stats = {'vpip': 0, 'pfr': 0, 'hands': 0}
bot2_stats = {'vpip': 0, 'pfr': 0, 'hands': 0}

match_wins = {'tdv5_stable': 0, 'tdv6': 0}
total_bankroll = {'tdv5_stable': 0, 'tdv6': 0}

for glog in glog_files:
    with open(glog, 'r') as f:
        content = f.read()
    
    # Parse final bankrolls from stats at end of file
    b1_match = re.search(r'Stats for tdv5_stable:.*?Total Bankroll:\s*(-?\d+)', content, re.DOTALL)
    b2_match = re.search(r'Stats for tdv6:.*?Total Bankroll:\s*(-?\d+)', content, re.DOTALL)
    
    if b1_match and b2_match:
        b1_bank = int(b1_match.group(1))
        b2_bank = int(b2_match.group(1))
        total_bankroll['tdv5_stable'] += b1_bank
        total_bankroll['tdv6'] += b2_bank
        if b1_bank > b2_bank:
            match_wins['tdv5_stable'] += 1
        elif b2_bank > b1_bank:
            match_wins['tdv6'] += 1
    
    # Split by rounds - format: "Round #N, ..."
    rounds = re.split(r'\nRound #\d+,', content)
    
    for round_text in rounds:
        if not round_text.strip():
            continue
        
        # Find preflop section (before Flop line)
        flop_idx = round_text.find('Flop [')
        if flop_idx == -1:
            preflop = round_text
        else:
            preflop = round_text[:flop_idx]
        
        # tdv5_stable preflop actions (after posting blind)
        # VPIP = called or raised (anything beyond posting blind)
        # In this game format: "calls" after posting 10 = completing SB to BB (VPIP)
        #                      "raises to X" = PFR
        if 'tdv5_stable' in preflop:
            bot1_stats['hands'] += 1
            # Check if they voluntarily put money in (calls or raises/bets beyond blind)
            if re.search(r'tdv5_stable (calls|raises|bets)', preflop):
                bot1_stats['vpip'] += 1
            # Check if they raised preflop (raised beyond just completing)
            if re.search(r'tdv5_stable raises', preflop):
                bot1_stats['pfr'] += 1
        
        # tdv6 preflop actions
        if 'tdv6' in preflop:
            bot2_stats['hands'] += 1
            if re.search(r'tdv6 (calls|raises|bets)', preflop):
                bot2_stats['vpip'] += 1
            if re.search(r'tdv6 raises', preflop):
                bot2_stats['pfr'] += 1

# Calculate percentages
if bot1_stats['hands'] > 0:
    bot1_vpip_pct = bot1_stats['vpip'] / bot1_stats['hands'] * 100
    bot1_pfr_pct = bot1_stats['pfr'] / bot1_stats['hands'] * 100
else:
    bot1_vpip_pct = bot1_pfr_pct = 0

if bot2_stats['hands'] > 0:
    bot2_vpip_pct = bot2_stats['vpip'] / bot2_stats['hands'] * 100
    bot2_pfr_pct = bot2_stats['pfr'] / bot2_stats['hands'] * 100
else:
    bot2_vpip_pct = bot2_pfr_pct = 0

print("=" * 70)
print("VPIP/PFR ANALYSIS FROM LOGS (100 matches)")
print("=" * 70)

print(f"\nMATCH RESULTS:")
print(f"  tdv5_stable wins: {match_wins['tdv5_stable']}")
print(f"  tdv6 wins:        {match_wins['tdv6']}")
print(f"  tdv5_stable total bankroll: {total_bankroll['tdv5_stable']:,}")
print(f"  tdv6 total bankroll:        {total_bankroll['tdv6']:,}")

print(f"\ntdv5_stable (opponent):")
print(f"  Hands analyzed: {bot1_stats['hands']:,}")
print(f"  VPIP: {bot1_vpip_pct:.1f}%")
print(f"  PFR:  {bot1_pfr_pct:.1f}%")

print(f"\ntdv6 (our bot):")
print(f"  Hands analyzed: {bot2_stats['hands']:,}")
print(f"  VPIP: {bot2_vpip_pct:.1f}%")
print(f"  PFR:  {bot2_pfr_pct:.1f}%")

print("\n" + "=" * 70)
print("PROFILE CLASSIFICATION FOR tdv5_stable:")
print("=" * 70)

if bot1_pfr_pct >= 45:
    profile = "MANIAC (PFR >= 45%)"
    threshold = 30
    scale = 0.8
elif bot1_pfr_pct < 12 or bot1_vpip_pct < 40:
    profile = "NIT (PFR < 12% or VPIP < 40%)"
    threshold = 150
    scale = 1.3
elif bot1_vpip_pct >= 55:
    profile = "CALLING STATION (VPIP >= 55%)"
    threshold = 85
    scale = 1.0
else:
    profile = "BALANCED"
    threshold = 80
    scale = 1.0

print(f"\n  Profile: {profile}")
print(f"  Current preflop threshold: {threshold}")
print(f"  Current postflop scale: {scale}")

print("\n" + "=" * 70)
print("THRESHOLD TUNING RECOMMENDATIONS:")
print("=" * 70)

# Analyze if current thresholds are appropriate
print(f"\n  tdv5_stable has VPIP={bot1_vpip_pct:.1f}% PFR={bot1_pfr_pct:.1f}%")

if profile == "BALANCED":
    # Check if we're on the edge of another profile
    if bot1_vpip_pct > 50:
        print(f"  -> VPIP is high ({bot1_vpip_pct:.1f}%), close to Calling Station territory.")
        print(f"     Consider raising threshold to 85-90 chips for more value extraction.")
    elif bot1_pfr_pct > 35:
        print(f"  -> PFR is elevated ({bot1_pfr_pct:.1f}%), opponent is aggressive.")
        print(f"     Consider lowering threshold to 50-60 chips to avoid getting trapped.")
    else:
        print(f"  -> Profile looks correct. Threshold 80 is appropriate.")

elif profile == "NIT (PFR < 12% or VPIP < 40%)":
    print(f"  -> Opponent plays tight. Threshold 150 allows stealing.")
    if bot1_pfr_pct < 5:
        print(f"     PFR is very low ({bot1_pfr_pct:.1f}%), consider increasing to 180-200.")
    
elif profile == "MANIAC (PFR >= 45%)":
    print(f"  -> Opponent is hyper-aggressive. Threshold 30 is very tight.")
    print(f"     This means folding most trash hands preflop which is correct.")

elif profile == "CALLING STATION (VPIP >= 55%)":
    print(f"  -> Opponent calls a lot but doesn't raise much.")
    print(f"     Threshold 85 is moderate. Focus on value betting postflop.")

print("\n" + "=" * 70)
