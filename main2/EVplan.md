# EVplan — Expected Value Bot for Sneak Peek Hold'em

## Overview

An EV-based poker bot for the IIT Pokerbots 2026 "Sneak Peek Hold'em" competition, derived from `eqrbotv2`. The core change: **replace raw equity (win probability) with Expected Value (EV)** as the decision-making signal, while keeping the proven heuristics, auction exploit, mutation logic, and raise-sizing from eqrbotv2.

---

## What Changes from eqrbotv2

| Aspect | eqrbotv2 (Equity) | EVbot (Expected Value) |
|---|---|---|
| **Core metric** | Win probability (0–1) | EV in chips (can be negative, zero, or positive) |
| **Preflop lookup** | `PREFLOP_EQ` — hardcoded win probabilities | `PREFLOP_EV` — hardcoded precomputed EV in chips |
| **Postflop evaluation** | Monte Carlo → win/tie/loss ratio | Monte Carlo → average chip gain/loss |
| **Decision thresholds** | Equity buckets (0.84, 0.70, 0.57, 0.45, 0.33) | EV buckets in chips (scaled to pot) |
| **Pot odds comparison** | `strength >= pot_odds` | `EV > 0` (positive EV = call/raise) |

**What stays the same:** Auction exploit (ExploitRunner + BB defensive / SB exploit), information exploitation, safeguard mode, mutation logic, raise sizing, opponent model, board texture analysis.

---

## How We Calculate Expected Value

### Definition of EV

Expected Value measures the **average chip profit/loss** from playing a hand, not just the probability of winning. This is more accurate because it accounts for pot size and betting.

```
EV = P(win) × Amount_Won − P(lose) × Amount_Lost + P(tie) × Tie_Share
```

In the Monte Carlo context:
```
For each simulation:
  EV_sample = (my_hand_wins)  → +pot_share
              (my_hand_ties)  → +0
              (my_hand_loses) → −cost_invested

EV = average(all EV_samples)
```

### Preflop: Hardcoded Precomputed EV Table

**Why hardcode preflop?** Running Monte Carlo preflop every hand is too slow given the 20-second total time constraint (1000 rounds). Preflop EV is stable — it depends only on hole cards.

**How we precompute the values:**

1. For each of the 169 canonical hole card combos (AA, AKs, AKo, ... 32o):
2. Run **10,000+ Monte Carlo simulations** per combo:
   ```python
   for each simulation:
       a. Deal 2 random opponent hole cards
       b. Deal 5 random community cards
       c. Evaluate both 5-card hands (best of 7)
       d. Determine winner
       e. Calculate chip outcome:
          - Win  → record +pot_size (we win the pot minus what we put in)
          - Tie  → record 0 (pot split, net zero)
          - Lose → record −investment (we lose what we put in)
   
   EV = mean(all chip outcomes)
   ```
3. **Pot model for preflop EV:** Assume a standard preflop pot scenario:
   - Pot = SB(10) + BB(20) = 30 chips (limped pot)
   - Our investment = 20 chips (BB perspective, worst case)
   - Win → gain +10 (net = pot − our_investment = 30 − 20)
   - Lose → lose −20 (our BB investment)
   - Tie → 0
   - So: `EV = P(win) × 10 − P(lose) × 20`
   - This can also be expressed relative to pot: `EV_normalized = EV / pot`

4. Store as `PREFLOP_EV: dict[str, float]` — maps canonical key → normalized EV (chips per pot-unit)

**Precomputation script** (`compute_preflop_ev.py`):
```python
import eval7, random, itertools

FULL_DECK = [eval7.Card(r+s) for r in '23456789TJQKA' for s in 'cdhs']
POT = 30  # SB + BB
INVEST = 20  # our BB

def preflop_ev(hole_str_1, hole_str_2, iters=10000):
    h1, h2 = eval7.Card(hole_str_1), eval7.Card(hole_str_2)
    my = [h1, h2]
    remaining = [c for c in FULL_DECK if c not in my]
    total_ev = 0.0
    for _ in range(iters):
        draw = random.sample(remaining, 7)  # 2 opp + 5 community
        opp = draw[:2]
        board = draw[2:]
        my_val = eval7.evaluate(my + board)
        opp_val = eval7.evaluate(opp + board)
        if my_val > opp_val:
            total_ev += (POT - INVEST)  # +10
        elif my_val < opp_val:
            total_ev -= INVEST           # -20
        # tie → 0
    return total_ev / iters  # average EV per hand

# Run for all 169 canonical combos, save to dict
```

### Postflop: Monte Carlo EV Estimation

Post-flop EV is calculated **live** using Monte Carlo simulations (same structure as eqrbotv2's `_mc_eq`, but computing EV instead of win-rate).

**How it works:**

```python
def _mc_ev(hand, board, pot, my_invested, opp_known=None, iters=200):
    """
    Monte Carlo Expected Value estimation.
    
    Returns: average EV in chips
    
    Parameters:
    - hand: our 2 hole cards
    - board: community cards dealt so far
    - pot: total pot right now
    - my_invested: chips we've put in this round so far
    - opp_known: opponent's revealed card (if we won auction)
    - iters: simulation count (200 flop, 250 turn, 350 river)
    """
    remaining = full_deck - set(hand) - set(board) - set(opp_known or [])
    cards_needed_opp = 2 - len(opp_known or [])
    cards_needed_board = 5 - len(board)
    
    total_ev = 0.0
    for _ in range(iters):
        draw = random.sample(remaining, cards_needed_opp + cards_needed_board)
        opp_hole = opp_known + draw[:cards_needed_opp]
        full_board = board + draw[cards_needed_opp:]
        
        my_strength = eval7.evaluate(hand + full_board)
        opp_strength = eval7.evaluate(opp_hole + full_board)
        
        if my_strength > opp_strength:
            total_ev += (pot - my_invested)    # we win the pot minus our investment
        elif my_strength < opp_strength:
            total_ev -= my_invested             # we lose our investment
        # tie → 0 (pot split, net zero)
    
    return total_ev / iters
```

**Key difference from equity:** Instead of returning a win probability (0 to 1), this returns signed chip value. A hand with 70% equity in a 100-chip pot where we invested 40 has:
- Equity model: 0.70
- EV model: `0.70 × 60 − 0.30 × 40 = +30 chips`

**Normalization for decision buckets:** To use the same bucket thresholds, we normalize EV:
```python
ev_normalized = ev / pot  # ranges roughly from -1 to +1
# Maps cleanly to the bucket system
```

### Iteration Counts / Exact Enumeration

After the flop, the number of unknown card combinations is small enough
that eval7 can handle exact enumeration on later streets:

| Street | Method | Combos / Iters | Rationale |
|--------|--------|----------------|------------------------------------------|
| Preflop | Hardcoded table | 0 runtime | Pre-computed offline |
| Flop (auction) | MC sampling | 150 iters | Quick estimate for bidding |
| Flop (play) | MC sampling | 200 iters | ~178K combos too many for exact |
| Turn | Exact enum | ~15,180 combos | C(46,3) — eval7 handles this easily |
| River | Exact enum | ~990 combos | C(45,2) — trivially enumerable |

> **No heuristic board texture analysis needed.** Wet/dry board effects are
> captured naturally by the EV calculation itself — if the board has flush
> draws or straight possibilities, the enumeration/MC will show lower EV
> because opponents hit those draws more often.

---

## Decision Logic (Adapted from eqrbotv2)

### Architecture: Same Bucket System, EV-Calibrated

The decision structure keeps eqrbotv2's proven two-branch bucket system:

```
if opponent_raised (cost_to_call > 0):
    if EV > 0:              # positive expected value → continue
        if EV > raise_threshold → raise (with freq gating)
        elif EV > call_threshold → call
        else → fold
    else:                    # negative EV → fold
        → fold

if we_act_first (cost_to_call == 0):
    if EV > raise_threshold → raise (with freq gating)
    else → check
```

### Postflop EV Buckets (replacing equity buckets)

Using normalized EV (`ev / pot`):

| Bucket | EV/Pot Range | Action | Sizing |
|--------|-------------|--------|--------|
| **PREMIUM** | > 0.35 | Bet 15–30% pot (trap, keep them in) | Small to extract |
| **STRONG** | 0.20 – 0.35 | Facing bet → call; No bet → bet 33–50% pot | Medium value bet |
| **MEDIUM** | 0.07 – 0.20 | Facing bet → call if EV > cost; No bet → probe 30% IP | Careful |
| **MARGINAL** | 0.0 – 0.07 | Facing bet → call rarely (30%); No bet → check | Passive |
| **WEAK** | −0.10 – 0.0 | Facing bet → check/fold; No bet → bluff stab (8%) | Bluff sizing |
| **TRASH** | < −0.10 | Check/fold; No bet → pure bluff (3%) | Big bluff |

### Preflop Decision (keeps eqrbotv2 structure)

Using `PREFLOP_EV` normalized values:

```
IP (SB):
  EV > 0.10  → raise 2.5×BB
  EV > −0.05 → call
  else       → fold

OOP (BB):
  no bet facing → raise if EV > 0.12, else check
  facing raise:
    EV > 0.20 → 3-bet 2.5×pot
    EV > −0.08 → call
    else       → fold
```

---

## Auction Strategy (Exact eqrbotv2 Exploit — Unchanged)

The auction exploit is the bot's strongest edge and is kept **exactly** as-is:

### ExploitRunner
- Custom `Runner` subclass that intercepts raw socket packets
- Reads opponent's `A<amount>` bid **before** we submit ours
- SB (second bidder) sees opponent's bid and exploits it

### BB (First Bidder) — Conservative
Saves chips, bids proportional to equity:

| Equity | Bid Range |
|--------|-----------|
| > 0.80 | 25–40% pot |
| > 0.68 | 14–25% pot |
| > 0.55 | 6–14% pot |
| < 0.30 | 0–2% pot |
| < 0.40 | 1–4% pot |
| else | uncertainty × pot × 0.18 |

All bids capped at `min(7% stack, 45% pot)` with ±13% noise.

### SB (Second Bidder) — Exploit
Reads intercepted opponent bid:

| Condition | Our Bid |
|-----------|---------|
| `opp_bid` unknown | 10% pot if EV > 0, else 0 |
| `opp_bid == 0` | Bid 1 (guaranteed free info) |
| `EV < WEAK_THRESHOLD` | `opp_bid − 1` (DRAIN mode: let them win, bleed their chips) |
| `EV ≥ WEAK_THRESHOLD` | `opp_bid + 1` (WIN mode: pay minimum to see their card) |

---

## Information Exploitation (from eqrbotv2)

When we **won the auction** and see opponent's revealed card:
- Paired board or flush made → `EV adjustment −5% pot`; opp_card_strong = true
- High card (T+) + flush draw → `EV adjustment −3% pot`; opp_card_strong = true
- Low card (≤5) not on board → `EV adjustment +4% pot`; opp_card_weak = true
- Mid card (≤8) not on board → `EV adjustment +2% pot`; opp_card_weak = true

When **opponent won the auction** → flat `EV adjustment −3% pot`

> **No separate board texture adjustments.** The Monte Carlo / exact
> enumeration already accounts for board texture naturally.

---

## Facing-Bet Discount (from eqrbotv2)

Post-flop only, applied last:
```
bet_frac = cost_to_call / (pot − cost_to_call)
discount = min(0.18, bet_frac × 0.14)

if opponent won auction AND bet_frac > 0.25:
    discount ×= 1.7, capped at 0.20
if opp_card_strong:
    discount += 0.05

EV_normalized −= discount  (floor at large negative)
```

### Handling Re-Raises

The bot uses a **reactive architecture** — the engine queries us fresh on every
opponent action. So re-raises are handled naturally:

```
Opponent re-raises
  → engine queries bot again with updated game state
  → bot sees new (larger) cost_to_call
  → recalculates EV with current pot
  → facing-bet discount is larger (bigger bet_frac → bigger penalty)
  → adjusted EV drops → more likely to fold or just call

Example:
  Pot = 200, opponent bets 100   → bet_frac = 0.50, discount = 0.07
  We raise to 300, opp re-raises to 600 → bet_frac ≈ 0.75, discount = 0.105
  With opponent auction win:      → discount × 1.7 = 0.18 → likely fold
```

The bot does **not** simulate future betting rounds (no game tree search).
It makes one decision at a time, relying on the facing-bet discount to
naturally scale its caution with opponent aggression.

---

## Safeguard Mode (from eqrbotv2)

Activated when winning comfortably:
- `bankroll > (rounds_left × 15) + 10` (can afford to fold every hand)
- OR `bankroll > 500`

Effect: `EV_normalized −= 0.06` + preflop EV −= additional 0.05
Goal: play hyper-defensively to lock in the positive bankroll.

---

## Mutation Logic (from base bot)

Self-tuning mechanism that adjusts thresholds during play:

```python
MUTATE_ITERS = 25  # check every 25 hands
total_payoffs = 0

Every MUTATE_ITERS hands:
    if total_payoffs < 6 × MUTATE_ITERS (underperforming):
        if not already mutated:
            # Apply small random perturbations to thresholds
            d_raise_threshold += random(-0.02, 0.03)
            d_call_threshold  += random(-0.02, 0.03)
            mutated = True
        else:
            # Undo last mutation (it didn't help)
            reverse the perturbations
            mutated = False
    else:
        # Performing well, keep current settings
        mutated = False
    
    reset total_payoffs = 0
```

This applies to the EV bucket thresholds (raise/call) for both preflop and postflop.

---

## Raise Sizing (from base bot, adapted to EV)

### Preflop
- Standard open raise: `2.5 × BB = 50 chips`
- 3-bet: `2.5 × pot`

### Postflop (EV-aware sizing from the original bot)

```python
if EV_normalized > raise_threshold:
    # street-scaled sizing (deeper streets = bigger bets)
    raise_amount = my_pips + cost_to_call + street² × exp(1.8 × (EV_norm − 0.32)) − 12.5
    raise_amount = clamp(min_raise, max_raise)
```

This exponential sizing means:
- Marginal hands → small bets
- Strong hands → aggressive sizing that grows with street depth
- The `street²` factor naturally escalates on turn/river

---

## Opponent Model (from eqrbotv2)

Tracks opponent tendencies across hands:

| Metric | Calculation | Activation |
|--------|------------|------------|
| `opp_fold_pct` | folds / fold_opportunities | After 15 samples |
| `opp_agg` | raises / total_actions | After 15 samples |
| `bluff_mod` | +0.04 if fold_pct > 0.50, −0.04 if < 0.25 | After 50 hands |

Effect: `bluff_mod` adjusts bluff frequency in WEAK and TRASH buckets.

---

## Summary of Bot Components

```
┌─────────────────────────────────────────────────┐
│                    EVbot                         │
├─────────────────────────────────────────────────┤
│  PREFLOP_EV table  ←  precomputed offline       │
│  _mc_ev()          ←  live Monte Carlo (EV)     │
│  _eqr()            ←  EQR multiplier (removed*) │
│  _bid()            ←  auction exploit (exact)    │
│  ExploitRunner     ←  intercept opp bids (exact) │
│  Safeguard mode    ←  lock-in bankroll (exact)   │
│  Mutation logic    ←  self-tune thresholds       │
│  Opponent model    ←  track fold%/agg (exact)    │
│  Raise sizing      ←  street² × exp() formula    │
│  Info exploitation ←  revealed card logic (exact) │
│  Decision buckets  ←  6 tiers on EV_normalized   │
└─────────────────────────────────────────────────┘

* EQR is optional — since EV already captures "how much
  we actually win", EQR adjustments may be less necessary.
  Can keep as a secondary modifier or remove entirely.
```

---

## Why EV Over Equity?

1. **Pot-aware decisions:** A 60% equity hand in a 200-chip pot is worth more than in a 40-chip pot. EV captures this; raw equity does not.
2. **Better call/fold math:** `EV > 0` directly answers "should I call?" — no need to separately compute pot odds and compare.
3. **Natural raise sizing:** EV magnitude tells you how much to bet (proportional to expected profit), rather than mapping a probability to a bet size with arbitrary formulas.
4. **Investment-aware:** EV accounts for sunk costs — how much we've already put in determines the effective decision, not just win probability.
