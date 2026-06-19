"""
temp_drain_simplified.py — Simplified Adaptive Bot (IIT Pokerbots 2026)

Stripped down to essentials:
  1. Premium hands (>= 0.76): All-in preflop, aggressive postflop
  2. Trash hands: Dynamic equity floors based on hand quality
  3. Simple auction drain strategy
  4. Lock-in mode at 20k
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
ALLIN_THRESHOLD = 0.76    # Premium if equity >= this
BANKROLL_TARGET = 20000   # Lock-in mode threshold

# Static chip thresholds (no dynamic adjustment needed)
PREFLOP_THRESHOLD = 80    # Max chips to commit preflop with trash
POSTFLOP_THRESHOLD = 120  # Max chips to commit postflop with trash

# Auction settings
AUCTION_MAX_BID = 100     # If opp_bid > this, drain them

# ---------------------------------------------------------------------------
# Dynamic equity floors (hand quality based)
# The worse your starting hand, the MORE postflop equity you need
# ---------------------------------------------------------------------------
HAND_QUALITY_MIN_EQ = 0.32    # 32o equity (worst)
HAND_QUALITY_MAX_EQ = 0.76    # Premium threshold

# Call floor: MIN for near-premium, MAX for worst trash
CALL_FLOOR_MIN = 0.45
CALL_FLOOR_MAX = 0.65

# Value bet floor
VALUE_BET_FLOOR_MIN = 0.58
VALUE_BET_FLOOR_MAX = 0.75

# Overbet floor (2x+ pot)
OVERBET_FLOOR_MIN = 0.68
OVERBET_FLOOR_MAX = 0.85

# All-in floor
ALLIN_FLOOR_MIN = 0.85
ALLIN_FLOOR_MAX = 0.95

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
    """Intercepts 'A<amount>' packets to extract opponent's auction bid."""

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
        
        # Hand state
        self._is_premium = False
        self._hand_quality = 0.5
        
        # Auction state
        self.opponent_last_bid = None
        self._auction_decision_made = False

    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        self.bankroll = game_info.bankroll
        self.round_num = game_info.round_num
        
        # Calculate hand quality once per hand
        pf_eq = self._pf_eq(cs.my_hand)
        self._is_premium = pf_eq >= ALLIN_THRESHOLD
        self._hand_quality = self._calc_hand_quality(pf_eq)
        
        # Reset auction state
        self.opponent_last_bid = None
        self._auction_decision_made = False

    def _calc_hand_quality(self, pf_eq: float) -> float:
        """Normalize preflop equity to 0.0-1.0 scale."""
        quality = (pf_eq - HAND_QUALITY_MIN_EQ) / (HAND_QUALITY_MAX_EQ - HAND_QUALITY_MIN_EQ)
        return max(0.0, min(1.0, quality))

    def _get_dynamic_floor(self, min_floor: float, max_floor: float) -> float:
        """
        Dynamic equity floor based on hand quality.
        Worse hand = higher floor required.
        """
        return max_floor - self._hand_quality * (max_floor - min_floor)

    def _receive_opponent_bid(self, bid_amount: int) -> None:
        """Called by ExploitRunner when opponent's bid is intercepted."""
        if not self._auction_decision_made:
            self.opponent_last_bid = bid_amount

    def _pf_eq(self, hand: list[str]) -> float:
        return PREFLOP_EQ.get(_hkey(hand), 0.47)

    def _exact_equity(self, hand: list[str], board: list[str], opp_known: list[str] = None) -> float:
        """Calculate exact equity using eval7."""
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

    # ------------------------------------------------------------------
    # Simple auction logic
    # ------------------------------------------------------------------
    def _bid(self, cs: PokerState) -> int:
        """Simple drain strategy for auction."""
        if cs.my_chips <= 0:
            return 0

        self._auction_decision_made = True

        # BB: bid modestly
        if cs.is_bb:
            return min(random.randint(50, 80), cs.my_chips)

        opp_bid = self.opponent_last_bid
        if opp_bid is None:
            return 0
        if opp_bid == 0:
            return min(1, cs.my_chips)

        # Drain if opponent bids high, otherwise outbid by 1
        if opp_bid >= AUCTION_MAX_BID:
            return min(max(opp_bid - 1, 0), cs.my_chips)
        return min(opp_bid + 1, cs.my_chips)

    # ------------------------------------------------------------------
    def get_move(self, game_info: GameInfo, cs: PokerState):
        # ==============================================================
        # LOCK-IN MODE
        # ==============================================================
        if self.bankroll >= BANKROLL_TARGET:
            if cs.street == "auction":
                return ActionBid(1)
            if cs.can_act(ActionCheck):
                return ActionCheck()
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # ==============================================================
        # AUCTION
        # ==============================================================
        if cs.street == "auction":
            if self._is_premium:
                # Premium: bid to win cheaply
                self._auction_decision_made = True
                opp_bid = self.opponent_last_bid
                if cs.is_bb:
                    return ActionBid(random.randint(1, 5))
                if opp_bid is None:
                    return ActionBid(min(10, cs.my_chips))
                if opp_bid == 0:
                    return ActionBid(min(1, cs.my_chips))
                return ActionBid(min(opp_bid + 1, cs.my_chips))
            else:
                return ActionBid(self._bid(cs))

        # ==============================================================
        # PREMIUM HANDS
        # ==============================================================
        if self._is_premium:
            return self._premium_action(cs)

        # ==============================================================
        # TRASH HANDS
        # ==============================================================
        return self._trash_action(cs)

    def _premium_action(self, cs: PokerState):
        """All-in on premium hands, but check board danger postflop."""
        if cs.street == "pre-flop":
            if cs.can_act(ActionRaise):
                _, mx = cs.raise_bounds
                return ActionRaise(mx)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # Postflop: check equity
        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)
        
        # If equity dropped significantly, play carefully
        if exact_eq < 0.65:
            return self._trash_action(cs)
        
        # Otherwise stay aggressive
        if cs.can_act(ActionRaise):
            _, mx = cs.raise_bounds
            return ActionRaise(mx)
        if cs.can_act(ActionCall):
            return ActionCall()
        return ActionCheck()

    def _trash_action(self, cs: PokerState):
        """
        Trash hand strategy with DYNAMIC EQUITY FLOORS.
        Key insight: 32o needs 65% equity to call, not 45%.
        """
        to_call = cs.cost_to_call

        # ── PREFLOP: Static chip threshold ──
        if cs.street == "pre-flop":
            total = cs.my_wager + to_call
            if total > PREFLOP_THRESHOLD:
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            if to_call == 0:
                return ActionCheck()
            if to_call <= PREFLOP_THRESHOLD - cs.my_wager:
                return ActionCall() if cs.can_act(ActionCall) else ActionCheck()
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # ── POSTFLOP: Dynamic equity floors ──
        opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None
        exact_eq = self._exact_equity(cs.my_hand, cs.board, opp_known)
        
        # Get dynamic floors based on hand quality
        call_floor = self._get_dynamic_floor(CALL_FLOOR_MIN, CALL_FLOOR_MAX)
        value_floor = self._get_dynamic_floor(VALUE_BET_FLOOR_MIN, VALUE_BET_FLOOR_MAX)
        overbet_floor = self._get_dynamic_floor(OVERBET_FLOOR_MIN, OVERBET_FLOOR_MAX)
        allin_floor = self._get_dynamic_floor(ALLIN_FLOOR_MIN, ALLIN_FLOOR_MAX)

        # All-in if we have monster equity
        if exact_eq >= allin_floor:
            if cs.can_act(ActionRaise):
                _, mx = cs.raise_bounds
                return ActionRaise(mx)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # Facing a bet
        if to_call > 0:
            total = cs.my_wager + to_call
            
            # Chip threshold check
            if total > POSTFLOP_THRESHOLD:
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            # Overbet check (2x+ pot)
            bet_to_pot = to_call / max(cs.pot, 1)
            if bet_to_pot >= 2.0:
                if exact_eq >= overbet_floor:
                    return ActionCall() if cs.can_act(ActionCall) else ActionCheck()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            
            # Normal bet
            if exact_eq >= call_floor:
                return ActionCall() if cs.can_act(ActionCall) else ActionCheck()
            
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
        
        # Checked to us: value bet if strong enough
        if exact_eq >= value_floor:
            pot = cs.pot
            bet_amt = int(pot * 0.25)
            bet_amt = min(bet_amt, POSTFLOP_THRESHOLD - cs.my_wager, cs.my_chips)
            
            if bet_amt > 0 and cs.can_act(ActionRaise):
                mn, mx = cs.raise_bounds
                bet_amt = max(mn, min(mx, bet_amt))
                return ActionRaise(bet_amt)
        
        return ActionCheck()


# ===========================================================================
#  Entry point
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
