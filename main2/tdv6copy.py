"""
tdv6.py - Adaptive Bot with VPIP/PFR Profiling + Postflop Probe Strategy (IIT Pokerbots 2026)

A strategic poker bot featuring opponent profiling and gradual pot building.

KEY FEATURES:
  1. VPIP/PFR OPPONENT PROFILING:
     - Tracks Voluntarily Put In Pot (VPIP) and Preflop Raise (PFR) percentages
     - Fixes the "mirroring bug" caused by tracking raw average raise sizes
     - Classifies opponents into 4 categories:
       * Maniacs (PFR >= 45%): Low threshold, trap with premiums
       * Nits (PFR < 12% OR VPIP < 40%): High threshold, steal freely
       * Calling Stations (VPIP >= 55%): Moderate threshold, value-bet heavy
       * Balanced: Default thresholds
     
  2. POSTFLOP MULTI-BET PROBE STRATEGY:
     - Adapts preflop probe strategy to postflop streets
     - Allows multiple escalating bets per street:
       * Max 300 chips per individual bet
       * Max 1200 chips total per street
       * 1.3x escalation factor for subsequent bets
     - Builds pots gradually without committing full stack in one action

  3. PREMIUM HANDS (JJ+ = equity >= 0.76):
     - Probe-then-bluff instead of all-in preflop
     - Raise 5-10x BB (100-200 chips) preflop to build pot
     - Bet 50-75% pot postflop on favorable boards (equity >= 55%)
     - Cut losses on bad boards (check/fold if equity < 55%)

  4. TRASH HANDS:
     - Dynamic chip thresholds based on opponent VPIP/PFR profile
     - Dynamic equity floors based on hand quality
     - Pot odds calling with 5% margin
     - Probe bluffing when opponent fold rate >= 60%

  5. LOCK-IN MODE:
     - Once bankroll >= 20000, fold everything to secure profit

  6. AUCTION EXPLOIT:
     - Intercepts opponent's bid before making our decision
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

STARTING_STACK = 5000
BIG_BLIND = 20
SMALL_BLIND = 10


# ═══════════════════════════════════════════════════════════════════════════════
# CHIP THRESHOLDS (max chips to commit per street with trash hands)
# Slightly elevated thresholds allow for better pot odds calls
# ═══════════════════════════════════════════════════════════════════════════════
BASE_PREFLOP_THRESHOLD = 96    # Max chips preflop with trash (4.8x BB)
BASE_FLOP_THRESHOLD = 120      # Max chips on flop (6x BB)
BASE_TURN_THRESHOLD = 144      # Max chips on turn (7.2x BB)
BASE_RIVER_THRESHOLD = 168     # Max chips on river (8.4x BB)

# Dynamic threshold bounds (adapts to opponent VPIP/PFR profile)
MIN_PREFLOP_THRESHOLD = 40     # Floor: don't go below this
MAX_PREFLOP_THRESHOLD = 200    # Ceiling: protect against all-in exploits


# ═══════════════════════════════════════════════════════════════════════════════
# AUCTION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
AUCTION_MAX_BID = 100          # If opp_bid + 1 > this, bid opp_bid - 1
BASE_AUCTION_MAX = 100         # Auction bid ceiling (scaled by hand quality)

# ═══════════════════════════════════════════════════════════════════════════════
# ALL-IN EQUITY THRESHOLDS (dynamically adjusted based on opponent fold rate)
# Higher fold rate opponent = lower threshold needed to commit stack
# ═══════════════════════════════════════════════════════════════════════════════
ALLIN_EQUITY_THRESHOLD_MIN = 0.82    # Aggressive: vs fold-heavy opponents
ALLIN_EQUITY_THRESHOLD_MAX = 0.92    # Conservative: vs call-heavy opponents
ALLIN_EQUITY_THRESHOLD_DEFAULT = 0.88


# ═══════════════════════════════════════════════════════════════════════════════
# DYNAMIC EQUITY FLOOR SYSTEM
# The worse your starting hand, the MORE postflop equity you need to continue.
# This prevents overvaluing marginal made hands when we started with trash.
# ═══════════════════════════════════════════════════════════════════════════════

# Hand quality normalization bounds (preflop equity range)
HAND_QUALITY_MIN_EQ = 0.32    # 32o = worst possible hand
HAND_QUALITY_MAX_EQ = 0.76    # JJ = premium threshold

# Call floor: equity needed to call a bet
# Near-premium hands (high quality) need only MIN; trash hands need MAX
CALL_FLOOR_MIN = 0.45
CALL_FLOOR_MAX = 0.65

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
PREMIUM_BLUFF_FOLD_THRESHOLD = 0.50  # Use premium sizing as bluff if fold > 50%


# ═══════════════════════════════════════════════════════════════════════════════
# POT ODDS CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
POT_ODDS_MARGIN = 0.05  # Extra equity buffer for calling (5%)


# ═══════════════════════════════════════════════════════════════════════════════
# POSTFLOP MULTI-BET PROBE CONFIGURATION
# Allows multiple escalating bets per street for gradual pot building
# ═══════════════════════════════════════════════════════════════════════════════
POSTFLOP_PROBE_MAX_SINGLE_BET = 300    # Max chips per individual bet/raise
POSTFLOP_PROBE_MAX_STREET_TOTAL = 1200 # Max chips to commit per street (cumulative)
POSTFLOP_PROBE_BET_ESCALATION = 1.3    # 1.3x escalation factor for subsequent bets
POSTFLOP_PROBE_BASE_FRACTION = (0.40, 0.60)  # Base bet sizing: 40-60% of pot

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

# Pre-build "any two cards" range for Monte Carlo equity calculation
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
# EXPLOIT RUNNER CLASS
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
        # DYNAMIC VPIP / PFR PROFILING
        # ═══════════════════════════════════════════════════════════════════
        self._hands_played = 0              # Total hands observed
        self._opp_vpip_count = 0            # Hands where opponent voluntarily put in chips preflop
        self._opp_pfr_count = 0             # Hands where opponent raised preflop
        self._opp_vpip_this_hand = False    # Flag for current hand
        self._opp_pfr_this_hand = False     # Flag for current hand
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
        
        # ═══════════════════════════════════════════════════════════════════
        # PREMIUM PROBE-THEN-BLUFF STATE
        # ═══════════════════════════════════════════════════════════════════
        self._premium_invested = 0          # Track chips invested with premium hand
        self._premium_should_bluff = False  # Should premium mimick bluff sizing?
        
        # ═══════════════════════════════════════════════════════════════════
        # POSTFLOP MULTI-BET PROBE STATE
        # ═══════════════════════════════════════════════════════════════════
        self._street_bets_made = 0          # Number of bets/raises we've made this street
        self._street_total_invested = 0    # Total chips invested this street through bets

    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        self.bankroll = game_info.bankroll
        self.round_num = game_info.round_num
        
        # ═══════════════════════════════════════════════════════════════════
        # VPIP/PFR TRACKING: Increment hand count and reset per-hand flags
        # ═══════════════════════════════════════════════════════════════════
        self._hands_played += 1
        self._opp_vpip_this_hand = False
        self._opp_pfr_this_hand = False
        
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
        
        # Reset per-street probe counters
        self._street_bets_made = 0
        self._street_total_invested = 0
        
        # Reset bluff state
        self._is_bluffing = False
        self._bluff_invested = 0
        self._bluff_called = False
        
        # Reset premium probe state
        self._premium_invested = 0
        self._premium_should_bluff = self._should_premium_bluff()
        
        # ═══════════════════════════════════════════════════════════════════
        # BLUFF DECISION: Roll dice for trash hands against fold-heavy opponents
        # ═══════════════════════════════════════════════════════════════════
        if not self._is_premium and self.bankroll < BANKROLL_TARGET:
            self._is_bluffing = self._should_bluff()
        
        # Update dynamic thresholds based on opponent behavior
        self._update_allin_threshold()
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

    def _should_premium_bluff(self) -> bool:
        """Decide if premium hand should use same sizing as bluffs for balance."""
        if self._opp_raises_faced < 5:
            return False
        
        fold_pct = self._opp_folds_after_raise / self._opp_raises_faced
        return fold_pct > PREMIUM_BLUFF_FOLD_THRESHOLD

    def on_hand_end(self, game_info: GameInfo, cs: PokerState) -> None:
        # ═══════════════════════════════════════════════════════════════════
        # VPIP/PFR FINALIZATION: Commit per-hand flags to lifetime counts
        # ═══════════════════════════════════════════════════════════════════
        if self._opp_vpip_this_hand:
            self._opp_vpip_count += 1
        if self._opp_pfr_this_hand:
            self._opp_pfr_count += 1
        
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
        Dynamically adjust street thresholds based on opponent VPIP & PFR.
        
        Opponent profiles:
        1. Maniacs (PFR ≥ 45%): Set threshold VERY LOW. Trap with premiums.
        2. Nits (PFR < 12% OR VPIP < 40%): Set threshold HIGH. Steal freely.
        3. Calling Stations (VPIP ≥ 55%): Moderate threshold. Play for value.
        4. Balanced: Default threshold.
        """
        if self._hands_played < 10:
            return  # Need a small sample size first
        
        vpip = self._opp_vpip_count / self._hands_played
        pfr = self._opp_pfr_count / self._hands_played
        
        # 1. The Maniacs (PFR > 45%)
        # Examples: Sushant (98%), Team_Rocket (71%), Fold_Club (67%), delta_one (46%)
        if pfr >= 0.45:
            # Action: Set threshold VERY LOW. Fold trash instantly. Trap with premiums.
            self._dynamic_preflop_threshold = 30  
            scale_factor = 0.8
            
        # 2. The Nits / Nit-Stations (PFR < 12% OR VPIP < 40%)
        # Examples: Dawn (VPIP 17%), Dev_s_Team (PFR 2%), FlushBox (PFR 7%), Nithin (PFR 7%)
        elif pfr < 0.12 or vpip < 0.40:
            # Action: Set threshold HIGH. Steal freely with any two cards.
            self._dynamic_preflop_threshold = 150 
            scale_factor = 1.3
            
        # 3. The Calling Stations (High VPIP > 55%, mid-low PFR)
        # Examples: Akshat (VPIP 77%, PFR 31%), Megatron, Taramani
        elif vpip >= 0.55:
            # Action: Moderate threshold. Play for value postflop.
            self._dynamic_preflop_threshold = 85  
            scale_factor = 1.0
            
        # 4. Normal / Balanced
        else:
            self._dynamic_preflop_threshold = 80
            scale_factor = 1.0
            
        # Apply the scale factor to postflop streets
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
        
        # Also cap at 100 per single street bet
        bet_amt = min(bet_amt, 100)
        
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
    # POSTFLOP MULTI-BET PROBE: Escalating bets within a street
    # ═══════════════════════════════════════════════════════════════════════
    def _get_postflop_probe_bet(self, cs: PokerState, exact_eq: float, min_equity: float = 0.55) -> int:
        """Calculate a probe bet for postflop streets with multi-bet capability.
        
        Allows multiple escalating bets per street instead of single large bets.
        - Max 300 per individual bet
        - Max 1200 total per street
        - Escalates bet size with each subsequent bet (1.3x factor)
        
        Returns 0 if we should check (bad equity, budget exhausted).
        """
        # Check if equity meets minimum requirement
        if exact_eq < min_equity:
            return 0
        
        # Check if we've already invested too much this street
        remaining_street_budget = POSTFLOP_PROBE_MAX_STREET_TOTAL - self._street_total_invested
        if remaining_street_budget <= 0:
            return 0
        
        pot = max(cs.pot, 1)
        
        # Calculate base bet size (40-60% of pot)
        frac_lo, frac_hi = POSTFLOP_PROBE_BASE_FRACTION
        base_frac = random.uniform(frac_lo, frac_hi)
        base_bet = int(pot * base_frac)
        
        # Escalate bet size if we've already bet this street
        if self._street_bets_made > 0:
            escalation = POSTFLOP_PROBE_BET_ESCALATION ** self._street_bets_made
            base_bet = int(base_bet * escalation)
        
        # Apply caps
        bet_amt = min(base_bet, POSTFLOP_PROBE_MAX_SINGLE_BET)  # Per-bet cap
        bet_amt = min(bet_amt, remaining_street_budget)          # Street budget cap
        bet_amt = min(bet_amt, cs.my_chips)                       # Chips cap
        
        return max(bet_amt, 1) if bet_amt > 0 else 0

    def _record_probe_bet(self, bet_amt: int) -> None:
        """Record a probe bet for per-street tracking."""
        self._street_bets_made += 1
        self._street_total_invested += bet_amt

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN MOVE LOGIC
    # ═══════════════════════════════════════════════════════════════════════
    def get_move(self, game_info: GameInfo, cs: PokerState):
        """Main decision function called every action point."""
        # Track street changes and reset commitment + per-street probe counters
        if cs.street != self._current_street:
            self._current_street = cs.street
            self._street_committed = 0
            # Reset per-street probe counters
            self._street_bets_made = 0
            self._street_total_invested = 0

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

        # --- Track Opponent VPIP & PFR ---
        if cs.street == "pre-flop":
            # If they wagered > big blind, they raised (PFR + VPIP)
            # If they wagered > small blind (from SB) or called BB, they VPIPed
            opp_wager = cs.opp_wager if hasattr(cs, 'opp_wager') else (cs.my_wager + cs.cost_to_call)
            
            if opp_wager > 20 and not self._opp_pfr_this_hand:
                self._opp_pfr_this_hand = True
                self._opp_vpip_this_hand = True
            elif opp_wager > 10 and not cs.is_bb and not self._opp_vpip_this_hand:
                # They completed the small blind to 20
                self._opp_vpip_this_hand = True

        # --- Detect auction outcome on flop ---
        if cs.street == "flop" and not self._opp_won_auction and not self._we_won_auction:
            if cs.opp_revealed_cards:
                self._we_won_auction = True
            else:
                self._opp_won_auction = True

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
                self._premium_invested += raise_amt
                return ActionRaise(raise_amt)
            
            # If facing a raise, call if reasonable
            if to_call > 0:
                required_eq = self._calc_pot_odds_required_equity(to_call, cs.pot)
                if self._current_pf_eq >= required_eq:
                    if cs.can_act(ActionCall):
                        self._premium_invested += to_call
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
        
        # ── FACING A BET: Use pot odds + margin, with multi-bet capability ──
        if to_call > 0:
            if exact_eq >= required_eq:
                # Good pot odds - call or raise
                if exact_eq >= 0.70 and cs.can_act(ActionRaise):
                    # Strong equity: raise for value using probe strategy
                    mn, mx = cs.raise_bounds
                    # Use probe bet with multi-bet tracking
                    raise_amt = self._get_postflop_probe_bet(cs, exact_eq, min_equity=0.60)
                    if raise_amt > 0:
                        raise_amt = max(mn, min(mx, raise_amt))
                        self._we_raised_this_hand = True
                        self._record_probe_bet(raise_amt)
                        return ActionRaise(raise_amt)
                return ActionCall()
            
            # Pot odds don't justify call - fold
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
        
        # ── CHECKED TO US: Build pot gradually with probe strategy ──
        bet_amt = self._get_postflop_probe_bet(cs, exact_eq, min_equity=PREMIUM_BAD_BOARD_CUTOFF)
        
        if bet_amt > 0 and cs.can_act(ActionRaise):
            mn, mx = cs.raise_bounds
            bet_amt = max(mn, min(mx, bet_amt))
            self._we_raised_this_hand = True
            self._record_probe_bet(bet_amt)
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
        
        # ── PROBE BLUFF: bet with weak hand to steal pot (with multi-bet tracking) ──
        if self._is_bluffing and not self._bluff_called:
            bluff_bet = self._get_bluff_bet(cs)
            if bluff_bet > 0 and cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                bluff_bet = max(mn, min(mx, bluff_bet))
                self._bluff_invested += bluff_bet
                self._we_raised_this_hand = True  # Feeds into fold tracker
                self._record_probe_bet(bluff_bet)  # Track for multi-bet
                return ActionRaise(bluff_bet)
        
        # ── Normal value bet with probe strategy if we meet the floor ──
        if exact_eq >= value_bet_floor:
            # Use probe bet strategy for gradual pot building
            bet_amt = self._get_postflop_probe_bet(cs, exact_eq, min_equity=value_bet_floor)
            
            if bet_amt > 0 and cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                bet_amt = max(mn, min(mx, bet_amt))
                self._we_raised_this_hand = True
                self._record_probe_bet(bet_amt)
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
