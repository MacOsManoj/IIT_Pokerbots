import sys
import os
import gzip
import glob
import matplotlib.pyplot as plt
from collections import defaultdict
import numpy as np

# ═══════════════════════════════════════════════════════════════════════════
# HARDCODE YOUR SETTINGS HERE
# ═══════════════════════════════════════════════════════════════════════════
LOG_DIR = "logs"                              # Directory containing logs
LOG_FILE = "20260307-024501-837144.glog"      # Log file name
BOT_NAME = "tdv4"                             # Bot name to analyze
# ═══════════════════════════════════════════════════════════════════════════

def parse_logs(log_path, bot_name="67"):
    if not os.path.exists(log_path):
        print(f"File not found: {log_path}")
        return None

    # Open file (handle gzip if needed)
    if log_path.endswith('.gz'):
        f = gzip.open(log_path, 'rt', encoding='utf-8')
    else:
        f = open(log_path, 'r', encoding='utf-8')

    rounds = []
    current_round = []
    
    # First pass to find bot name if not provided or to confirm
    found_bot_name = bot_name
    
    for line in f:
        line = line.strip()
        if not found_bot_name and "vs" in line:
            parts = line.split("vs")
            left_part = parts[0].strip().split()
            if len(left_part) > 0:
                found_bot_name = left_part[-1]
        
        if line.startswith("Round #"):
            if current_round:
                rounds.append(current_round)
            current_round = [line]
        elif line:
            current_round.append(line)
            
    if current_round:
        rounds.append(current_round)
    
    f.close()
    return rounds, found_bot_name

def analyze_combined(rounds, bot_name, min_loss=21, max_loss=70):
    # Data for histogram
    losses = []
    wins = []
    
    # Data for raise tracking (per round)
    # round_num -> {street -> total_raises}
    bot_raises_per_round = defaultdict(lambda: defaultdict(int))
    opp_raises_per_round = defaultdict(lambda: defaultdict(int))
    
    # Mid-loss analyzer stats
    mid_loss_count = 0
    total_mid_loss = 0
    street_reached_mid = defaultdict(int)
    
    # Fold tracking
    bot_folds = defaultdict(int)
    opp_folds = defaultdict(int)
    
    # General stats
    total_rounds = len(rounds)
    
    for r_idx, r_lines in enumerate(rounds):
        round_num = r_idx + 1
        street = "Preflop"
        bot_award = 0
        opp_name = "Opponent"
        
        # Round specific temp raise tracking
        r_bot_raises = defaultdict(int)
        r_opp_raises = defaultdict(int)
        
        for line in r_lines:
            if line.startswith("Flop"): street = "Flop"
            elif line.startswith("Turn"): street = "Turn"
            elif line.startswith("River"): street = "River"
            
            # Detect opponent name
            if "posts blind" in line and bot_name not in line:
                opp_name = line.split(" posts blind")[0]

            # Track raises/bets
            if "bets " in line or "raises to " in line:
                actor = line.split(" ")[0]
                try:
                    # Extract amount
                    if "bets " in line:
                        amt = int(line.split("bets ")[1].split(" ")[0])
                    else:
                        amt = int(line.split("raises to ")[1].split(" ")[0])
                    
                    if actor == bot_name:
                        r_bot_raises[street] += amt
                    else:
                        r_opp_raises[street] += amt
                except (ValueError, IndexError):
                    pass
            
            # Fold tracking
            if " folds" in line:
                actor = line.split(" folds")[0]
                if actor == bot_name:
                    bot_folds[street] += 1
                else:
                    opp_folds[street] += 1

            # Award tracking
            if f"{bot_name} awarded " in line:
                try:
                    bot_award = int(line.split("awarded ")[1])
                except ValueError:
                    pass

        # Store raises for this round
        for st in ["Preflop", "Flop", "Turn", "River"]:
            bot_raises_per_round[round_num][st] = r_bot_raises[st]
            opp_raises_per_round[round_num][st] = r_opp_raises[st]

        if bot_award < 0:
            loss = abs(bot_award)
            losses.append(loss)
            if min_loss <= loss <= max_loss:
                mid_loss_count += 1
                total_mid_loss += loss
                street_reached_mid[street] += 1
        elif bot_award > 0:
            wins.append(bot_award)

    return {
        'losses': losses,
        'wins': wins,
        'bot_raises': bot_raises_per_round,
        'opp_raises': opp_raises_per_round,
        'mid_loss_count': mid_loss_count,
        'total_mid_loss': total_mid_loss,
        'street_reached_mid': street_reached_mid,
        'total_rounds': total_rounds,
        'opp_name': opp_name,
        'bot_folds': bot_folds,
        'opp_folds': opp_folds
    }

def print_ascii_histogram(data, label, bot_name, bin_size=5):
    if not data:
        print(f"No {label} data found for '{bot_name}'.")
        return

    buckets_count = defaultdict(int)
    buckets_sum = defaultdict(int)
    
    for val in data:
        bucket_idx = val // bin_size
        buckets_count[bucket_idx] += 1
        buckets_sum[bucket_idx] += val
        
    print(f"\n--- {label} Analysis for '{bot_name}' ---")
    print(f"Total hands: {len(data)}")
    print(f"Total chips: {sum(data)}")
    print(f"Bin size: {bin_size}")
    print()
    print(f"{'Range':<15} | {'Count':<7} | {'Total Chips':<12} | {'Histogram (Count)'}")
    print("-" * 75)
    
    max_count = max(buckets_count.values())
    max_bucket = max(buckets_count.keys())
    
    for i in range(max_bucket + 1):
        if buckets_count[i] > 0:
            start = i * bin_size
            end = start + bin_size
            range_str = f"({start}-{end})"
            count = buckets_count[i]
            chips = buckets_sum[i]
            
            # Simple ASCII bar, max width 30
            bar_len = int((count / max_count) * 30)
            if count > 0 and bar_len == 0:
                bar_len = 1 
            bar = "█" * bar_len
            
            print(f"{range_str:<15} | {count:<7} | {chips:<12} | {bar}")

def plot_data(stats, bot_name, base_name="poker"):
    opp_name = stats['opp_name']
    
    # --- DELETE OLD GRAPHS ---
    for old_file in glob.glob("*.svg") + glob.glob("*.png"):
        try:
            os.remove(old_file)
            print(f"Deleted: {old_file}")
        except:
            pass
    
    # --- TERMINAL ASCII HISTOGRAMS ---
    # Using bin_size=5 for terminal as it's more readable than 1
    print_ascii_histogram(stats['losses'], "Loss", bot_name, bin_size=5)
    print_ascii_histogram(stats['wins'], "Win", bot_name, bin_size=5)

    # --- Raising Trends Plot (Single SVG) ---
    fig_trends, axes = plt.subplots(4, 1, figsize=(15, 18), sharex=False)
    rounds = sorted(stats['opp_raises'].keys())
    streets = ["Preflop", "Flop", "Turn", "River"]
    
    # Calculate a clean tick interval (e.g. every 100 rounds)
    max_round = 1000  # Fixed to 1000 for standard comparison across matches
    tick_interval = 100
    
    for i, st in enumerate(streets):
        ax = axes[i]
        
        # Extract data for this street
        opp_y = [stats['opp_raises'][r][st] for r in rounds]
        bot_y = [stats['bot_raises'][r][st] for r in rounds]
        
        # Plot both bot and opponent on the same subplot for head-to-head comparison
        ax.plot(rounds, opp_y, label=f'Opponent: {opp_name}', color='red', alpha=0.6, marker='o', markersize=3, linewidth=1)
        ax.plot(rounds, bot_y, label=f'Bot: {bot_name}', color='blue', alpha=0.6, marker='x', markersize=3, linewidth=1)
        
        ax.set_title(f'{st} Aggression (Exact Raises per hand, connected): {bot_name} vs {opp_name}')
        ax.set_ylabel('Raises')
        ax.set_xlabel('Round Number')
        
        # Explicit x-axis markings out to 1000
        ax.set_xlim(0, max_round)
        ax.set_xticks(np.arange(0, max_round + tick_interval, tick_interval))
        
        ax.legend(loc='upper right')
        ax.grid(True, alpha=0.5)

    plt.tight_layout()
    
    # Save as infinitely zoomable SVG
    out_file = f"{base_name}_raising_trends.svg"
    plt.savefig(out_file, format="svg")
    print(f"\nRaising trends saved to {out_file}")
    plt.close()
    
    # --- FOLDS PER STREET ---
    print("\n" + "="*40)
    print(f"{'STREET':<10} | {bot_name+' Folds':<12} | {opp_name+' Folds':<12}")
    print("-" * 40)
    for st in streets:
        b_folds = stats['bot_folds'][st]
        o_folds = stats['opp_folds'][st]
        print(f"{st:<10} | {b_folds:<12} | {o_folds:<12}")
    print("="*40)
    
    # Print Mid-Loss Analysis to console
    print("\n" + "="*40)
    print("      MID-LOSS ANALYZER SUMMARY")
    print("="*40)
    print(f"Filter range: [21, 70] chips")
    print(f"Total mid-range loss hands: {stats['mid_loss_count']}")
    print(f"Total chips lost in this range: {stats['total_mid_loss']}")
    if stats['mid_loss_count'] > 0:
        print(f"Average loss: {stats['total_mid_loss']/stats['mid_loss_count']:.1f}")
    print("\nStreet reached in mid-loss hands:")
    for st in streets:
        count = stats['street_reached_mid'][st]
        pct = (count / stats['mid_loss_count'] * 100) if stats['mid_loss_count'] > 0 else 0
        print(f"  {st:<8}: {count:<4} ({pct:>5.1f}%)")
    print("="*40)

if __name__ == "__main__":
    # Use hardcoded values from top of file
    log_file = os.path.join(LOG_DIR, LOG_FILE)
    bot_name = BOT_NAME
    
    print(f"Processing {log_file}...")
    result = parse_logs(log_file, bot_name)
    if result:
        rounds, actual_bot_name = result
        stats = analyze_combined(rounds, actual_bot_name)
        
        # Get base name for output files
        base_name = os.path.basename(log_file)
        if base_name.endswith('.glog'):
            base_name = base_name[:-5]
        elif base_name.endswith('.log'):
            base_name = base_name[:-4]
            
        plot_data(stats, actual_bot_name, base_name)
    else:
        print("Failed to parse logs.")