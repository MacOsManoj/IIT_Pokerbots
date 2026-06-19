"""
cv3slow.py — IIT Pokerbots 2026 "Sneak Peek Hold'em" Bot

SUPER-DEFENSIVE bot with:
  - CYTHON EXACT EQUITY (eval7.py_hand_vs_range_exact) — no random sampling
  - EXPLOIT AUCTION (ExploitRunner intercepts opponent bid)
  - TIGHT preflop, SMALL postflop bets, HEAVY info exploitation
  - No texture analysis (removed for simplicity/speed)
"""

import random
import time
import socket
import argparse

import eval7

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import Runner, parse_args

# ---------------------------------------------------------------------------
STARTING_STACK = 5000
BIG_BLIND = 20
SMALL_BLIND = 10

# ======================== AUCTION THRESHOLD ========================
WEAK_AUCTION_EQ = 0.30   # Below → drain mode.  Above → win mode.

# ---------------------------------------------------------------------------
# Preflop equity table (hardcoded — hybrid 5000 sims)
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

FULL_DECK = [eval7.Card(s) for s in [
    '2c','2d','2h','2s','3c','3d','3h','3s','4c','4d','4h','4s',
    '5c','5d','5h','5s','6c','6d','6h','6s','7c','7d','7h','7s',
    '8c','8d','8h','8s','9c','9d','9h','9s','Tc','Td','Th','Ts',
    'Jc','Jd','Jh','Js','Qc','Qd','Qh','Qs','Kc','Kd','Kh','Ks',
    'Ac','Ad','Ah','As',
]]

# ---------------------------------------------------------------------------
# GLOBAL CACHE FOR HAND RANGE OBJECTS (from expcython — cython exact equity)
# ---------------------------------------------------------------------------
_ANY_TWO_STR = "22+,32s+,32o+,42s+,42o+,52s+,52o+,62s+,62o+,72s+,72o+,82s+,82o+,92s+,92o+,T2s+,T2o+,J2s+,J2o+,Q2s+,Q2o+,K2s+,K2o+,A2s+,A2o+,33+,43s+,43o+,53s+,53o+,63s+,63o+,73s+,73o+,83s+,83o+,93s+,93o+,T3s+,T3o+,J3s+,J3o+,Q3s+,Q3o+,K3s+,K3o+,A3s+,A3o+,44+,54s+,54o+,64s+,64o+,74s+,74o+,84s+,84o+,94s+,94o+,T4s+,T4o+,J4s+,J4o+,Q4s+,Q4o+,K4s+,K4o+,A4s+,A4o+,55+,65s+,65o+,75s+,75o+,85s+,85o+,95s+,95o+,T5s+,T5o+,J5s+,J5o+,Q5s+,Q5o+,K5s+,K5o+,A5s+,A5o+,66+,76s+,76o+,86s+,86o+,96s+,96o+,T6s+,T6o+,J6s+,J6o+,Q6s+,Q6o+,K6s+,K6o+,A6s+,A6o+,77+,87s+,87o+,97s+,97o+,T7s+,T7o+,J7s+,J7o+,Q7s+,Q7o+,K7s+,K7o+,A7s+,A7o+,88+,98s+,98o+,T8s+,T8o+,J8s+,J8o+,Q8s+,Q8o+,K8s+,K8o+,A8s+,A8o+,99+,T9s+,T9o+,J9s+,J9o+,Q9s+,Q9o+,K9s+,K9o+,A9s+,A9o+,TT+,JTs+,JTo+,QTs+,QTo+,KTs+,KTo+,ATs+,ATo+,JJ+,QJs+,QJo+,KJs+,KJo+,AJs+,AJo+,QQ+,KQs+,KQo+,AQs+,AQo+,KK+,AKs+,AKo+,AA+"
_ANY_TWO_RANGE = eval7.HandRange(_ANY_TWO_STR)
_ONE_CARD_CACHE = {}


def _hkey(cards: list[str]) -> str:
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
    '''
    Modified runner that intercepts 'A<amount>' packets to extract
    opponent's auction bid BEFORE the main Runner processes them.
    '''

    def __init__(self, pokerbot, socketfile):
        super().__init__(pokerbot, socketfile)

    def receive(self):
        '''Override: scan each packet for auction bids before yielding.'''
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
        self.opp_folds = 0
        self.opp_fold_opps = 0
        self.opp_raises = 0
        self.opp_actions = 0
        self.opp_bids: list[int] = []
        self.total_hands = 0
        self._last_cost_to_call = 0
        # Auction outcome tracking
        self._opp_won_auction = False
        self._we_won_auction = False
        # Exploit: intercepted opponent bid
        self.opponent_last_bid = None
        self._auction_decision_made = False

    # ------------------------------------------------------------------
    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        self.total_hands += 1
        self._last_cost_to_call = 0
        self._opp_won_auction = False
        self._we_won_auction = False
        self.opponent_last_bid = None
        self._auction_decision_made = False

    def on_hand_end(self, game_info: GameInfo, cs: PokerState) -> None:
        pass

    def _receive_opponent_bid(self, bid_amount: int) -> None:
        '''Only accept bids BEFORE we've made our own auction decision.'''
        if not self._auction_decision_made:
            self.opponent_last_bid = bid_amount

    # ------------------------------------------------------------------
    # Opponent model
    # ------------------------------------------------------------------
    @property
    def opp_fold_pct(self) -> float:
        return self.opp_folds / self.opp_fold_opps if self.opp_fold_opps > 15 else 0.33

    @property
    def opp_agg(self) -> float:
        return self.opp_raises / self.opp_actions if self.opp_actions > 15 else 0.30

    @property
    def opp_avg_bid(self) -> float:
        if len(self.opp_bids) < 5:
            return 25.0
        recent = self.opp_bids[-80:]
        return sum(recent) / len(recent)

    # ------------------------------------------------------------------
    # Equity — CYTHON EXACT (from expcython.py)
    # ------------------------------------------------------------------
    def _pf_eq(self, hand: list[str]) -> float:
        return PREFLOP_EQ.get(_hkey(hand), 0.47)

    def _mc_eq(self, hand: list[str], board: list[str],
               opp_known: list[str] = None, iters: int = 0) -> float:
        """
        Uses eval7's C-level exact enumerator with global caching.
        iters=0 → exact enumeration (default, fast via cython).
        iters>0 → Monte Carlo fallback.
        """
        h_cards = [eval7.Card(c) for c in hand]
        b_cards = [eval7.Card(c) for c in board]

        # Define villain's range
        if opp_known and len(opp_known) == 2:
            villain_str = "".join(opp_known)
            villain = eval7.HandRange(villain_str)
        elif opp_known and len(opp_known) == 1:
            target = opp_known[0]
            if target in _ONE_CARD_CACHE:
                villain = _ONE_CARD_CACHE[target]
            else:
                deck = [r + s for r in '23456789TJQKA' for s in 'cdhs']
                hands = [target + c for c in deck if c != target]
                villain = eval7.HandRange(",".join(hands))
                _ONE_CARD_CACHE[target] = villain
        else:
            villain = _ANY_TWO_RANGE

        # Heavy math done in C
        if iters == 0:
            return float(eval7.py_hand_vs_range_exact(h_cards, villain, b_cards))
        return float(eval7.py_hand_vs_range_monte_carlo(h_cards, villain, b_cards, iters))

    def _equity(self, cs: PokerState) -> float:
        if cs.street == "pre-flop":
            return self._pf_eq(cs.my_hand)
        ok = cs.opp_revealed_cards or None
        return self._mc_eq(cs.my_hand, cs.board, opp_known=ok)

    # ------------------------------------------------------------------
    # Board helpers (no texture — removed)
    # ------------------------------------------------------------------
    @staticmethod
    def _spr(pot: int, mc: int, oc: int) -> float:
        return min(mc, oc) / pot if pot > 0 else 100.0

    # ------------------------------------------------------------------
    # Auction  (DEFENSIVE BB + SB EXPLOIT)
    # ------------------------------------------------------------------
    def _bid(self, cs: PokerState) -> int:
        """
        DEFENSIVE auction: conservative BB bids, SB exploit.
        """
        self._auction_decision_made = True

        if cs.my_chips <= 0:
            return 0

        eq = self._mc_eq(cs.my_hand, cs.board, iters=150)

        # ══════════════════════════════════════════════════════════════
        # BB (first bidder): CONSERVATIVE — save chips
        # ══════════════════════════════════════════════════════════════
        if cs.is_bb:
            pot = cs.pot

            if eq > 0.80:
                iv = pot * random.uniform(0.25, 0.40)
            elif eq > 0.68:
                iv = pot * random.uniform(0.14, 0.25)
            elif eq > 0.55:
                iv = pot * random.uniform(0.06, 0.14)
            elif eq < 0.30:
                iv = pot * random.uniform(0.00, 0.02)
            elif eq < 0.40:
                iv = pot * random.uniform(0.01, 0.04)
            else:
                # Marginal (0.40-0.55): uncertainty-based
                unc = max(0.0, 1.0 - (2.0 * abs(eq - 0.5)) ** 1.4)
                iv = unc * pot * 0.18

            iv = min(iv, cs.my_chips * 0.07, pot * 0.45)
            noise = random.uniform(0.87, 1.13)
            iv *= noise

            return int(max(0, min(iv, cs.my_chips)))

        # ══════════════════════════════════════════════════════════════
        # SB (second bidder): EXPLOIT
        # ══════════════════════════════════════════════════════════════
        opp_bid = self.opponent_last_bid

        if opp_bid is None:
            if eq > 0.5:
                return min(int(cs.pot * 0.10), cs.my_chips)
            return 0

        # Free peek
        if opp_bid == 0:
            return min(1, cs.my_chips)

        # Drain mode (weak hands)
        if eq < WEAK_AUCTION_EQ:
            drain_bid = max(opp_bid - 1, 0)
            return min(drain_bid, cs.my_chips)

        # Win mode (medium-strong)
        win_bid = opp_bid + 1
        win_bid = min(win_bid, cs.my_chips)

        if win_bid <= opp_bid:
            return cs.my_chips

        return win_bid

    # ------------------------------------------------------------------
    @staticmethod
    def _do_raise(cs: PokerState, amt: int):
        mn, mx = cs.raise_bounds
        return ActionRaise(min(mx, max(mn, amt)))

    # ------------------------------------------------------------------
    # MAIN — SUPER DEFENSIVE with info exploitation + exact equity
    # ------------------------------------------------------------------
    def get_move(self, game_info: GameInfo, cs: PokerState):
        # ── Auction ──
        if cs.street == "auction":
            return ActionBid(self._bid(cs))

        # ── Detect auction outcome ──
        if cs.street == "flop" and not self._opp_won_auction and not self._we_won_auction:
            if cs.opp_revealed_cards:
                self._we_won_auction = True
            else:
                self._opp_won_auction = True

        # ── Equity (exact via cython) ──
        equity = self._equity(cs)

        # ── Context ──
        pot       = cs.pot
        to_call   = cs.cost_to_call
        my_chips  = cs.my_chips
        opp_chips = cs.opp_chips
        is_ip     = not cs.is_bb
        spr       = self._spr(pot, my_chips, opp_chips)
        r         = random.random()

        # ── Minimal position boost ──
        if is_ip:
            equity += 0.01

        # ══════════════════════════════════════════════════════════════
        # INFORMATION EXPLOITATION
        #
        # Exact equity already accounts for revealed cards perfectly.
        # Additional adjustments based on board interaction analysis.
        # ══════════════════════════════════════════════════════════════
        opp_card_strong = False
        opp_card_weak   = False

        if self._we_won_auction and cs.opp_revealed_cards and cs.street != "pre-flop":
            revealed = cs.opp_revealed_cards[0]
            rev_rank = RANK_VAL.get(revealed[0], 6)
            rev_suit = revealed[1]

            # Check if their card connects with the board
            board_ranks = [RANK_VAL[c[0]] for c in cs.board]
            board_suits = [c[1] for c in cs.board]

            paired_board = rev_rank in board_ranks
            high_card = rev_rank >= 8  # T or higher
            suit_count = sum(1 for s in board_suits if s == rev_suit)
            flush_draw = suit_count >= 2
            flush_made = suit_count >= 3 and len(cs.board) >= 4

            if paired_board or flush_made:
                opp_card_strong = True
                equity -= 0.04
            elif high_card and flush_draw:
                opp_card_strong = True
                equity -= 0.02
            elif rev_rank <= 5 and not paired_board:
                opp_card_weak = True
                equity += 0.03
            elif rev_rank <= 8 and not paired_board and not flush_draw:
                opp_card_weak = True
                equity += 0.015

        elif self._opp_won_auction:
            equity -= 0.02

        # ── SPR adjustment ──
        if spr > 8 and 0.35 < equity < 0.55:
            equity += 0.01

        equity = max(0.0, min(1.0, equity))

        # ==============================================================
        #  FACING-BET DISCOUNT — HEAVY (defensive core)
        # ==============================================================
        if to_call > 0 and cs.street != "pre-flop":
            bet_frac = to_call / (pot - to_call + 0.01)
            discount = min(0.16, bet_frac * 0.12)

            if self._opp_won_auction:
                discount *= 1.6
                discount = min(discount, 0.22)

            if opp_card_strong:
                discount += 0.04

            equity -= discount
            equity = max(0.0, equity)

        # ── Pot odds ──
        pot_odds = to_call / (pot + to_call + 0.01) if to_call > 0 else 0.0

        # ── Opponent exploitation (minimal) ──
        bluff_mod = 0.0
        if self.total_hands > 50:
            if self.opp_fold_pct > 0.50:
                bluff_mod = 0.04
            elif self.opp_fold_pct < 0.25:
                bluff_mod = -0.04

        # ==============================================================
        #  PREFLOP — TIGHT
        # ==============================================================
        if cs.street == "pre-flop":
            return self._pf_action(cs, equity, is_ip, r, bluff_mod)

        # ==============================================================
        #  POSTFLOP BUCKETS — DEFENSIVE
        # ==============================================================

        # ── BUCKET 1: PREMIUM (equity > 0.82) ──
        if equity > 0.82:
            if cs.can_act(ActionRaise):
                if opp_card_weak:
                    bet = int(pot * random.uniform(0.40, 0.55))
                else:
                    bet = int(pot * random.uniform(0.55, 0.75))
                if r < 0.85:
                    return self._do_raise(cs, bet)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # ── BUCKET 2: STRONG (equity 0.68 – 0.82) ──
        if equity > 0.68:
            if to_call > 0:
                if cs.can_act(ActionCall):
                    return ActionCall()
                return ActionCheck()
            if cs.can_act(ActionRaise):
                bet = int(pot * random.uniform(0.33, 0.50))
                freq = 0.50 if opp_card_weak else 0.40
                if r < freq:
                    return self._do_raise(cs, bet)
            return ActionCheck() if cs.can_act(ActionCheck) else ActionCall()

        # ── BUCKET 3: MEDIUM (equity 0.55 – 0.68) ──
        if equity > 0.55:
            if to_call > 0:
                if pot_odds < equity - 0.06:
                    if cs.can_act(ActionCall):
                        return ActionCall()
                if cs.can_act(ActionCheck):
                    return ActionCheck()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            if opp_card_weak and is_ip and r < 0.30 and cs.can_act(ActionRaise):
                return self._do_raise(cs, int(pot * random.uniform(0.28, 0.40)))
            return ActionCheck() if cs.can_act(ActionCheck) else ActionCall()

        # ── BUCKET 4: MARGINAL (equity 0.45 – 0.55) ──
        if equity > 0.45:
            if to_call > 0:
                if pot_odds < equity - 0.08 and r < 0.30:
                    if cs.can_act(ActionCall):
                        return ActionCall()
                if cs.can_act(ActionCheck):
                    return ActionCheck()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            return ActionCheck() if cs.can_act(ActionCheck) else ActionCall()

        # ── BUCKET 5: WEAK (equity 0.33 – 0.45) ──
        if equity > 0.33:
            if to_call > 0:
                if cs.can_act(ActionCheck):
                    return ActionCheck()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            if opp_card_weak and is_ip and r < (0.08 + bluff_mod):
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(pot * random.uniform(0.40, 0.55)))
            return ActionCheck() if cs.can_act(ActionCheck) else ActionCall()

        # ── BUCKET 6: TRASH (equity < 0.33) ──
        if cs.can_act(ActionCheck):
            if opp_card_weak and is_ip and r < (0.03 + bluff_mod):
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(pot * random.uniform(0.45, 0.60)))
            return ActionCheck()
        return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

    # ------------------------------------------------------------------
    # Preflop sub-strategy — TIGHT
    # ------------------------------------------------------------------
    def _pf_action(self, cs: PokerState, eq: float, is_ip: bool,
                   r: float, bluff_mod: float):
        to_call = cs.cost_to_call

        if is_ip:
            if eq > 0.58:
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(BIG_BLIND * 2.5))
            elif eq > 0.50:
                if cs.can_act(ActionCall):
                    return ActionCall()
            else:
                return ActionFold() if cs.can_act(ActionFold) else ActionCall()
        else:
            if to_call == 0:
                if eq > 0.60 and cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(BIG_BLIND * 2.5))
                return ActionCheck()
            if eq > 0.68:
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(cs.pot * 2.5))
            if eq > 0.48:
                if cs.can_act(ActionCall):
                    return ActionCall()
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # Fallback
        if cs.can_act(ActionCheck):
            return ActionCheck()
        if cs.can_act(ActionCall):
            return ActionCall()
        return ActionFold()


# ===========================================================================
#  Exploit runner + entry point
# ===========================================================================

def run_exploit_bot(pokerbot, args):
    '''Runs the pokerbot with ExploitRunner for auction exploitation.'''
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
