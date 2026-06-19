# sobv2_with_bluff.py - Bluffing Feature Documentation

## What's New

Added **selective bluffing** for ultra-trash hands to sobv2.

## Bluffing Configuration

```python
BLUFF_EQUITY_THRESHOLD = 0.35  # Only bluff with equity < 35% (worst 20% of hands)
BLUFF_FREQUENCY = 0.25         # Bluff 25% of the time with ultra-trash
BLUFF_BASE_SIZE = 1000         # Starting bet: 1000 chips
BLUFF_INCREMENT = 1000         # Increase by 1000 each street
```

## How It Works

### 1. Hand Selection (Preflop)
- When dealt ultra-trash hands (equity < 35%), like 72o, 32o, 63o, etc.
- Bot decides to bluff **25% of the time** (random selection)
- Sets `_is_bluffing = True` for the hand

### 2. Bluffing Strategy (Postflop)

**Escalating Bet Sizes:**
- **Flop:** Bet 1000 chips
- **Turn:** Bet 2000 chips (if opponent doesn't fold)
- **River:** Bet 3000 chips (if opponent still doesn't fold)
- **Beyond:** Bet 4000+ chips (unlikely to reach)

**Abandoning the Bluff:**
- If opponent bets or raises → **FOLD immediately**
- Only continue bluffing if opponent checks
- This is "positional aggression" - we bet when we can, fold when challenged

### 3. Example Hand

```
Hand: 32o (32% equity) - ULTRA TRASH
Decision: Bluff! (random 25% triggered)

PREFLOP: 
- Play normally (call/fold based on thresholds)

FLOP: [Kh 9s 4c]
- Opponent checks
- We BET 1000 chips (bluff!)
- Opponent calls

TURN: [Kh 9s 4c 2d]
- Opponent checks
- We BET 2000 chips (continuing bluff!)
- Opponent calls

RIVER: [Kh 9s 4c 2d 7h]
- Opponent checks
- We BET 3000 chips (final bluff!)
- Opponent folds OR calls and we lose

Alternative scenario:
TURN: [Kh 9s 4c 2d]
- Opponent BETS 500
- We FOLD (abandon bluff)
```

## Differences from Normal sobv2

| Aspect | Normal sobv2 | sobv2_with_bluff |
|--------|-------------|------------------|
| **Ultra-trash hands** | Play passively, check/fold | Bluff 25% of the time |
| **Bet sizing** | 20-30% pot max | 1000 → 2000 → 3000 fixed sizes |
| **Facing bets** | Calculate equity, call if good | Immediately fold (bluff failed) |
| **Strategy** | Conservative, equity-based | Aggressive bluffing mixed in |

## Why This Might Help

1. **Adds unpredictability** - opponent can't always assume small bets = trash
2. **Wins some pots we'd otherwise lose** - if opponent folds to the bluff
3. **Not too risky** - only 25% of ultra-trash hands, fold if challenged
4. **Fixed sizes make it manageable** - not risking entire stack

## Potential Risks

1. **Can lose 1000-6000 chips** when bluff gets called
2. **Might be too predictable** if opponent notices fixed bet sizes (1000, 2000, 3000)
3. **Only works against opponents who fold** to aggression

## Testing Instructions

```bash
# Run sobv2_with_bluff.py against sobkiller
python3 comparator.py --bot1 sobv2_with_bluff --bot2 sobkiller --games 100

# Compare to original sobv2
python3 comparator.py --bot1 sobv2 --bot2 sobkiller --games 100
```

## Tuning Parameters

You can adjust bluffing behavior by changing:

```python
# Bluff more often (currently 25%)
BLUFF_FREQUENCY = 0.35  # Bluff 35% of ultra-trash hands

# Bluff with slightly better hands
BLUFF_EQUITY_THRESHOLD = 0.40  # Bluff with equity < 40%

# Larger/smaller bluff sizes
BLUFF_BASE_SIZE = 1500      # Start with 1500 instead of 1000
BLUFF_INCREMENT = 800       # Increase by 800 each street (1500, 2300, 3100)

# More aggressive escalation
BLUFF_INCREMENT = 1500      # Increase by 1500 (1000, 2500, 4000)
```

## Safety Features

- **Bankroll protection:** Bluff size capped at remaining chips
- **Fold to resistance:** Immediately gives up if opponent fights back
- **Limited frequency:** Only 25% of ultra-trash hands (2-3% of all hands)
- **NOT all-in:** Controlled bet sizes, never risks entire stack on bluff

## Status

⚠️ **EXPERIMENTAL** - This is a temp file for testing. Do NOT use in production until thoroughly tested.

Original sobv2.py remains unchanged and can still be used.
