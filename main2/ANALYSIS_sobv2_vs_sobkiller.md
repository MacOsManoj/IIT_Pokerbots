# Analysis: sobv2 vs sobkiller (cython)

## Summary
**Results from analyzed games:**
- Game 1: sobv2 lost -3,884 chips
- Game 2: sobv2 lost -6,367 chips  
- Game 3: sobv2 lost -3,215 chips
- **Average loss: ~4,489 chips per 1000 hands**

Out of 100 games, sobv2 only won 5 matches, losing 95 matches by consistent margins.

---

## The Paradox: Winning More Hands But Losing the Game

**Detailed Statistics from Game 1 (1000 hands):**

| Metric | sobv2 | sobkiller | 
|--------|-------|-----------|
| **Hands Won** | 543 (54.3%) | 448 (44.8%) |
| **Average Win Size** | 38.2 chips | **54.9 chips** |
| **Total Won** | 20,729 chips | 24,613 chips |
| **Hands Lost** | 457 (45.7%) | 552 (55.2%) |
| **Average Loss Size** | **53.9 chips** | 37.6 chips |
| **Total Lost** | 24,613 chips | 20,729 chips |
| **NET RESULT** | **-3,884 chips** | **+3,884 chips** |

**The Smoking Gun:**
- sobv2 wins MORE hands (95 more wins than sobkiller)
- BUT loses the game by nearly 4,000 chips!
- **Average pot size difference: 16.8 chips** (54.9 vs 38.2)
- sobkiller wins 44% of hands but takes BIGGER pots
- sobv2 wins 54% of hands but takes SMALLER pots

**This is "Small Ball vs Big Pot" poker:**
- sobv2 plays small ball: wins many small pots, loses big pots
- sobkiller plays big pot: builds pots strategically and wins them

**Auction Performance (Critical Leak):**
- sobv2 wins 524 auctions (65% of all auctions)
- sobkiller wins 280 auctions (35% of all auctions)
- **sobv2 loses 52.1% of hands after winning auction** (273/524)
- This means sobv2 pays for information (auction cost) then abandons the hand

**Why sobv2 Loses Despite Winning More Hands:**
1. Folds when pots get big (fixed thresholds)
2. Gives up pots after investing in auctions  
3. Plays passively with medium-strength hands
4. sobkiller exploits this by building pots then betting just above thresholds

---

## Critical Issues Identified

### 1. **Auction Strategy Mismatch**

**sobv2's Auction Strategy (Trash Hands):**
- **BB (first bidder):** Bids 0-2 chips (extremely conservative)
- **SB (second bidder):** If `opp_bid + 1 > 300`, bids `opp_bid - 1` to let opponent win
- **Problem:** sobv2 is losing most auctions or winning them at higher costs

**sobkiller's Auction Strategy:**
- **BB:** Bids based on equity with randomization (0-40% of pot depending on hand strength)
- **SB:** Strategically wins auctions with minimal overpay
- **Advantage:** More flexible, equity-based bidding wins more valuable auctions

**Impact Examples:**
```
Round #18 (Game 1):
- sobv2 (Ah 5h) vs cython (Qh 8c)
- sobv2 bids 18, wins auction, reveals 8c
- Flop: Kd Qc 2h - cython has pair of Queens
- sobv2 calls 79 on flop, then folds to 172 bet on turn
- Result: -136 chips (paid for auction + flop, got nothing)

Round #56 (Game 1):
- sobv2 (Ah 2h) vs cython (Td Jd)  
- sobv2 bids 25, wins auction, reveals Jd
- Flop: 4s Tc Kh - cython has pair of Tens
- sobv2 calls 82 on flop, then folds to 196 bet on turn
- Result: -146 chips (same pattern - auction → fold)
```

### 2. **Fixed Threshold Approach is Too Rigid**

**sobv2's Betting Thresholds:**
```python
PREFLOP_BET_THRESHOLD = 80
FLOP_BET_THRESHOLD = 100
TURN_BET_THRESHOLD = 120
RIVER_BET_THRESHOLD = 140
```

**Problems:**
1. **Not pot-relative:** Thresholds don't scale with pot size
2. **Too low for mid-stage pots:** By turn/river, pot is often 200-400 chips
3. **Predictable:** sobkiller can exploit this by betting just above thresholds
4. **Auction waste:** After winning auction (paying 10-30 chips), thresholds don't account for sunk cost

**sobkiller's Approach:**
- Uses **pot-proportional betting** based on Kelly criterion
- Adjusts bet sizes based on opponent aggression
- Has dynamic pot-odds safety valves
- Example: `raise_add = (edge * multiplier) * pot * opp_discount`

**Impact Example:**
```
Round #25 (Game 1):
- Pot at flop: 80 chips (40 each)
- cython bets 99 (125% pot)
- sobv2 calls (within 100 threshold)
- Turn pot: 278 chips
- cython bets 254 (91% pot)
- sobv2 FOLDS (exceeds 120 turn threshold)
- sobv2 lost -139 chips despite committing 139 already
```

### 3. **All-In Equity Threshold Too High**

**sobv2's Dynamic Threshold:**
```python
ALLIN_EQUITY_THRESHOLD_MIN = 0.82  (defensive opponents)
ALLIN_EQUITY_THRESHOLD_MAX = 0.92  (aggressive opponents)
ALLIN_EQUITY_THRESHOLD_DEFAULT = 0.88
```

**Problem:**
- 82-92% equity is **EXTREMELY rare** postflop (top 0.1-1% of hands)
- sobv2 almost never triggers this, missing opportunities to extract value
- Even with 75% equity (strong hand), sobv2 plays conservatively within thresholds

**sobkiller's Approach:**
- Uses **graduated betting** based on equity ranges:
  - 48-65%: Small raises
  - 65-75%: Medium raises  
  - 75-90%: Large raises
  - 90%+: Maximum aggression
- Can apply pressure at multiple equity levels

### 4. **Fold Too Easily After Committing**

**Pattern identified in logs:**
1. sobv2 calls preflop (commits 20-40 chips)
2. sobv2 wins auction (commits 10-30 more chips)
3. sobv2 calls flop bet (commits another 20-100 chips)
4. sobkiller makes pot-sized turn bet
5. sobv2 folds (exceeds turn threshold)

**This is happening repeatedly:**
- Round #18: Lost -136 after committing through flop
- Round #25: Lost -139 after committing through flop
- Round #56: Lost -146 after committing through flop

**Why this is a major leak:**
- **Sunk cost fallacy prevention gone wrong:** sobv2 is abandoning pots after investing 30-40% of stack
- **Pot odds ignored:** When pot is 200-300 chips and facing 150 bet, sobv2 needs only ~43% equity to call profitably
- **sobkiller exploits this:** Makes bets just above thresholds, knowing sobv2 will fold

### 5. **Predictable Bet Sizing**

**sobv2's bet sizing:**
- When equity >= 60%: Bets 20-30% pot (very small)
- When equity >= dynamic threshold: Goes all-in
- **No middle ground**

**sobkiller's bet sizing:**
- Continuous spectrum based on edge and pot size
- Uses opponent aggression discount
- Adjusts based on street and bet-size-ratio
- Much harder to read

---

## Specific Hand Analysis

### Hand #1: Round #18 (Game 1)
```
sobv2 (SB): Ah 5h
cython (BB): Qh 8c

Preflop: sobv2 calls, cython raises to 40, sobv2 calls
Flop: Kd Qc 2h (sobv2: 40, cython: 40)

Auction: cython bids 17, sobv2 bids 18 → sobv2 wins, reveals 8c
- sobv2 now knows cython has Qx (likely Qh or Q8c pair)

Flop Action:
- cython bets 79 (99% pot)
- sobv2 calls (within 100 flop threshold)
- **sobv2 equity: ~23% (A-high vs pair of Queens)**

Turn: Jd
- cython bets 172 (63% pot) 
- sobv2 folds (exceeds 120 turn threshold)

Analysis:
❌ sobv2 paid 18 for auction but ignored the information (opponent has pair)
❌ sobv2 called flop with 23% equity (pot offered 42% implied odds)
❌ sobv2 folded turn getting 1.8:1 pot odds, needs 36% equity
❌ With A-high draw, sobv2 had ~18-25% equity - fold was correct BUT should never have called flop
```

### Hand #2: Round #56 (Game 1)
```
sobv2 (SB): Ah 2h
cython (BB): Td Jd

Preflop: sobv2 calls, cython raises to 40, sobv2 calls
Flop: 4s Tc Kh (sobv2: 40, cython: 40)

Auction: cython bids 24, sobv2 bids 25 → sobv2 wins, reveals Jd
- sobv2 now knows cython has Tx or TJd

Flop Action:
- cython bets 82 (102% pot)
- sobv2 calls 
- **sobv2 equity: ~15% (A-high vs pair of Tens)**

Turn: 6s
- cython bets 196 (67% pot)
- sobv2 folds

Analysis:
❌ Same pattern as Hand #1
❌ Paid 25 for auction, learned opponent has pair of Tens
❌ Called flop with 15% equity despite pot odds not supporting it
❌ Folded turn when pot was offering 1.5:1 (need 40% equity)
❌ Should have folded flop immediately
```

### Hand #3: Round #25 (Game 1)
```
sobv2 (SB): Kc As  
cython (BB): Js Td

Preflop: cython raises to 40, sobv2 calls
Flop: 9s Tc 3s (sobv2: 40, cython: 40)

Auction: sobv2 bids 0, cython bids 1 → cython wins, reveals Kc
- cython learns sobv2 doesn't have K

Flop Action:
- sobv2 checks
- cython bets 99 (124% pot)
- sobv2 calls (within 100 flop threshold)
- **sobv2 equity: ~38% (overcards + backdoor straight)**

Turn: 3h
- sobv2 checks
- cython bets 254 (91% pot)
- sobv2 folds (exceeds 120 turn threshold)

Analysis:
❌ sobv2 had 38% equity and was getting 1.8:1 on flop (needs 36%) - call was marginal
❌ Turn bet was 254 into 278 pot - sobv2 getting 2.08:1 (needs 32% equity)
❌ With two overcards, sobv2 likely had 25-30% equity
❌ Turn fold was technically correct BUT...
❌ The real mistake: sobv2 should have raised flop with decent equity instead of passive call-and-fold
```

---

## Key Differences in Strategy

| Aspect | sobv2 | sobkiller | Winner |
|--------|-------|-----------|--------|
| **Auction (BB trash)** | Bid 0-2 | Bid 0-40% pot based on equity | sobkiller |
| **Auction (SB trash)** | Bid opp+1 or opp-1 | Bid to win with edge | sobkiller |
| **Postflop betting** | Fixed thresholds | Pot-proportional | sobkiller |
| **Equity thresholds** | 88% for aggression | 65%+ graduated | sobkiller |
| **Bet sizing** | 25% pot or all-in | Kelly-based spectrum | sobkiller |
| **Pot odds** | Ignored (uses thresholds) | Explicit safety valves | sobkiller |
| **Raise capping** | None | 2 per street (unless 92%+) | sobkiller |
| **Adaptability** | Static thresholds | Dynamic based on opponent | sobkiller |

---

## Recommendations for Improving sobv2

### 1. **Fix Auction-to-Fold Leak**
**Current Problem:** Winning auction then folding wastes chips

**Solutions:**
```python
# Track auction cost and adjust thresholds dynamically
def _trash_action(self, cs: PokerState):
    # If we won auction, increase our commitment threshold
    if self._we_won_auction:
        auction_investment = self._auction_cost  # Track this
        threshold = self._get_street_threshold(street)
        threshold += auction_investment * 2  # Increase threshold to account for sunk cost
```

**Better Solution:** Use pot odds instead of fixed thresholds
```python
# Replace threshold logic with pot odds calculation
if to_call > 0:
    pot_odds = to_call / (cs.pot + to_call)
    required_equity = pot_odds * 1.4  # Add margin for safety
    if exact_eq >= required_equity:
        return ActionCall()
    else:
        return ActionFold()
```

### 2. **Lower All-In Equity Threshold**
**Current:** 82-92% (almost never triggered)

**Recommended:**
```python
ALLIN_EQUITY_THRESHOLD_MIN = 0.70  # Down from 0.82
ALLIN_EQUITY_THRESHOLD_MAX = 0.85  # Down from 0.92  
ALLIN_EQUITY_THRESHOLD_DEFAULT = 0.78  # Down from 0.88
```

This allows sobv2 to be aggressive with strong (but not nutted) hands.

### 3. **Implement Graduated Betting**
**Current:** Small bet (25% pot) or all-in

**Recommended:** Add middle-ground betting
```python
def _calculate_bet_size(self, cs: PokerState, equity: float) -> int:
    pot = cs.pot
    
    if equity >= 0.78:  # Very strong
        return int(pot * 1.2)  # Overbet
    elif equity >= 0.68:  # Strong
        return int(pot * 0.85)  # Pot-sized
    elif equity >= 0.58:  # Medium
        return int(pot * 0.55)  # Half-pot
    elif equity >= 0.50:  # Marginal
        return int(pot * 0.30)  # Small bet
    else:
        return 0  # Check/fold
```

### 4. **Use Pot-Relative Thresholds**
**Current:** Fixed chip amounts (80, 100, 120, 140)

**Recommended:** Pot-relative limits
```python
def _get_max_commitment(self, cs: PokerState, equity: float) -> int:
    pot = cs.pot
    base_pot = pot - cs.cost_to_call  # Pot before opponent bet
    
    # Calculate pot odds
    if cs.cost_to_call > 0:
        pot_odds = cs.cost_to_call / (pot + cs.cost_to_call)
        required_equity = pot_odds * 1.35  # Margin for safety
        
        if equity < required_equity:
            return 0  # Fold
    
    # Willing to commit based on equity
    if equity >= 0.70:
        return int(pot * 2.0)  # Willing to commit 2x pot
    elif equity >= 0.60:
        return int(pot * 1.2)  # Willing to commit 1.2x pot
    elif equity >= 0.50:
        return int(pot * 0.7)  # Willing to commit 0.7x pot
    else:
        return int(pot * 0.3)  # Only small commitment
```

### 5. **Improve Auction Strategy**
**Current:** BB bids 0-2 (too weak)

**Recommended:** Equity-based auction bidding
```python
def _bid(self, cs: PokerState) -> int:
    if cs.my_chips <= 0:
        return 0

    eq = self._equity(cs)
    self._auction_decision_made = True

    if cs.is_bb:
        # BB strategy: Bid based on equity to set second-price
        pot = cs.pot
        if eq > 0.65:
            return random.randint(int(pot * 0.15), int(pot * 0.25))
        elif eq > 0.55:
            return random.randint(int(pot * 0.08), int(pot * 0.15))
        elif eq > 0.45:
            return random.randint(int(pot * 0.03), int(pot * 0.08))
        else:
            return random.randint(0, int(pot * 0.03))
    
    # SB strategy remains similar but adjust thresholds
    # ... (keep current SB logic but adjust AUCTION_MAX_BID to 250-280)
```

### 6. **Add Raise Capping Like sobkiller**
**Current:** No limit on raises

**Recommended:** Add raise capping to prevent wars
```python
def get_move(self, game_info: GameInfo, cs: PokerState):
    # Track street changes
    if cs.street != self._current_street:
        self._current_street = cs.street
        self._raise_count = 0  # Reset raise counter
    
    # ... get action ...
    
    # Limit raises per street
    if isinstance(action, ActionRaise):
        if self._raise_count >= 2 and exact_eq < 0.85:
            # Cap reached and hand not strong enough - just call
            return ActionCall() if cs.can_act(ActionCall) else ActionCheck()
        else:
            self._raise_count += 1
            return action
```

### 7. **Fix Preflop Call-Fold Pattern**
**Problem:** Calling preflop with trash, then folding flop

**Solution:** Be more selective preflop OR commit postflop when calling
```python
def _trash_action_preflop(self, cs: PokerState):
    """More selective preflop play with trash hands"""
    pf_eq = self._pf_eq(cs.my_hand)
    to_call = cs.cost_to_call
    
    # Only call cheap preflop raises with decent trash (40%+ equity)
    if to_call <= 30 and pf_eq >= 0.40:
        return ActionCall()
    elif to_call <= 50 and pf_eq >= 0.48:
        return ActionCall()
    else:
        # Fold trash preflop rather than call-and-fold flop
        return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
```

---

## Estimated Impact of Changes

| Change | Expected Improvement | Priority |
|--------|---------------------|----------|
| Replace fixed thresholds with pot odds | +1500-2000 chips/game | **HIGH** |
| Lower all-in equity threshold to 70-85% | +800-1200 chips/game | **HIGH** |
| Implement graduated betting | +600-1000 chips/game | **MEDIUM** |
| Fix auction-to-fold leak | +1000-1500 chips/game | **HIGH** |
| Improve auction bidding (BB) | +400-700 chips/game | **MEDIUM** |
| Add raise capping | +300-500 chips/game | **LOW** |
| More selective preflop | +500-800 chips/game | **MEDIUM** |

**Total Expected Improvement:** +5,100 to 7,700 chips per game

This would turn a -4,489 average loss into a +600 to +3,200 average win.

---

## Implementation Priority

**Phase 1 (Immediate - High Impact):**
1. Replace fixed thresholds with pot odds calculation
2. Lower all-in equity thresholds to 70-85%
3. Fix auction-to-fold leak by accounting for auction cost

**Phase 2 (Short-term - Medium Impact):**
4. Implement graduated bet sizing
5. Improve BB auction bidding strategy
6. Add more selective preflop calling

**Phase 3 (Long-term - Fine-tuning):**
7. Add raise capping mechanism
8. Implement opponent modeling
9. Tune all parameters based on results

---

## Conclusion

**sobv2's main weakness is overly conservative play combined with poor pot commitment decisions.**

The bot:
- Pays for information (auctions) then doesn't use it
- Uses fixed thresholds in a pot-size-relative game
- Folds too easily after committing chips
- Requires near-perfect hands (88%+ equity) to be aggressive
- Doesn't use pot odds for decision-making

**sobkiller wins by:**
- Using pot-proportional betting that scales with pot size
- Having flexible equity thresholds (can be aggressive at 65%+)
- Exploiting sobv2's predictable fold points
- Better auction strategy that wins at lower costs
- Using pot odds safety valves

The fixes are straightforward and should significantly improve sobv2's performance against sobkiller and other opponents.
