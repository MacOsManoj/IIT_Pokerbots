"""
tdv4.py - Adaptive Bot with Probe-Then-Bluff Premium Strategy (IIT Pokerbots 2026)

A strategic poker bot featuring controlled pot building and dynamic opponent adaptation.

KEY FEATURES:
  1. PREMIUM HANDS (JJ+ = equity >= 0.76):
     - Probe-then-bluff instead of all-in preflop
     - Raise 5-10x BB (100-200 chips) preflop to build pot
     - Bet 50-75% pot postflop on favorable boards (equity >= 55%)
     - Cut losses on bad boards (check when free, fold when facing bet if equity < 55%)
     - Bluff-balance premium sizing when opponent folds > 50%

  2. TRASH HANDS:
     - Dynamic chip thresholds based on opponent's preflop raise amounts
     - Dynamic equity floors based on hand quality (worse hand = higher floor needed)
     - Pot odds calling: required_equity = to_call / (pot + to_call) + 0.05 margin
     - Probe bluffing when opponent fold rate >= 60%

  3. LOCK-IN MODE:
     - Once bankroll >= 20000, fold everything to secure profit

  4. AUCTION EXPLOIT:
     - Intercepts opponent's bid before making our decision (second-price auction)
     - Hand quality scaling: trash hands bid less
"""

import random
import time
import socket

import eval7

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import Runner, parse_args


# ═══════════════════════════════════════════════════════════════════════════════
# GAME CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
ALLIN_THRESHOLD = 0.76    # Premium hand if equity >= this (JJ+ excluded TT=0.750)
BANKROLL_TARGET = 20000   # Lock-in mode: fold everything once reached


# ═══════════════════════════════════════════════════════════════════════════════
# CHIP THRESHOLDS (max chips to commit per street with trash hands)
# Slightly elevated thresholds allow for better pot odds calls
# ═══════════════════════════════════════════════════════════════════════════════
BASE_PREFLOP_THRESHOLD = 96    # Max chips preflop with trash (4.8x BB)
BASE_FLOP_THRESHOLD = 120      # Max chips on flop (6x BB)
BASE_TURN_THRESHOLD = 144      # Max chips on turn (7.2x BB)
BASE_RIVER_THRESHOLD = 168     # Max chips on river (8.4x BB)

# Dynamic threshold bounds (adapts to opponent's raise sizing)
MIN_PREFLOP_THRESHOLD = 40     # Floor: don't go below this
MAX_PREFLOP_THRESHOLD = 200    # Ceiling: protect against all-in exploits


# ═══════════════════════════════════════════════════════════════════════════════
# AUCTION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BASE_AUCTION_MAX = 100         # Auction bid ceiling (scaled by hand quality)

# ═══════════════════════════════════════════════════════════════════════════════
# DYNAMIC EQUITY FLOOR SYSTEM
# The worse your starting hand, the MORE postflop equity you need to continue.
# This prevents overvaluing marginal made hands when we started with trash.
# ═════════════════════════════════════════════Z══════════════════════════════════

# Hand quality normalization bounds (preflop equity range)
HAND_QUALITY_MIN_EQ = 0.32    # 32o = worst possible hand
HAND_QUALITY_MAX_EQ = 0.76    # JJ = premium threshold

# Value bet floor: equity needed to bet for value when checked to us
VALUE_BET_FLOOR_MIN = 0.58
VALUE_BET_FLOOR_MAX = 0.75

# Overbet floor: equity needed to call 2x+ pot bets
OVERBET_FLOOR_MIN = 0.68
OVERBET_FLOOR_MAX = 0.85

# All-in floor: equity needed to shove or call an all-in
ALLIN_FLOOR_MIN = 0.85
ALLIN_FLOOR_MAX = 0.95

# Street escalation: deeper streets require higher equity
STREET_MODIFIER = {
    "pre-flop": 0.00,
    "flop": 0.00,
    "turn": 0.03,    # +3% equity needed on turn
    "river": 0.05,   # +5% equity needed on river
}

# ═══════════════════════════════════════════════════════════════════════════════
# BLUFFING CONFIGURATION
# Activate bluffing when opponent folds frequently to our raises.
# ═══════════════════════════════════════════════════════════════════════════════
BLUFF_MIN_FOLD_PCT = 0.60       # Min fold % to activate bluffing (60%+)
BLUFF_MIN_SAMPLES = 8           # Min data points before enabling bluffs
BLUFF_MAX_FREQUENCY = 0.20      # Cap bluff hands at 20% of eligible
BLUFF_MAX_INVESTMENT = 100      # Max chips to risk per bluff hand
BLUFF_POT_ABANDON = 200         # Stop bluffing if pot exceeds this

# Bluff bet sizing (fraction of pot per street)
BLUFF_BET_FRACTION = {
    "flop": (0.30, 0.40),        # 30-40% pot
    "turn": (0.35, 0.45),        # 35-45% pot
    "river": (0.40, 0.50),       # 40-50% pot
}


# ═══════════════════════════════════════════════════════════════════════════════
# PREMIUM HAND CONFIGURATION (JJ+)
# Probe-then-bluff strategy: controlled pot building instead of all-in preflop
# ═══════════════════════════════════════════════════════════════════════════════
PREMIUM_PREFLOP_RAISE_MIN = 100  # 5x BB minimum raise
PREMIUM_PREFLOP_RAISE_MAX = 200  # 10x BB maximum raise
PREMIUM_POSTFLOP_BET_MIN = 0.50  # 50% pot bet
PREMIUM_POSTFLOP_BET_MAX = 0.75  # 75% pot bet
PREMIUM_BAD_BOARD_CUTOFF = 0.55  # Below this equity, check/fold (cut losses)


# ═══════════════════════════════════════════════════════════════════════════════
# POT ODDS CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
POT_ODDS_MARGIN = 0.05  # Extra equity buffer for calling (5%)

# ═══════════════════════════════════════════════════════════════════════════════
# PREFLOP EQUITY TABLE
# Precomputed equity vs random opponent (169 hand combos)
# ═══════════════════════════════════════════════════════════════════════════════
PREFLOP_EQ: dict[str, float] = {
    "AA": 0.852183, "KK": 0.824196, "QQ": 0.799191, "JJ": 0.774431,
    "TT": 0.750071, "99": 0.720453, "88": 0.691701, "AKs": 0.670392,
    "77": 0.662712, "AQs": 0.662197, "AJs": 0.653904, "AKo": 0.653225,
    "ATs": 0.646025, "AQo": 0.644168, "AJo": 0.635557, "KQs": 0.634290,
    "66": 0.633214, "A9s": 0.627679, "ATo": 0.627385, "KJs": 0.625471,
    "A8s": 0.619500, "KTs": 0.617886, "KQo": 0.614722, "A7s": 0.609758,
    "A9o": 0.607503, "KJo": 0.605522, "55": 0.603176, "QJs": 0.602634,
    "K9s": 0.600114, "A6s": 0.599297, "A5s": 0.599217, "A8o": 0.598766,
    "KTo": 0.597529, "QTs": 0.594641, "A4s": 0.590452, "A7o": 0.588179,
    "K8s": 0.583135, "A3s": 0.582202, "QJo": 0.581244, "K9o": 0.578094,
    "A6o": 0.577104, "A5o": 0.577031, "Q9s": 0.576858, "K7s": 0.575437,
    "JTs": 0.575282, "A2s": 0.574308, "QTo": 0.573167, "44": 0.570136,
    "A4o": 0.566985, "K6s": 0.566398, "K8o": 0.560125, "Q8s": 0.559834,
    "A3o": 0.558116, "K5s": 0.558004, "J9s": 0.556544, "Q9o": 0.553631,
    "JTo": 0.552446, "K7o": 0.551691, "A2o": 0.548994, "K4s": 0.548895,
    "Q7s": 0.543058, "K6o": 0.541934, "K3s": 0.540632, "T9s": 0.540342,
    "J8s": 0.540195, "33": 0.537315, "Q6s": 0.536214, "Q8o": 0.536113,
    "K5o": 0.533013, "J9o": 0.532693, "K2s": 0.531965, "Q5s": 0.527635,
    "K4o": 0.523368, "T8s": 0.523207, "J7s": 0.523185, "Q4s": 0.518402,
    "Q7o": 0.517932, "T9o": 0.515265, "J8o": 0.514831, "K3o": 0.514342,
    "Q6o": 0.510226, "Q3s": 0.510111, "98s": 0.508049, "T7s": 0.506383,
    "J6s": 0.506014, "K2o": 0.505214, "22": 0.503355, "Q2s": 0.501650,
    "Q5o": 0.501567, "J5s": 0.499679, "T8o": 0.497304, "J7o": 0.496586,
    "Q4o": 0.491312, "97s": 0.490826, "J4s": 0.490709, "T6s": 0.489450,
    "J3s": 0.482458, "Q3o": 0.482211, "98o": 0.480816, "87s": 0.479239,
    "T7o": 0.479023, "J6o": 0.478494, "96s": 0.474308, "J2s": 0.473701,
    "Q2o": 0.473027, "T5s": 0.472144, "J5o": 0.471488, "T4s": 0.465563,
    "97o": 0.462920, "86s": 0.462199, "J4o": 0.461736, "T6o": 0.460856,
    "95s": 0.457090, "T3s": 0.457052, "76s": 0.453805, "J3o": 0.452673,
    "87o": 0.450608, "T2s": 0.448209, "85s": 0.445399, "96o": 0.444856,
    "J2o": 0.443324, "T5o": 0.442603, "94s": 0.438509, "75s": 0.436531,
    "T4o": 0.435045, "93s": 0.432652, "86o": 0.432537, "65s": 0.431193,
    "84s": 0.427003, "95o": 0.426803, "T3o": 0.425601, "92s": 0.423873,
    "76o": 0.423251, "74s": 0.418559, "T2o": 0.416894, "54s": 0.414434,
    "85o": 0.414433, "64s": 0.413518, "83s": 0.408648, "94o": 0.406452,
    "75o": 0.405143, "82s": 0.402756, "73s": 0.400491, "93o": 0.399820,
    "65o": 0.399579, "53s": 0.397308, "63s": 0.395181, "84o": 0.394336,
    "92o": 0.391195, "43s": 0.386398, "74o": 0.385508, "72s": 0.381638,
    "54o": 0.381554, "64o": 0.380063, "52s": 0.378516, "62s": 0.376704,
    "83o": 0.374886, "42s": 0.368367, "82o": 0.368321, "73o": 0.365887,
    "53o": 0.362707, "63o": 0.360540, "32s": 0.359599, "43o": 0.351484,
    "72o": 0.346146, "52o": 0.343080, "62o": 0.341154, "42o": 0.332130,
    "32o": 0.323179,
}


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════
RANK_ORDER = "23456789TJQKA"
RANK_VAL = {r: i for i, r in enumerate(RANK_ORDER)}

# Pre-build "any two cards" range for for exact equity calculation
_ANY_TWO_STR = ",".join(
    r1 + s1 + r2 + s2
    for i, (r1, s1) in enumerate([(r, s) for r in '23456789TJQKA' for s in 'cdhs'])
    for r2, s2 in [(r, s) for r in '23456789TJQKA' for s in 'cdhs'][i + 1:]
)
_ANY_TWO_RANGE = eval7.HandRange(_ANY_TWO_STR)
_ONE_CARD_CACHE = {}


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _hkey(cards: list[str]) -> str:
    """Canonical hand key: 'AA', 'AKs', 'AKo', etc."""
    if len(cards) != 2:
        return ""
    r1, s1 = cards[0][0], cards[0][1]
    r2, s2 = cards[1][0], cards[1][1]
    v1, v2 = RANK_VAL.get(r1, 0), RANK_VAL.get(r2, 0)
    if v1 < v2:
        r1, r2 = r2, r1
        s1, s2 = s2, s1
    if v1 == v2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


# ═══════════════════════════════════════════════════════════════════════════
# EXPLOIT : RUNNER CLASS
# Intercepts opponent bid packets before we decide ours (second-price auction exploit).
# By knowing opponent's bid first, we can optimally outbid by exactly 1 chip.
# ═══════════════════════════════════════════════════════════════════════════

class ExploitRunner(Runner):
    """Intercepts 'A<amount>' packets to extract opponent's auction bid
    BEFORE our bot makes its own bid decision."""

    def __init__(self, pokerbot, socketfile):
        super().__init__(pokerbot, socketfile)

    def receive(self):
        while True:
            packet = self.socketfile.readline().strip().split(' ')
            if not packet:
                break
            for clause in packet:
                if clause and len(clause) > 1 and clause[0] == 'A':
                    try:
                        bid_amount = int(clause[1:])
                        if hasattr(self.pokerbot, '_receive_opponent_bid'):
                            self.pokerbot._receive_opponent_bid(bid_amount)
                    except (ValueError, IndexError):
                        pass
            yield packet


# ═══════════════════════════════════════════════════════════════════════════
# MAIN BOT CLASS
# ═══════════════════════════════════════════════════════════════════════════

class Player(BaseBot):

    def __init__(self) -> None:
        random.seed(time.time() + id(self))
        self.bankroll = 0.0
        self.round_num = 0
        self._is_premium = False
        # Auction exploit state
        self.opponent_last_bid = None
        self._auction_decision_made = False
        self._current_street = ""
        # Opponent fold tracking (for bluff frequency decisions)
        self._opp_raises_faced = 0        # Times we raised
        self._opp_folds_after_raise = 0   # Times opponent folded after our raise
        self._we_raised_this_hand = False # Track if we raised in current hand
        # Dynamic BB bid tracking (track opponent's SB bids when we're BB)
        self._opp_sb_bids = []            # History of opponent's SB bids
        self._was_bb_this_hand = False    # Track if we were BB this hand
        
        # ═══════════════════════════════════════════════════════════════════
        # Dynamic street threshold tracking (amount-based)
        # ═══════════════════════════════════════════════════════════════════
        self._opp_raise_amounts = []        # Track opponent's raise amounts
        self._opp_raised_this_hand = False  # Did opponent raise preflop this hand?
        self._dynamic_preflop_threshold = BASE_PREFLOP_THRESHOLD
        self._dynamic_flop_threshold = BASE_FLOP_THRESHOLD
        self._dynamic_turn_threshold = BASE_TURN_THRESHOLD
        self._dynamic_river_threshold = BASE_RIVER_THRESHOLD
        
        # ═══════════════════════════════════════════════════════════════════
        # DYNAMIC EQUITY FLOOR: Track current hand's preflop equity
        # ═══════════════════════════════════════════════════════════════════
        self._current_pf_eq = 0.47          # Current hand's preflop equity
        self._current_hand_quality = 0.34   # Normalized hand quality (0.0-1.0)

        # ═══════════════════════════════════════════════════════════════════
        # BLUFFING STATE
        # ═══════════════════════════════════════════════════════════════════
        self._is_bluffing = False           # Are we in a bluff attempt this hand?
        self._bluff_invested = 0            # Total chips committed to bluffing this hand
        self._bluff_called = False          # Did opponent call our bluff? (stop bluffing)

    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        self.bankroll = game_info.bankroll
        self.round_num = game_info.round_num
        
        # Calculate and cache preflop equity + hand quality for this hand
        self._current_pf_eq = self._pf_eq(cs.my_hand)
        self._current_hand_quality = self._calc_hand_quality(self._current_pf_eq)
        self._is_premium = self._current_pf_eq >= ALLIN_THRESHOLD
        
        # Reset per-hand state
        self.opponent_last_bid = None
        self._auction_decision_made = False
        self._current_street = ""
        self._we_raised_this_hand = False
        self._was_bb_this_hand = cs.is_bb  # Track if we're BB this hand
        self._opp_raised_this_hand = False  # Reset opponent raise tracking
        
        # Reset bluff state
        self._is_bluffing = False
        self._bluff_invested = 0
        self._bluff_called = False
        
        # ═══════════════════════════════════════════════════════════════════
        # BLUFF DECISION: Roll dice for trash hands against fold-heavy opponents
        # ═══════════════════════════════════════════════════════════════════
        if not self._is_premium and self.bankroll < BANKROLL_TARGET:
            self._is_bluffing = self._should_bluff()
        
        # Update dynamic thresholds based on opponent behavior
        self._update_street_thresholds()

    def _should_bluff(self) -> bool:
        """Decide whether to bluff this hand based on opponent fold history."""
        if self._opp_raises_faced < BLUFF_MIN_SAMPLES:
            return False
        
        fold_pct = self._opp_folds_after_raise / self._opp_raises_faced
        
        if fold_pct < BLUFF_MIN_FOLD_PCT:
            return False
        
        # Dynamic frequency: scales with fold %
        # fold_pct 0.60 -> 0%, 0.70 -> 5%, 0.80 -> 10%, 0.90 -> 15%, 1.0 -> 20%
        bluff_freq = min((fold_pct - BLUFF_MIN_FOLD_PCT) * 0.5, BLUFF_MAX_FREQUENCY)
        
        return random.random() < bluff_freq

    def on_hand_end(self, game_info: GameInfo, cs: PokerState) -> None:
        # Track if opponent folded after we raised
        if self._we_raised_this_hand:
            self._opp_raises_faced += 1
            # Check if opponent folded (we won without showdown)
            if cs.payoff > 0 and not cs.opp_revealed_cards:
                self._opp_folds_after_raise += 1
        
        # Track opponent's SB bid when we were BB
        if self._was_bb_this_hand and self.opponent_last_bid is not None:
            self._opp_sb_bids.append(self.opponent_last_bid)
            if len(self._opp_sb_bids) > 50:
                self._opp_sb_bids = self._opp_sb_bids[-50:]

    def _update_street_thresholds(self) -> None:
        """
        Dynamically adjust street thresholds based on opponent's actual bet amounts.
        """
        if len(self._opp_raise_amounts) < 3:
            return
        
        avg_raise = sum(self._opp_raise_amounts) / len(self._opp_raise_amounts)
        
        # Enforce both MIN and MAX bounds for preflop threshold
        self._dynamic_preflop_threshold = max(MIN_PREFLOP_THRESHOLD, min(int(avg_raise + 5), MAX_PREFLOP_THRESHOLD))
        
        # Scale postflop thresholds, but keep minimum at 50% of base (prevent extreme drops)
        scale_factor = max(0.5, min(avg_raise / 100, 1.5))
        
        self._dynamic_flop_threshold = int(BASE_FLOP_THRESHOLD * scale_factor)
        self._dynamic_turn_threshold = int(BASE_TURN_THRESHOLD * scale_factor)
        self._dynamic_river_threshold = int(BASE_RIVER_THRESHOLD * scale_factor)

    def _calc_hand_quality(self, pf_eq: float) -> float:
        """
        Normalize preflop equity to 0.0-1.0 scale.
        0.0 = worst trash (32o), 1.0 = near-premium (0.76+)
        """
        quality = (pf_eq - HAND_QUALITY_MIN_EQ) / (HAND_QUALITY_MAX_EQ - HAND_QUALITY_MIN_EQ)
        return max(0.0, min(1.0, quality))

    def _get_dynamic_floor(self, min_floor: float, max_floor: float, street: str) -> float:
        """
        Calculate dynamic equity floor based on hand quality and street.
        
        The WORSE your hand, the HIGHER the floor (you need better board equity).
        Formula: floor = MAX - hand_quality × (MAX - MIN) + street_modifier
        """
        hq = self._current_hand_quality
        base_floor = max_floor - hq * (max_floor - min_floor)
        modifier = STREET_MODIFIER.get(street, 0.0)
        return base_floor + modifier

    def _receive_opponent_bid(self, bid_amount: int) -> None:
        """Called by ExploitRunner when opponent's bid is intercepted."""
        if not self._auction_decision_made:
            self.opponent_last_bid = bid_amount

    def _pf_eq(self, hand: list[str]) -> float:
        return PREFLOP_EQ.get(_hkey(hand), 0.47)

    def _exact_equity(self, hand: list[str], board: list[str], opp_known: list[str] = None) -> float:
        """Calculate exact equity using eval7 against opponent's range."""
        h = [eval7.Card(c) for c in hand]
        b = [eval7.Card(c) for c in board]
        
        if opp_known and len(opp_known) == 2:
            villain = eval7.HandRange("".join(opp_known))
        elif opp_known and len(opp_known) == 1:
            t = opp_known[0]
            if t in _ONE_CARD_CACHE:
                villain = _ONE_CARD_CACHE[t]
            else:
                dk = [r + s for r in '23456789TJQKA' for s in 'cdhs']
                villain = eval7.HandRange(",".join(t + c for c in dk if c != t))
                _ONE_CARD_CACHE[t] = villain
        else:
            villain = _ANY_TWO_RANGE
            
        return float(eval7.py_hand_vs_range_exact(h, villain, b))

    def _get_street_threshold(self, street: str) -> int:
        """Get the DYNAMIC max bet threshold for the current street."""
        thresholds = {
            "pre-flop": self._dynamic_preflop_threshold,
            "flop": self._dynamic_flop_threshold,
            "turn": self._dynamic_turn_threshold,
            "river": self._dynamic_river_threshold,
        }
        return thresholds.get(street, self._dynamic_preflop_threshold)

    def _get_dynamic_bb_bid(self) -> int:
        """Calculate dynamic BB bid based on opponent's SB bidding history."""
        if len(self._opp_sb_bids) < 5:
            return 100
        
        avg_opp_sb_bid = sum(self._opp_sb_bids) / len(self._opp_sb_bids)
        
        if avg_opp_sb_bid > 150:
            return int(avg_opp_sb_bid * 0.85)
        elif avg_opp_sb_bid > 100:
            return int(avg_opp_sb_bid * 0.80)
        elif avg_opp_sb_bid > 50:
            return int(avg_opp_sb_bid * 0.70)
        else:
            return random.randint(5, 20)

    def _calc_pot_odds_required_equity(self, to_call: int, pot: int) -> float:
        """
        Calculate required equity using pot odds formula with margin.
        
        required_equity = to_call / (pot + to_call) + margin
        """
        if pot + to_call <= 0:
            return 1.0  # Can't call with 0 pot
        return (to_call / (pot + to_call)) + POT_ODDS_MARGIN

    # ═══════════════════════════════════════════════════════════════════════
    # Auction logic for TRASH hands (with hand quality scaling)
    # ═══════════════════════════════════════════════════════════════════════
    def _bid(self, cs: PokerState) -> int:
        """Calculate auction bid for trash hands, scaled by hand quality."""
        if cs.my_chips <= 0:
            return 0

        self._auction_decision_made = True
        
        # Hand quality scaled auction bid
        hq = self._current_hand_quality
        max_bid_for_hand = int(BASE_AUCTION_MAX * (hq ** 2))

        if cs.is_bb:
            dynamic_bid = self._get_dynamic_bb_bid()
            return min(dynamic_bid, max_bid_for_hand, cs.my_chips)

        opp_bid = self.opponent_last_bid

        if opp_bid is None:
            return 0

        if opp_bid == 0:
            return min(1, max_bid_for_hand, cs.my_chips)

        # If opponent bid is already above our max, drain them
        if opp_bid >= max_bid_for_hand:
            drain_bid = max(opp_bid - 1, 0)
            return min(drain_bid, cs.my_chips)
        
        # Otherwise try to win but cap at our hand's max
        win_bid = min(opp_bid + 1, max_bid_for_hand)
        return min(win_bid, cs.my_chips)

    # ═══════════════════════════════════════════════════════════════════════
    # BLUFF BET: Calculate probe bet size for bluffing
    # ═══════════════════════════════════════════════════════════════════════
    def _get_bluff_bet(self, cs: PokerState) -> int:
        """Calculate a bluff bet size for the current street.
        
        Returns 0 if bluffing should not happen (bail triggers hit).
        """
        street = cs.street
        
        # Bail trigger: pot too large
        if cs.pot >= BLUFF_POT_ABANDON:
            return 0
        
        # Bail trigger: already invested too much in this bluff
        if self._bluff_invested >= BLUFF_MAX_INVESTMENT:
            return 0
        
        # Bail trigger: opponent called a previous bluff bet
        if self._bluff_called:
            return 0
        
        # Get bet fraction range for this street
        fraction_range = BLUFF_BET_FRACTION.get(street)
        if fraction_range is None:
            return 0  # Don't bluff preflop
        
        frac_lo, frac_hi = fraction_range
        frac = random.uniform(frac_lo, frac_hi)
        
        bet_amt = int(cs.pot * frac)
        
        # Cap so total bluff investment doesn't exceed limit
        remaining_budget = BLUFF_MAX_INVESTMENT - self._bluff_invested
        bet_amt = min(bet_amt, remaining_budget)
        
        # Must be at least 1 chip to be meaningful
        if bet_amt < 1:
            return 0
        
        return bet_amt

    # ═══════════════════════════════════════════════════════════════════════
    # PREMIUM PROBE BET: Gradual pot building for premium hands
    # ═══════════════════════════════════════════════════════════════════════
    def _get_premium_probe_bet(self, cs: PokerState, exact_eq: float) -> int:
        """Calculate a controlled bet for premium hands postflop.
        
        Build pot gradually with 50-75% pot bets on favorable boards.
        Returns 0 if we should check instead (bad board or capping losses).
        """
        street = cs.street
        pot = max(cs.pot, 1)
        
        # If equity is bad, don't build pot further (cut losses)
        if exact_eq < PREMIUM_BAD_BOARD_CUTOFF:
            return 0
        
        # Calculate bet size: 50-75% pot
        frac = random.uniform(PREMIUM_POSTFLOP_BET_MIN, PREMIUM_POSTFLOP_BET_MAX)
        bet_amt = int(pot * frac)
        
        # Cap to reasonable amount (don't over-invest on single street)
        max_street_bet = self._get_street_threshold(street) * 2  # Allow more for premiums
        bet_amt = min(bet_amt, max_street_bet)
        
        # Cap at remaining chips
        bet_amt = min(bet_amt, cs.my_chips)
        
        return max(bet_amt, 1) if bet_amt > 0 else 0

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN MOVE LOGIC
    # ═══════════════════════════════════════════════════════════════════════
    def get_move(self, game_info: GameInfo, cs: PokerState):
        """Main decision function called every action point."""
        # Track street changes and reset commitment
        if cs.street != self._current_street:
            self._current_street = cs.street
            self._street_committed = 0

        # ==============================================================
        # PHASE 2 — LOCK-IN MODE
        # ==============================================================
        if self.bankroll >= BANKROLL_TARGET:
            if cs.street == "auction":
                return ActionBid(1)
            if cs.can_act(ActionCheck):
                return ActionCheck()
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # ==============================================================
        # PHASE 1 — HUNT MODE
        # ==============================================================

        # --- Auction handling ---
        if cs.street == "auction":
            if self._is_premium:
                return self._premium_auction(cs)
            else:
                return ActionBid(self._bid(cs))

        # --- Track opponent preflop raise amounts (with outlier detection) ---
        if cs.street == "pre-flop":
            if cs.cost_to_call > 10 and not self._opp_raised_this_hand:
                self._opp_raised_this_hand = True
                opp_total_bet = cs.opp_wager if hasattr(cs, 'opp_wager') else (cs.my_wager + cs.cost_to_call)
                
                if len(self._opp_raise_amounts) < 5:
                    if opp_total_bet <= 200:
                        self._opp_raise_amounts.append(opp_total_bet)
                else:
                    rolling_window = self._opp_raise_amounts[-50:]
                    mean = sum(rolling_window) / len(rolling_window)
                    variance = sum((x - mean) ** 2 for x in rolling_window) / len(rolling_window)
                    std = max(variance ** 0.5, 10)
                    
                    upper_bound = mean + 2 * std
                    if opp_total_bet <= upper_bound:
                        self._opp_raise_amounts.append(opp_total_bet)
                
                if len(self._opp_raise_amounts) > 50:
                    self._opp_raise_amounts = self._opp_raise_amounts[-50:]

        # --- PREMIUM HAND: probe-then-bluff strategy ---
        if self._is_premium:
            return self._premium_action(cs)

        # --- TRASH HAND: careful extraction with DYNAMIC thresholds + BLUFFING ---
        return self._trash_action(cs)

    def _premium_auction(self, cs: PokerState) -> ActionBid:
        """Auction strategy for premium hands - bid to win."""
        self._auction_decision_made = True
        
        if cs.is_bb:
            return ActionBid(random.randint(1, 5))
        
        opp_bid = self.opponent_last_bid
        if opp_bid is None:
            return ActionBid(min(10, cs.my_chips))
        if opp_bid == 0:
            return ActionBid(min(1, cs.my_chips))
        return ActionBid(min(opp_bid + 1, cs.my_chips))

    def _premium_action(self, cs: PokerState):
        """
        PROBE-THEN-BLUFF strategy for premium hands (JJ+).
        
        Instead of all-in preflop:
        1. Raise 5-10x BB (100-200) preflop to build pot
        2. Evaluate exact equity postflop
        3. On good boards (equity >= 55%): bet 50-75% pot
        4. On bad boards (equity < 55%): check/fold (lost 200 instead of 5000)
        5. If opponent fold rate > 50%, use same sizing for balance with bluffs
        """
        street = cs.street
        to_call = cs.cost_to_call

        # ── PREFLOP: Controlled raise (100-200 chips) ──
        if street == "pre-flop":
            if cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                # Raise 5-10x BB (100-200 chips)
                raise_amt = random.randint(PREMIUM_PREFLOP_RAISE_MIN, PREMIUM_PREFLOP_RAISE_MAX)
                raise_amt = max(mn, min(mx, raise_amt))
                self._we_raised_this_hand = True
                return ActionRaise(raise_amt)
            
            # If facing a raise, call if reasonable
            if to_call > 0:
                required_eq = self._calc_pot_odds_required_equity(to_call, cs.pot)
                if self._current_pf_eq >= required_eq:
                    if cs.can_act(ActionCall):
                        return ActionCall()
            
            return ActionCheck() if cs.can_act(ActionCheck) else ActionFold()

        # ── POSTFLOP: Exact equity evaluation ──
        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)
        
        # Calculate pot odds requirement
        required_eq = self._calc_pot_odds_required_equity(to_call, cs.pot) if to_call > 0 else 0.0
        
        # ── BAD BOARD: Cut losses ──
        if exact_eq < PREMIUM_BAD_BOARD_CUTOFF:
            if to_call > 0:
                # Fold unless pot odds are exceptional
                if exact_eq >= required_eq:
                    return ActionCall()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            return ActionCheck()
        
        # ── MONSTER EQUITY (85%+): Go for max value ──
        allin_floor = self._get_dynamic_floor(ALLIN_FLOOR_MIN, ALLIN_FLOOR_MAX, street)
        if exact_eq >= allin_floor:
            if cs.can_act(ActionRaise):
                _, mx = cs.raise_bounds
                self._we_raised_this_hand = True
                return ActionRaise(mx)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()
        
        # ── FACING A BET: Use pot odds + margin ──
        if to_call > 0:
            if exact_eq >= required_eq:
                # Good pot odds - call or raise
                if exact_eq >= 0.70 and cs.can_act(ActionRaise):
                    # Strong equity: raise for value
                    mn, mx = cs.raise_bounds
                    raise_amt = int(cs.pot * random.uniform(0.6, 0.8))
                    raise_amt = max(mn, min(mx, raise_amt))
                    self._we_raised_this_hand = True
                    return ActionRaise(raise_amt)
                return ActionCall()
            
            # Pot odds don't justify call - fold
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
        
        # ── CHECKED TO US: Build pot gradually ──
        bet_amt = self._get_premium_probe_bet(cs, exact_eq)
        
        if bet_amt > 0 and cs.can_act(ActionRaise):
            mn, mx = cs.raise_bounds
            bet_amt = max(mn, min(mx, bet_amt))
            self._we_raised_this_hand = True
            return ActionRaise(bet_amt)
        
        return ActionCheck()

    def _trash_action(self, cs: PokerState):
        """
        Trash hand strategy with DYNAMIC EQUITY FLOORS + POT ODDS + PROBE BLUFFING:
        - The worse your hand, the MORE postflop equity you need
        - Street escalation: later streets require higher equity
        - Chip threshold still adapts to opponent's preflop aggression
        - Pot odds formula: only call if equity >= required_equity + 0.05
        - NEW: Probe bluff bets when opponent is fold-heavy
        """
        street = cs.street
        threshold = self._get_street_threshold(street)  # Chip threshold
        to_call = cs.cost_to_call

        # ── PREFLOP ──
        if street == "pre-flop":
            total_committed = cs.my_wager + to_call
            
            if total_committed > threshold:
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            if to_call == 0:
                return ActionCheck()
            
            if to_call <= threshold - cs.my_wager:
                # Use pot odds for preflop calls too
                required_eq = self._calc_pot_odds_required_equity(to_call, cs.pot)
                if self._current_pf_eq >= required_eq:
                    if cs.can_act(ActionCall):
                        return ActionCall()
            
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # ── POSTFLOP (Flop/Turn/River) ──
        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)
        
        # ═══════════════════════════════════════════════════════════════════
        # DYNAMIC EQUITY FLOORS (based on preflop hand quality + street)
        # ═══════════════════════════════════════════════════════════════════
        value_bet_floor = self._get_dynamic_floor(VALUE_BET_FLOOR_MIN, VALUE_BET_FLOOR_MAX, street)
        overbet_floor = self._get_dynamic_floor(OVERBET_FLOOR_MIN, OVERBET_FLOOR_MAX, street)
        allin_floor = self._get_dynamic_floor(ALLIN_FLOOR_MIN, ALLIN_FLOOR_MAX, street)

        total_street_cost = cs.my_wager + to_call
        
        if to_call > 0:
            # ═══════════════════════════════════════════════════════════════
            # FACING A BET: All-in only when opponent commits chips first
            # ═══════════════════════════════════════════════════════════════
            if exact_eq >= allin_floor:
                if cs.can_act(ActionRaise):
                    _, mx = cs.raise_bounds
                    self._we_raised_this_hand = True
                    return ActionRaise(mx)
                if cs.can_act(ActionCall):
                    return ActionCall()
            
            # ═══════════════════════════════════════════════════════════════
            # OPPONENT BET/RAISED — check if we were bluffing
            # ═══════════════════════════════════════════════════════════════
            if self._is_bluffing and not self._bluff_called:
                # Opponent didn't fold to our bluff — they called or re-raised
                self._bluff_called = True
                # Fall through to normal trash logic (fold if too expensive)
            
            # Chip threshold check
            if total_street_cost > threshold:
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            # ═══════════════════════════════════════════════════════════════
            # POT ODDS CHECK: required_equity = to_call / (pot + to_call) + 0.05
            # ═══════════════════════════════════════════════════════════════
            required_eq = self._calc_pot_odds_required_equity(to_call, cs.pot)
            
            bet_to_pot_ratio = to_call / max(cs.pot, 1)
            
            # Overbet (2x+ pot): use higher floor AND pot odds
            if bet_to_pot_ratio >= 2.0:
                # Need both: high equity AND good pot odds
                if exact_eq >= overbet_floor and exact_eq >= required_eq:
                    if cs.can_act(ActionCall):
                        return ActionCall()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            # Normal bet: use pot odds formula
            if exact_eq >= required_eq:
                if cs.can_act(ActionCall):
                    return ActionCall()
            
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
        
        # ═══════════════════════════════════════════════════════════════════
        # CHECKED TO US (to_call == 0)
        # ═══════════════════════════════════════════════════════════════════
        
        # ── PROBE BLUFF: bet with weak hand to steal pot ──
        if self._is_bluffing and not self._bluff_called:
            bluff_bet = self._get_bluff_bet(cs)
            if bluff_bet > 0 and cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                bluff_bet = max(mn, min(mx, bluff_bet))
                self._bluff_invested += bluff_bet
                self._we_raised_this_hand = True  # Feeds into fold tracker
                return ActionRaise(bluff_bet)
        
        # ── Normal value bet if we meet the floor ──
        # Trash hands use conservative 25% pot sizing (vs 50-75% for premiums)
        # to minimize losses when called by better hands
        if exact_eq >= value_bet_floor:
            pot = cs.pot
            bet_amt = int(pot * 0.25)
            # Cap so total street investment doesn't exceed threshold
            bet_amt = max(0, min(bet_amt, threshold - cs.my_wager, cs.my_chips))
            
            if bet_amt > 0 and cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                bet_amt = max(mn, min(mx, bet_amt))
                self._we_raised_this_hand = True
                return ActionRaise(bet_amt)
        
        return ActionCheck()


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT — Uses ExploitRunner for auction interception
# ═══════════════════════════════════════════════════════════════════════════════
def run_exploit_bot(pokerbot, args):
    assert isinstance(pokerbot, BaseBot)
    try:
        sock = socket.create_connection((args.host, args.port))
    except OSError:
        print('Could not connect to {}:{}'.format(args.host, args.port))
        return
    socketfile = sock.makefile('rw')
    runner = ExploitRunner(pokerbot, socketfile)
    runner.run()
    socketfile.close()
    sock.close()


if __name__ == "__main__":
    run_exploit_bot(Player(), parse_args())
