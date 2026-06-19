# 🃏 IIT Pokerbots 2026

**Sneak Peek Hold'em** poker bot developed for the IIT Pokerbots 2026 competition.

**Final submitted bot:** `tdv4.py` (Adaptive Bot with Probe-Then-Bluff Premium Strategy)

## Game Variant

**Sneak Peek Hold'em** — a heads-up No Limit Texas Hold'em variant with a unique **auction mechanic**. After the flop, both players bid chips in a second-price auction to peek at one of the opponent's hole cards. The winner pays the loser's bid amount and gets revealed one random card from the opponent's hand.

- **1000 rounds** per match
- **5000 chip** starting stack per hand
- **20/10** big blind / small blind
- **30s** total game clock

## Repository Structure

```
poker_code/
├── main2/                    # Primary workspace (final bot versions)
│   ├── tdv4.py               # ✅ FINAL SUBMITTED BOT
│   ├── tdv5.py / tdv6.py     # Post-submission experiments
│   ├── tdv2.py / tdv3.py     # Earlier iterations
│   ├── eqrbot1.py / eqrbotv2.py  # Equity-ratio based bots
│   ├── sobv2.py / sobkiller.py   # Opponent exploitation bots
│   ├── botnig.py / ogbot.py  # Other experimental bots
│   ├── battle_royale.py      # Automated multi-bot tournament runner
│   ├── analyze_*.py          # Log analysis & stats tools
│   ├── engine.py             # Local game engine
│   └── config.py             # Match configuration
├── main1/                    # Initial workspace (earlier bots)
│   ├── speedbot*.py          # Speed-optimized bots
│   ├── exploit_bot.py        # Opponent exploit bot
│   ├── mccfr.py              # Monte Carlo CFR solver
│   └── equity_calc.cpp       # C++ equity calculator
├── bot-engine-2026-main/     # Clean reference engine
├── gto-poker-bot-main/       # GTO poker bot (DQN agent, external reference)
├── simple-poker-cfr-master/  # CFR reference implementation
├── WINS/ / logs/             # Match logs & results
├── IITPokerbots_PS.pdf       # Problem statement
└── Pokerbot Strategy Research Plan.pdf
```

## Final Bot: `tdv4` — Strategy Overview

### 1. Premium Hands (JJ+ / equity ≥ 0.76) — Probe-Then-Bluff
- **Preflop:** Raise 5–10× BB (100–200 chips) instead of shoving all-in
- **Postflop (good board, equity ≥ 55%):** Bet 50–75% pot to build value
- **Postflop (bad board, equity < 55%):** Check/fold to cut losses early
- **Balance:** Uses same sizing structure for bluffs when opponent folds >50%

### 2. Trash Hands — Dynamic Thresholds + Pot Odds
- **Adaptive chip limits:** Preflop/flop/turn/river thresholds dynamically adjust to opponent's raise sizing (with outlier detection)
- **Dynamic equity floors:** Worse starting hands require *more* postflop equity to continue (prevents overvaluing marginal made hands)
- **Pot odds calling:** `required_equity = to_call / (pot + to_call) + 5% margin`
- **Probe bluffing:** When opponent folds ≥60% to raises, fires 30–50% pot bets

### 3. Lock-In Mode
- Once bankroll ≥ 20,000 chips, folds everything to secure the win

### 4. Auction Exploit (Second-Price Sneak)
- Uses a custom `ExploitRunner` that intercepts the opponent's bid packet *before* making its own decision
- As SB: Outbids opponent by exactly 1 chip (or drains them with losing bids)
- As BB: Uses dynamic bidding based on opponent's SB bid history
- Hand quality scaling: Trash hands bid less (bid ∝ quality²)

### 5. Opponent Modeling
- Tracks opponent fold after raise rate → activates bluffing when fold% ≥ 60%
- Tracks opponent preflop raise amounts → adjusts street thresholds dynamically
- Rolling window of 50 samples with outlier rejection (mean ± 2σ)

## Bot Evolution

| Version | Key Idea |
|---------|----------|
| `speedbot` | Speed optimized basic strategy |
| `exploit_bot` | Opponent frequency exploitation |
| `eqrbot` | Equity-ratio based decisions |
| `sobv2` | Stack-off boundary optimization |
| `tdv2` | First threshold-dynamic version |
| `tdv3` | Added pot odds + equity floors |
| **`tdv4`** | **✅ Probe-then-bluff premiums, auction exploit, bluffing system** |
| `tdv5/v6` | Post-submission experiments |

## How to Run

```bash
cd main2
pip install -r requirements.txt   # eval7
```

Edit `config.py` to set which bots to match:
```python
BOT_1_NAME = 'tdv4'
BOT_1_FILE = './tdv4.py'
BOT_2_NAME = 'tdv3'
BOT_2_FILE = './tdv3.py'
```

Run a match:
```bash
python engine.py               # full logs
python engine.py --small_log   # compressed logs
```

## Dependencies

- Python 3.10+
- `eval7` — fast poker hand evaluation & equity calculation

---
