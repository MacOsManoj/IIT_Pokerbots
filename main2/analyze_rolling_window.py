"""
Analyze if the rolling window preflop threshold is working correctly.

The mechanism:
1. Tracks opponent preflop raises when cost_to_call > 10
2. First 5 raises: accepts if <= 200
3. After 5 raises: uses outlier detection (mean + 2*std) to filter
4. Updates threshold = min(avg_raise + 5, 200)
"""

import re
import sys

# Constants from tdv5.py
BASE_PREFLOP_THRESHOLD = 96
MAX_PREFLOP_THRESHOLD = 200

def simulate_rolling_window(raise_amounts: list[int]) -> list[dict]:
    """
    Simulate the rolling window threshold update mechanism.
    Returns a trace of each hand's state.
    """
    trace = []
    opp_raise_amounts = []
    dynamic_preflop_threshold = BASE_PREFLOP_THRESHOLD
    
    for hand_num, raise_amt in enumerate(raise_amounts, 1):
        # Record state BEFORE this hand
        hand_record = {
            "hand": hand_num,
            "opp_raise": raise_amt,
            "threshold_before": dynamic_preflop_threshold,
            "would_call": raise_amt <= dynamic_preflop_threshold,
            "tracked": False,
            "opp_raise_amounts_len": len(opp_raise_amounts),
        }
        
        # Simulate tracking logic (from get_move lines 615-629)
        if len(opp_raise_amounts) < 5:
            if raise_amt <= 200:
                opp_raise_amounts.append(raise_amt)
                hand_record["tracked"] = True
        else:
            rolling_window = opp_raise_amounts[-50:]
            mean = sum(rolling_window) / len(rolling_window)
            variance = sum((x - mean) ** 2 for x in rolling_window) / len(rolling_window)
            std = max(variance ** 0.5, 10)
            upper_bound = mean + 2 * std
            
            if raise_amt <= upper_bound:
                opp_raise_amounts.append(raise_amt)
                hand_record["tracked"] = True
            hand_record["outlier_bound"] = upper_bound
        
        if len(opp_raise_amounts) > 50:
            opp_raise_amounts = opp_raise_amounts[-50:]
        
        # Simulate threshold update (happens at START of NEXT hand)
        # For analysis, we show what threshold will be for next hand
        if len(opp_raise_amounts) >= 3:
            avg_raise = sum(opp_raise_amounts) / len(opp_raise_amounts)
            new_threshold = min(int(avg_raise + 5), MAX_PREFLOP_THRESHOLD)
            hand_record["avg_raise"] = avg_raise
            hand_record["threshold_next"] = new_threshold
            dynamic_preflop_threshold = new_threshold
        else:
            hand_record["threshold_next"] = dynamic_preflop_threshold
        
        trace.append(hand_record)
    
    return trace


def analyze_trace(trace: list[dict]) -> None:
    """Analyze and print the trace."""
    print("\n" + "="*80)
    print("ROLLING WINDOW THRESHOLD ANALYSIS")
    print("="*80)
    
    folds_before_adapt = 0
    calls_after_adapt = 0
    
    print(f"\n{'Hand':>5} | {'OppRaise':>8} | {'Threshold':>9} | {'Would Call':>10} | {'Tracked':>7} | {'Avg':>6} | {'Next Thresh':>11}")
    print("-" * 80)
    
    for h in trace[:20]:  # Show first 20 hands
        avg_str = f"{h.get('avg_raise', ''):>6.1f}" if 'avg_raise' in h else "   N/A"
        print(f"{h['hand']:>5} | {h['opp_raise']:>8} | {h['threshold_before']:>9} | "
              f"{'YES' if h['would_call'] else 'NO':>10} | "
              f"{'YES' if h['tracked'] else 'NO':>7} | {avg_str} | {h['threshold_next']:>11}")
        
        if not h['would_call']:
            folds_before_adapt += 1
    
    if len(trace) > 20:
        print(f"... ({len(trace) - 20} more hands)")
    
    # Summary stats
    total_folds = sum(1 for h in trace if not h['would_call'])
    total_calls = sum(1 for h in trace if h['would_call'])
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total hands analyzed: {len(trace)}")
    print(f"Total would-fold decisions: {total_folds}")
    print(f"Total would-call decisions: {total_calls}")
    
    # Find adaptation point
    adapted = False
    for i, h in enumerate(trace):
        if h['would_call'] and h['opp_raise'] > BASE_PREFLOP_THRESHOLD:
            print(f"\nFirst successful adaptation at hand {h['hand']}:")
            print(f"  - Opponent raise: {h['opp_raise']}")
            print(f"  - Dynamic threshold had adapted to: {h['threshold_before']}")
            print(f"  - Hands folded before adaptation: {i}")
            adapted = True
            break
    
    if not adapted:
        print(f"\nNever adapted! All raises > {BASE_PREFLOP_THRESHOLD} were folded")


def parse_log_raises(log_path: str, bot_name: str = None) -> list[int]:
    """
    Parse preflop raises from a glog file.
    If bot_name specified, only track raises by that bot.
    """
    raises = []
    with open(log_path, 'r') as f:
        for line in f:
            match = re.search(r'(\w+) raises to (\d+)', line)
            if match:
                raiser = match.group(1)
                amount = int(match.group(2))
                # Filter for preflop-sized raises (under 500, assuming stack is 5000)
                if amount <= 300:
                    if bot_name is None or raiser == bot_name:
                        raises.append(amount)
    return raises


def test_realistic_raise_frequency():
    """
    Realistic scenario: opponent raises 30% of hands.
    This tests how long it takes to adapt when raises are sparse.
    """
    import random
    random.seed(42)
    
    global BASE_PREFLOP_THRESHOLD
    
    opp_raise_amounts = []
    dynamic_preflop_threshold = BASE_PREFLOP_THRESHOLD
    total_hands = 0
    raise_hands = 0
    folds_before_adapt = 0
    adapted = False
    
    print("\n" + "="*80)
    print("TEST CASE 6: Realistic 30% raise frequency (opponent raises to 102)")
    print("="*80)
    
    while total_hands < 100:
        total_hands += 1
        
        if random.random() < 0.30:
            raise_hands += 1
            raise_amt = 102
            
            would_fold = raise_amt > dynamic_preflop_threshold
            if would_fold and not adapted:
                folds_before_adapt += 1
            
            if not adapted and not would_fold:
                adapted = True
                print(f"Hand {total_hands}: First successful call! (raise #{raise_hands})")
                print(f"  Threshold had adapted to: {dynamic_preflop_threshold}")
                print(f"  Folds before adaptation: {folds_before_adapt}")
            
            if len(opp_raise_amounts) < 5:
                if raise_amt <= 200:
                    opp_raise_amounts.append(raise_amt)
            else:
                rolling_window = opp_raise_amounts[-50:]
                mean = sum(rolling_window) / len(rolling_window)
                variance = sum((x - mean) ** 2 for x in rolling_window) / len(rolling_window)
                std = max(variance ** 0.5, 10)
                upper_bound = mean + 2 * std
                if raise_amt <= upper_bound:
                    opp_raise_amounts.append(raise_amt)
            
            if len(opp_raise_amounts) >= 3:
                avg_raise = sum(opp_raise_amounts) / len(opp_raise_amounts)
                dynamic_preflop_threshold = min(int(avg_raise + 5), MAX_PREFLOP_THRESHOLD)
    
    print(f"\nFinal stats after 100 hands:")
    print(f"  Total raise hands: {raise_hands}")
    print(f"  Folds before adaptation: {folds_before_adapt}")
    print(f"  Final threshold: {dynamic_preflop_threshold}")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("TEST CASE 1: Opponent consistently raises to 102")
    print("="*80)
    
    # Simulate opponent raising to 102 every hand
    raises_102 = [102] * 20
    trace = simulate_rolling_window(raises_102)
    analyze_trace(trace)
    
    print("\n" + "="*80)
    print("TEST CASE 2: Opponent raises to 97 (just above 96 threshold)")
    print("="*80)
    
    raises_97 = [97] * 20
    trace = simulate_rolling_window(raises_97)
    analyze_trace(trace)
    
    print("\n" + "="*80)
    print("TEST CASE 3: Mixed raises (80, 90, 100, 102, 110)")
    print("="*80)
    
    raises_mixed = [80, 90, 100, 102, 110, 102, 102, 102, 102, 102, 102, 102, 102, 102]
    trace = simulate_rolling_window(raises_mixed)
    analyze_trace(trace)
    
    print("\n" + "="*80)
    print("TEST CASE 4: Opponent occasionally shoves (outlier detection)")
    print("="*80)
    
    # 5 normal raises, then a mix of normal and all-in
    raises_with_outliers = [100, 100, 100, 100, 100, 5000, 100, 100, 5000, 100]
    trace = simulate_rolling_window(raises_with_outliers)
    print("\nExpected: 5000 all-in should be rejected by outlier detection after 5 samples")
    analyze_trace(trace)
    
    # Test realistic scenario
    test_realistic_raise_frequency()
