"""
sobv2.py — Sob-style Bot with Trash Hand Exploitation (IIT Pokerbots 2026)

Strategy:
  Premium Hands (equity >= 0.76, excluding TT):
    - Go ALL-IN preflop on premium hands
    - Same as sob.py behavior
    
  Trash Hands (equity < 0.76):
    - Preflop: stay within PREFLOP_BET_THRESHOLD, fold if opponent raises beyond
    - Auction: Use exploit strategy with max bid caps
      - SB: if opp_bid + 1 > 300 → bid opp_bid - 1; else bid opp_bid + 1
      - BB: bid small (0-2) for trash hands
    - Postflop: Calculate exact equity with eval7
      - If equity >= 0.982 (top 1.8%) → go ALL-IN
      - Otherwise bet within street threshold
      - If opponent raises beyond threshold → fold
      
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
# Trash hand betting thresholds per street (max we're willing to commit)
# ---------------------------------------------------------------------------
PREFLOP_BET_THRESHOLD = 80 #old =40      # Max chips to commit preflop with trash
FLOP_BET_THRESHOLD = 100 #old = 60        # Max chips to commit on flop with trash
TURN_BET_THRESHOLD = 120  #old = 80       # Max chips to commit on turn with trash
RIVER_BET_THRESHOLD = 140  #old = 100     # Max chips to commit on river with trash

# Auction exploit thresholds
AUCTION_MAX_BID = 100           # If opp_bid + 1 > this, bid opp_bid - 1

# Exact equity threshold for all-in (dynamically adjusted based on opponent)
ALLIN_EQUITY_THRESHOLD_MIN = 0.82   # old = 0.6 - For defensive opponents (high fold %)
ALLIN_EQUITY_THRESHOLD_MAX = 0.92   # old = 0.9 - For offensive opponents (low fold %)
ALLIN_EQUITY_THRESHOLD_DEFAULT = 0.88  # old = 0.8 - Starting default

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

    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        self.bankroll = game_info.bankroll
        self.round_num = game_info.round_num
        self._is_premium = self._pf_eq(cs.my_hand) >= ALLIN_THRESHOLD
        # Reset per-hand state
        self.opponent_last_bid = None
        self._auction_decision_made = False
        self._we_won_auction = False
        self._opp_won_auction = False
        self._street_committed = 0
        self._current_street = ""
        self._we_raised_this_hand = False
        self._was_bb_this_hand = cs.is_bb  # Track if we're BB this hand
        # Update dynamic threshold based on opponent fold history
        self._update_allin_threshold()

    def on_hand_end(self, game_info: GameInfo, cs: PokerState) -> None:
        # Track if opponent folded after we raised
        if self._we_raised_this_hand:
            self._opp_raises_faced += 1
            # Check if opponent folded (we won without showdown)
            # If payoff > 0 and opponent cards not revealed, they likely folded
            if cs.payoff > 0 and not cs.opp_revealed_cards:
                self._opp_folds_after_raise += 1
        
        # Track opponent's SB bid when we were BB
        # opponent_last_bid contains opp's bid when we're SB (second bidder)
        # When we're BB, we bid first, then opponent bids - we intercept their bid
        if self._was_bb_this_hand and self.opponent_last_bid is not None:
            self._opp_sb_bids.append(self.opponent_last_bid)
            # Keep only last 50 bids for rolling average
            if len(self._opp_sb_bids) > 50:
                self._opp_sb_bids = self._opp_sb_bids[-50:]

    def _update_allin_threshold(self) -> None:
        """Update the all-in equity threshold based on opponent fold percentage.
        
        Defensive opponents (high fold %) -> lower threshold (0.6)
        Offensive opponents (low fold %) -> higher threshold (0.9)
        """
        if self._opp_raises_faced < 5:
            # Not enough data, use default
            self._dynamic_allin_threshold = ALLIN_EQUITY_THRESHOLD_DEFAULT
            return
        
        fold_pct = self._opp_folds_after_raise / self._opp_raises_faced
        
        # Linear interpolation: high fold % -> low threshold, low fold % -> high threshold
        # fold_pct 0.0 (never folds) -> threshold = 0.9 (be more careful)
        # fold_pct 1.0 (always folds) -> threshold = 0.6 (can bluff more aggressively)
        self._dynamic_allin_threshold = (
            ALLIN_EQUITY_THRESHOLD_MAX - 
            fold_pct * (ALLIN_EQUITY_THRESHOLD_MAX - ALLIN_EQUITY_THRESHOLD_MIN)
        )

    def _get_allin_threshold(self) -> float:
        """Get the current dynamic all-in equity threshold."""
        return self._dynamic_allin_threshold

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
            # We know both opponent cards
            villain = eval7.HandRange("".join(opp_known))
        elif opp_known and len(opp_known) == 1:
            # We know one opponent card (auction revealed)
            t = opp_known[0]
            if t in _ONE_CARD_CACHE:
                villain = _ONE_CARD_CACHE[t]
            else:
                dk = [r + s for r in '23456789TJQKA' for s in 'cdhs']
                villain = eval7.HandRange(",".join(t + c for c in dk if c != t))
                _ONE_CARD_CACHE[t] = villain
        else:
            # We don't know opponent cards
            villain = _ANY_TWO_RANGE
            
        return float(eval7.py_hand_vs_range_exact(h, villain, b))

    def _get_street_threshold(self, street: str) -> int:
        """Get the max bet threshold for the current street."""
        thresholds = {
            "pre-flop": PREFLOP_BET_THRESHOLD,
            "flop": FLOP_BET_THRESHOLD,
            "turn": TURN_BET_THRESHOLD,
            "river": RIVER_BET_THRESHOLD,
        }
        return thresholds.get(street, PREFLOP_BET_THRESHOLD)

    def _get_dynamic_bb_bid(self) -> int:
        """Calculate dynamic BB bid based on opponent's SB bidding history.
        
        Strategy:
        - If opponent bids high as SB (>100 avg), bid ~80% of their avg to drain them
        - If opponent bids low (<50 avg), bid small to keep it cheap
        - Default to 100 with no history (safe middle ground)
        """
        if len(self._opp_sb_bids) < 5:
            # Not enough data, use safe default
            return 100
        
        avg_opp_sb_bid = sum(self._opp_sb_bids) / len(self._opp_sb_bids)
        
        if avg_opp_sb_bid > 150:
            # Opponent bids very high - drain them hard
            return int(avg_opp_sb_bid * 0.85)
        elif avg_opp_sb_bid > 100:
            # Opponent bids moderately high
            return int(avg_opp_sb_bid * 0.80)
        elif avg_opp_sb_bid > 50:
            # Opponent bids moderate
            return int(avg_opp_sb_bid * 0.70)
        else:
            # Opponent bids low - keep it cheap
            return random.randint(5, 20)

    # ------------------------------------------------------------------
    # Auction logic for TRASH hands
    # ------------------------------------------------------------------
    def _bid(self, cs: PokerState) -> int:
        if cs.my_chips <= 0:
            return 0

        self._auction_decision_made = True

        # ════════════════════════════════════════════════════════
        # BB (first bidder): DYNAMIC bid based on opponent history
        # ════════════════════════════════════════════════════════
        if cs.is_bb:
            dynamic_bid = self._get_dynamic_bb_bid()
            return min(dynamic_bid, cs.my_chips)

        # ════════════════════════════════════════════════════════
        # SB (second bidder): EXPLOIT opponent's known bid
        # ════════════════════════════════════════════════════════
        opp_bid = self.opponent_last_bid

        if opp_bid is None:
            # Couldn't intercept — bid 0 for trash
            return 0

        if opp_bid == 0:
            # Free info! Bid 1 to guarantee winning the auction
            return min(1, cs.my_chips)

        # Auction exploit strategy for trash hands:
        # if opp_bid + 1 > AUCTION_MAX_BID: bid opp_bid - 1 (let them win)
        # else: bid opp_bid + 1 (win auction cheaply, second-price = opp_bid)
        if opp_bid + 1 > AUCTION_MAX_BID:
            # Too expensive, let opponent win but they pay our bid
            drain_bid = max(opp_bid - 1, 0)
            return min(drain_bid, cs.my_chips)
        else:
            # Win the auction at second-price
            win_bid = opp_bid + 1
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
                # Premium: bid aggressively to win auction
                return self._premium_auction(cs)
            else:
                # Trash: use exploit strategy
                return ActionBid(self._bid(cs))

        # --- Detect auction outcome on flop ---
        if cs.street == "flop" and not self._opp_won_auction and not self._we_won_auction:
            if cs.opp_revealed_cards:
                self._we_won_auction = True
            else:
                self._opp_won_auction = True

        # --- PREMIUM HAND: all-in strategy (like sob) ---
        if self._is_premium:
            return self._premium_action(cs)

        # --- TRASH HAND: careful extraction with thresholds ---
        return self._trash_action(cs)

    def _premium_auction(self, cs: PokerState) -> ActionBid:
        """Auction strategy for premium hands - bid to win."""
        self._auction_decision_made = True
        
        if cs.is_bb:
            # BB with premium: bid small to set up second-price
            return ActionBid(random.randint(1, 5))
        
        # SB with premium: win the auction
        opp_bid = self.opponent_last_bid
        if opp_bid is None:
            return ActionBid(min(10, cs.my_chips))
        if opp_bid == 0:
            return ActionBid(min(1, cs.my_chips))
        # Win bid
        return ActionBid(min(opp_bid + 1, cs.my_chips))

    def _premium_action(self, cs: PokerState):
        """All-in on premium hands (like sob.py), but check board danger postflop."""
        street = cs.street

        if street == "pre-flop":
            # GO ALL-IN: raise to maximum
            if cs.can_act(ActionRaise):
                _, mx = cs.raise_bounds
                self._we_raised_this_hand = True  # Track that we raised
                return ActionRaise(mx)
            # If we can't raise (opponent already all-in), call
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # POSTFLOP: Check actual equity before continuing all-in
        # Premium preflop doesn't mean premium postflop!
        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)
        
        # If our equity dropped below 65%, don't continue all-in blind
        if exact_eq < 0.65:
            # Play like trash hand instead
            return self._trash_action(cs)
        
        # Still strong - continue all-in
        if cs.can_act(ActionRaise):
            _, mx = cs.raise_bounds
            self._we_raised_this_hand = True  # Track that we raised
            return ActionRaise(mx)
        if cs.can_act(ActionCall):
            return ActionCall()
        return ActionCheck()

    def _trash_action(self, cs: PokerState):
        """
        Trash hand strategy:
        - Stay within street threshold
        - Calculate exact equity in postflop
        - Go all-in if equity >= 0.982 (top 1.8%)
        - Otherwise bet conservatively within threshold
        - Fold if opponent raises beyond threshold
        """
        street = cs.street
        threshold = self._get_street_threshold(street)
        to_call = cs.cost_to_call

        # ── PREFLOP ──
        if street == "pre-flop":
            # Calculate total we'd commit this street
            total_committed = cs.my_wager + to_call
            
            if total_committed > threshold:
                # Opponent raised beyond threshold, fold
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            if to_call == 0:
                # Free to see more cards
                return ActionCheck()
            
            if to_call <= threshold - cs.my_wager:
                # Within threshold, call to see auction/flop
                if cs.can_act(ActionCall):
                    return ActionCall()
            
            # Too expensive, fold
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # ── POSTFLOP (Flop/Turn/River) ──
        # Calculate exact equity with board cards
        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)

        # Check if we have a monster hand (using dynamic threshold)
        allin_threshold = self._get_allin_threshold()
        if exact_eq >= allin_threshold:
            # GO ALL-IN!
            if cs.can_act(ActionRaise):
                _, mx = cs.raise_bounds
                self._we_raised_this_hand = True  # Track that we raised
                return ActionRaise(mx)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # Not a monster - play conservatively within threshold
        total_street_cost = cs.my_wager + to_call
        
        if to_call > 0:
            # Opponent bet/raised
            if total_street_cost > threshold:
                # Too expensive, fold
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            # CRITICAL: If opponent bets huge (>50% of remaining stack), they likely have it
            # Only call if we have strong equity (>=60%)
            pot_after_call = cs.pot + to_call
            bet_to_pot_ratio = to_call / max(cs.pot, 1)
            
            if bet_to_pot_ratio >= 2.0:  # Overbet (2x pot or more)
                # Opponent overbetting - only call with very strong hand
                if exact_eq >= 0.70:
                    if cs.can_act(ActionCall):
                        return ActionCall()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            # Normal bet - call if equity is decent (at least 45%) # old = 0.30
            if exact_eq >= 0.45:
                if cs.can_act(ActionCall):
                    return ActionCall()
            
            # Poor equity, fold even within threshold
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
        
        # to_call == 0 (we can check for free)
        # Only bet for value if we have good equity (>=60%) # old = 0.50
        if exact_eq >= 0.60:
            # Bet small (20-30% pot) but stay within threshold
            pot = cs.pot
            bet_amt = int(pot * 0.25)
            bet_amt = min(bet_amt, threshold - cs.my_wager, cs.my_chips)
            
            if bet_amt > 0 and cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                bet_amt = max(mn, min(mx, bet_amt))
                self._we_raised_this_hand = True  # Track that we raised
                return ActionRaise(bet_amt)
        
        # Check for free
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
