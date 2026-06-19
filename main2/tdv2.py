"""
temp_drainv2.py — Adaptive Bot with Dynamic Street Thresholds (IIT Pokerbots 2026)

Counters exploit bots like Travelling_Salesmen that raise every hand preflop.

Strategy:
  Dynamic Thresholds:
    - Track opponent's preflop raise frequency and amounts
    - If opponent raises >70% of hands → they're bluffing, increase our call threshold
    - Adjust thresholds based on opponent's actual raise sizing
    
  Premium Hands (equity >= 0.76, excluding TT):
    - Go ALL-IN preflop
    
  Trash Hands (equity < 0.76):
    - Use DYNAMIC thresholds based on opponent behavior
    - Auction: Drain mode exploit strategy
    - Postflop: Equity-based decisions
      
  Phase 2 (Lock): Once bankroll >= target, fold everything.
"""

import random
import time
import socket

import eval7

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import Runner, parse_args

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ALLIN_THRESHOLD = 0.76    # Premium if equity >= this (excludes TT at 0.750071)
BANKROLL_TARGET = 20000   # Once reached, fold everything to lock in profit

STARTING_STACK = 5000
BIG_BLIND = 20
SMALL_BLIND = 10

# ---------------------------------------------------------------------------
# BASE thresholds (will be adjusted dynamically)
# ---------------------------------------------------------------------------
BASE_PREFLOP_THRESHOLD = 80       # Base max chips to commit preflop with trash
BASE_FLOP_THRESHOLD = 100         # Base max chips on flop
BASE_TURN_THRESHOLD = 120         # Base max chips on turn
BASE_RIVER_THRESHOLD = 140        # Base max chips on river

# Dynamic threshold bounds
MIN_PREFLOP_THRESHOLD = 40        # Never go below this
MAX_PREFLOP_THRESHOLD = 200       # Never go above this (protect against all-in exploits)

# Auction exploit thresholds
AUCTION_MAX_BID = 100           # If opp_bid + 1 > this, bid opp_bid - 1

# Exact equity threshold for all-in (dynamically adjusted based on opponent)
ALLIN_EQUITY_THRESHOLD_MIN = 0.82
ALLIN_EQUITY_THRESHOLD_MAX = 0.92
ALLIN_EQUITY_THRESHOLD_DEFAULT = 0.88

# ---------------------------------------------------------------------------
# DYNAMIC EQUITY FLOOR SYSTEM (based on preflop hand quality)
# The worse your starting hand, the MORE postflop equity you need
# ---------------------------------------------------------------------------
# Hand quality normalization bounds
HAND_QUALITY_MIN_EQ = 0.32    # 32o equity (worst possible)
HAND_QUALITY_MAX_EQ = 0.76    # Premium threshold

# Call floor: MIN for near-premium, MAX for worst trash
CALL_FLOOR_MIN = 0.45
CALL_FLOOR_MAX = 0.65

# Value bet floor (checked to us)
VALUE_BET_FLOOR_MIN = 0.58
VALUE_BET_FLOOR_MAX = 0.75

# Overbet floor (2x+ pot bets)
OVERBET_FLOOR_MIN = 0.68
OVERBET_FLOOR_MAX = 0.85

# All-in floor
ALLIN_FLOOR_MIN = 0.85
ALLIN_FLOOR_MAX = 0.95

# Street escalation modifiers (deeper streets = higher requirements)
STREET_MODIFIER = {
    "pre-flop": 0.00,
    "flop": 0.00,
    "turn": 0.03,
    "river": 0.05,
}

# Auction bid scaling
BASE_AUCTION_MAX = 100

# ---------------------------------------------------------------------------
# Preflop equity table
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
RANK_ORDER = "23456789TJQKA"
RANK_VAL = {r: i for i, r in enumerate(RANK_ORDER)}

# Pre-build "any two cards" range for exact equity calculation
_ANY_TWO_STR = ",".join(
    r1 + s1 + r2 + s2
    for i, (r1, s1) in enumerate([(r, s) for r in '23456789TJQKA' for s in 'cdhs'])
    for r2, s2 in [(r, s) for r in '23456789TJQKA' for s in 'cdhs'][i + 1:]
)
_ANY_TWO_RANGE = eval7.HandRange(_ANY_TWO_STR)
_ONE_CARD_CACHE = {}


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


# ===========================================================================
#  ExploitRunner — intercepts opponent bid packets
# ===========================================================================
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


# ===========================================================================
class Player(BaseBot):

    def __init__(self) -> None:
        random.seed(time.time() + id(self))
        self.bankroll = 0.0
        self.round_num = 0
        self._is_premium = False
        # Auction exploit state
        self.opponent_last_bid = None
        self._auction_decision_made = False
        self._we_won_auction = False
        self._opp_won_auction = False
        # Track chips committed per street in this hand (for trash threshold)
        self._street_committed = 0
        self._current_street = ""
        # Opponent fold tracking (for dynamic ALLIN_EQUITY_THRESHOLD)
        self._opp_raises_faced = 0        # Times we raised
        self._opp_folds_after_raise = 0   # Times opponent folded after our raise
        self._we_raised_this_hand = False # Track if we raised in current hand
        self._dynamic_allin_threshold = ALLIN_EQUITY_THRESHOLD_DEFAULT
        # Dynamic BB bid tracking (track opponent's SB bids when we're BB)
        self._opp_sb_bids = []            # History of opponent's SB bids
        self._was_bb_this_hand = False    # Track if we were BB this hand
        
        # ═══════════════════════════════════════════════════════════════════
        # NEW: Dynamic street threshold tracking (amount-based)
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
        self._we_won_auction = False
        self._opp_won_auction = False
        self._street_committed = 0
        self._current_street = ""
        self._we_raised_this_hand = False
        self._was_bb_this_hand = cs.is_bb  # Track if we're BB this hand
        self._opp_raised_this_hand = False  # Reset opponent raise tracking
        
        # Update dynamic thresholds based on opponent behavior
        self._update_allin_threshold()
        self._update_street_thresholds()

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

    def _update_allin_threshold(self) -> None:
        """Update the all-in equity threshold based on opponent fold percentage."""
        if self._opp_raises_faced < 5:
            self._dynamic_allin_threshold = ALLIN_EQUITY_THRESHOLD_DEFAULT
            return
        
        fold_pct = self._opp_folds_after_raise / self._opp_raises_faced
        self._dynamic_allin_threshold = (
            ALLIN_EQUITY_THRESHOLD_MAX - 
            fold_pct * (ALLIN_EQUITY_THRESHOLD_MAX - ALLIN_EQUITY_THRESHOLD_MIN)
        )

    def _update_street_thresholds(self) -> None:
        """
        Dynamically adjust street thresholds based on opponent's actual bet amounts.
        
        Simple logic: Set threshold to match opponent's typical raise amount.
        If they raise to 102, our threshold should be ~105 to call it.
        """
        if len(self._opp_raise_amounts) < 3:
            # Not enough data, use base thresholds
            return
        
        # Use the average of opponent's raise amounts
        avg_raise = sum(self._opp_raise_amounts) / len(self._opp_raise_amounts)
        
        # ═══════════════════════════════════════════════════════════════════
        # Simple amount-based threshold: match their bet size + small buffer
        # ═══════════════════════════════════════════════════════════════════
        
        # Preflop: Set threshold to their average raise + 5 chip buffer
        self._dynamic_preflop_threshold = min(int(avg_raise + 5), MAX_PREFLOP_THRESHOLD)
        
        # Postflop: Scale proportionally from their preflop aggression
        # If they raise big preflop, they'll likely bet big postflop too
        # Cap scale factor at 1.5 to prevent extreme thresholds
        scale_factor = min(avg_raise / 100, 1.5)  # 102 raise -> 1.02x, 150 raise -> 1.5x max
        
        self._dynamic_flop_threshold = int(BASE_FLOP_THRESHOLD * scale_factor)
        self._dynamic_turn_threshold = int(BASE_TURN_THRESHOLD * scale_factor)
        self._dynamic_river_threshold = int(BASE_RIVER_THRESHOLD * scale_factor)

    def _get_allin_threshold(self) -> float:
        """Get the current dynamic all-in equity threshold."""
        return self._dynamic_allin_threshold

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

    # ------------------------------------------------------------------
    # Auction logic for TRASH hands (with hand quality scaling)
    # ------------------------------------------------------------------
    def _bid(self, cs: PokerState) -> int:
        if cs.my_chips <= 0:
            return 0

        self._auction_decision_made = True
        
        # ═══════════════════════════════════════════════════════════════════
        # HAND QUALITY SCALED AUCTION BID
        # Trash hands don't try to win auction, eliminating "buy and fold"
        # ═══════════════════════════════════════════════════════════════════
        hq = self._current_hand_quality
        max_bid_for_hand = int(BASE_AUCTION_MAX * (hq ** 2))
        # 32o (quality 0.01): max ~0 chips
        # 75o (quality 0.19): max ~4 chips
        # T8o (quality 0.40): max ~16 chips
        # KJo (quality 0.65): max ~42 chips
        # 99  (quality 0.91): max ~83 chips

        if cs.is_bb:
            dynamic_bid = self._get_dynamic_bb_bid()
            # Cap BB bid by hand quality too
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

    # ------------------------------------------------------------------
    def get_move(self, game_info: GameInfo, cs: PokerState):
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
            # Detect if opponent raised (cost_to_call > just the blind difference)
            if cs.cost_to_call > 10 and not self._opp_raised_this_hand:
                self._opp_raised_this_hand = True
                # Track the raise amount (total they raised to)
                opp_total_bet = cs.opp_wager if hasattr(cs, 'opp_wager') else (cs.my_wager + cs.cost_to_call)
                
                # Outlier detection: ignore values > 2 standard deviations from mean
                # For first few samples, use a reasonable cap (200)
                if len(self._opp_raise_amounts) < 5:
                    # Not enough data for stats, use simple cap
                    if opp_total_bet <= 200:
                        self._opp_raise_amounts.append(opp_total_bet)
                else:
                    # Calculate mean and std using ROLLING window (last 50 entries)
                    rolling_window = self._opp_raise_amounts[-50:]
                    mean = sum(rolling_window) / len(rolling_window)
                    variance = sum((x - mean) ** 2 for x in rolling_window) / len(rolling_window)
                    std = max(variance ** 0.5, 10)  # Minimum sigma of 10 to avoid near-zero bounds
                    
                    # Accept if within 2 sigma
                    upper_bound = mean + 2 * std
                    if opp_total_bet <= upper_bound:
                        self._opp_raise_amounts.append(opp_total_bet)
                
                # Keep only last 50 for rolling average
                if len(self._opp_raise_amounts) > 50:
                    self._opp_raise_amounts = self._opp_raise_amounts[-50:]

        # --- Detect auction outcome on flop ---
        if cs.street == "flop" and not self._opp_won_auction and not self._we_won_auction:
            if cs.opp_revealed_cards:
                self._we_won_auction = True
            else:
                self._opp_won_auction = True

        # --- PREMIUM HAND: all-in strategy ---
        if self._is_premium:
            return self._premium_action(cs)

        # --- TRASH HAND: careful extraction with DYNAMIC thresholds ---
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
        """All-in on premium hands, but check board danger postflop."""
        street = cs.street

        if street == "pre-flop":
            if cs.can_act(ActionRaise):
                _, mx = cs.raise_bounds
                self._we_raised_this_hand = True
                return ActionRaise(mx)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)
        
        if exact_eq < 0.65:
            return self._trash_action(cs)
        
        if cs.can_act(ActionRaise):
            _, mx = cs.raise_bounds
            self._we_raised_this_hand = True
            return ActionRaise(mx)
        if cs.can_act(ActionCall):
            return ActionCall()
        return ActionCheck()

    def _trash_action(self, cs: PokerState):
        """
        Trash hand strategy with DYNAMIC EQUITY FLOORS:
        - The worse your hand, the MORE postflop equity you need
        - Street escalation: later streets require higher equity
        - Chip threshold still adapts to opponent's preflop aggression
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
                if cs.can_act(ActionCall):
                    return ActionCall()
            
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # ── POSTFLOP (Flop/Turn/River) ──
        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)
        
        # ═══════════════════════════════════════════════════════════════════
        # DYNAMIC EQUITY FLOORS (based on preflop hand quality + street)
        # ═══════════════════════════════════════════════════════════════════
        call_floor = self._get_dynamic_floor(CALL_FLOOR_MIN, CALL_FLOOR_MAX, street)
        value_bet_floor = self._get_dynamic_floor(VALUE_BET_FLOOR_MIN, VALUE_BET_FLOOR_MAX, street)
        overbet_floor = self._get_dynamic_floor(OVERBET_FLOOR_MIN, OVERBET_FLOOR_MAX, street)
        allin_floor = self._get_dynamic_floor(ALLIN_FLOOR_MIN, ALLIN_FLOOR_MAX, street)

        # All-in: if equity exceeds our dynamic all-in floor
        if exact_eq >= allin_floor:
            if cs.can_act(ActionRaise):
                _, mx = cs.raise_bounds
                self._we_raised_this_hand = True
                return ActionRaise(mx)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        total_street_cost = cs.my_wager + to_call
        
        if to_call > 0:
            # Chip threshold check first
            if total_street_cost > threshold:
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            bet_to_pot_ratio = to_call / max(cs.pot, 1)
            
            # Overbet (2x+ pot): use higher floor
            if bet_to_pot_ratio >= 2.0:
                if exact_eq >= overbet_floor:
                    if cs.can_act(ActionCall):
                        return ActionCall()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            # Normal bet: use call floor
            if exact_eq >= call_floor:
                if cs.can_act(ActionCall):
                    return ActionCall()
            
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
        
        # Checked to us: value bet if we meet the floor
        if exact_eq >= value_bet_floor:
            pot = cs.pot
            bet_amt = int(pot * 0.25)
            bet_amt = min(bet_amt, threshold - cs.my_wager, cs.my_chips)
            
            if bet_amt > 0 and cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                bet_amt = max(mn, min(mx, bet_amt))
                self._we_raised_this_hand = True
                return ActionRaise(bet_amt)
        
        return ActionCheck()


# ===========================================================================
#  Entry point — uses ExploitRunner for auction interception
# ===========================================================================
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
