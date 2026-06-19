import sys
import os
from collections import defaultdict

def generate_histogram(log_path, bin_size=5):
    if not os.path.exists(log_path):
        print(f"File not found: {log_path}")
        return

    bot_name = "67"
    losses = []
    
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            
            # Extract bot name from the first line
            if not bot_name and "vs" in line:
                parts = line.split("vs")
                left_part = parts[0].strip().split()
                if len(left_part) > 0:
                    bot_name = left_part[-1]
            
            # Find when our bot is awarded a negative amount
            if bot_name and line.startswith(f"{bot_name} awarded "):
                amount_str = line.split("awarded ")[1]
                try:
                    amount = int(amount_str)
                    if amount < 0:
                        losses.append(-amount)
                except ValueError:
                    pass

    if not losses:
        print(f"No losses found for bot '{bot_name}' in {log_path}.")
        return

    # Create buckets
    buckets_count = defaultdict(int)
    buckets_sum = defaultdict(int)
    
    for loss in losses:
        bucket_idx = loss // bin_size
        buckets_count[bucket_idx] += 1
        buckets_sum[bucket_idx] += loss
        
    print(f"--- Loss Analysis for '{bot_name}' ---")
    print(f"File: {os.path.basename(log_path)}")
    print(f"Total hands lost: {len(losses)}")
    print(f"Total chips lost: {sum(losses)}")
    print(f"Bin size: {bin_size}")
    print()
    print(f"{'Loss Range':<15} | {'Count':<7} | {'Total Chips':<12} | {'Histogram (Count)'}")
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
                bar_len = 1 # Show at least a blip for low counts
            bar = "█" * bar_len
            
            print(f"{range_str:<15} | {count:<7} | {chips:<12} | {bar}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python histogram_generator.py <log_file> [bin_size]")
        sys.exit(1)
    
    bin_size = 5
    if len(sys.argv) >= 3:
        bin_size = int(sys.argv[2])
        
    generate_histogram(sys.argv[1], bin_size)
