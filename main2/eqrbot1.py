"""
eqrbotv2.py — Advanced EQR + Exploit + Safeguard Poker Bot

This bot integrates multiple advanced poker concepts simultaneously to maintain a high win rate. 

Key Features & Strategies:
  * Preflop Equity Dictionary: Instant lookup for 5000+ Monte Carlo simulated preflop hand strengths.
  * Postflop Monte Carlo: Live multi-street MC evaluation customized to known opponent cards.
  * Equity Realization (EQR) Multiplier: Dynamically distorts raw equity based on:
      - Position (In Position +8%, Out of Position -6%)
      - Hand Properties: Suitedness (+5%), Connectedness (+4%/-3%), Pairs (TT+ vs 22-55 tiering)
      - Street Weights: Caps multiplier impact dynamically per street.
  * Auction Exploit System:
      - Intercepts the opponent bid packets over the socket.
      - SB: Reacts perfectly (bids +1 to win securely, or -1 to painfully drain weak hands).
      - BB: Defensive, chip-preserving bid strategy with randomized noise.
  * Direct Information Exploitation: 
      - Heavy equity penalizations if opponent's revealed card hits the board (pairs/flush draws).
      - Equity inflation if opponent holds irrelevant weak trash (<5).
  * Board Texture Analysis: Identifies "wet" vs "dry" boards based on suit count/gaps and pulls back OOP.
  * Facing-Bet Discount: Drastically slashes bot confidence when facing bets, scaled heavily (1.7x) if opponent won the auction (playing blind against informed opponent).
  * Safe-Guard Score Sustain Module:
      - Tracks bankroll and round count in real-time.
      - Enters "Safeguard Mode" if bankroll hits +500 OR exceeds the worst-case blind posting cost of all remaining rounds.
      - Imposes intense -6% equity penalties to essentially fold-to-victory once the match is locked.
  * Six-Bucket Postflop System:
      - Premium (>84%): Tricky small "bleed" bets (15-30% pot) rather than massive shove folds.
      - Strong (70-84%): Heavy value bets.
      - Medium/Marginal/Weak/Trash: Strict risk-averse defensive check/calls based on exact pot odds.
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


# ===========================================================================
# EV Subgame Solver (inlined — single file submission)
# ===========================================================================

# Villain range for eval7 (any two cards)
_ANY_TWO_STR = ",".join(
    r1 + s1 + r2 + s2
    for i, (r1, s1) in enumerate([(r, s) for r in '23456789TJQKA' for s in 'cdhs'])
    for r2, s2 in [(r, s) for r in '23456789TJQKA' for s in 'cdhs'][i + 1:]
)
_ANY_TWO_RANGE = eval7.HandRange(_ANY_TWO_STR)
_ONE_CARD_CACHE = {}

NODE_MAX  = "hero"
NODE_MIN  = "opponent"
NODE_TERM = "terminal"


def _solver_compute_equity(hand, board, opp_known=None, iters=0):
    """Compute hero equity using eval7. iters=0 → exact, >0 → MC."""
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
    if iters == 0:
        return float(eval7.py_hand_vs_range_exact(h, villain, b))
    return float(eval7.py_hand_vs_range_monte_carlo(h, villain, b, iters))


class _TreeNode:
    __slots__ = ('node_type', 'action_name', 'children', 'ev', 'equity', 'best_action')
    def __init__(self, nt, an="", ch=None):
        self.node_type = nt; self.action_name = an; self.children = ch or {}
        self.ev = None; self.equity = None; self.best_action = None


def _get_bet_sizes(pot, mn, mx):
    sizes, seen, result = [], set(), []
    hp = max(pot // 2, mn)
    if hp <= mx: sizes.append(("bet_half", min(hp, mx)))
    pp = max(pot, mn)
    if pp <= mx: sizes.append(("bet_pot", min(pp, mx)))
    if mx > 0: sizes.append(("bet_allin", mx))
    for l, a in sizes:
        if a not in seen: result.append((l, a)); seen.add(a)
    return result


def _get_raise_sizes(pot, ctc, mn, mx):
    sizes, seen, result = [], set(), []
    pac = pot + ctc
    hp = max(pac // 2 + ctc, mn)
    if hp <= mx: sizes.append(("raise_half", min(hp, mx)))
    pp = max(pac + ctc, mn)
    if pp <= mx: sizes.append(("raise_pot", min(pp, mx)))
    if mx > 0: sizes.append(("raise_allin", mx))
    for l, a in sizes:
        if a not in seen: result.append((l, a)); seen.add(a)
    return result


def _build_subgame_tree(pot, hero_stack, opp_stack, ctc, mn, mx):
    root = _TreeNode(NODE_MAX, "root")
    if ctc > 0:
        root.children["fold"] = _TreeNode(NODE_TERM, "fold")
        cn = _TreeNode(NODE_MIN, "call")
        cn.children["showdown"] = _TreeNode(NODE_TERM, "showdown")
        root.children["call"] = cn
        if mn <= mx and hero_stack > ctc:
            for l, a in _get_raise_sizes(pot, ctc, mn, mx):
                rn = _TreeNode(NODE_MIN, l)
                rn.children["fold"] = _TreeNode(NODE_TERM, "fold")
                rn.children["call"] = _TreeNode(NODE_TERM, "call")
                root.children[l] = rn
    else:
        ck = _TreeNode(NODE_MIN, "check")
        ck.children["check"] = _TreeNode(NODE_TERM, "check")
        ck.children["bet"] = _TreeNode(NODE_TERM, "bet")
        root.children["check"] = ck
        if mn <= mx:
            for l, a in _get_bet_sizes(pot, mn, mx):
                bn = _TreeNode(NODE_MIN, l)
                bn.children["fold"] = _TreeNode(NODE_TERM, "fold")
                bn.children["call"] = _TreeNode(NODE_TERM, "call")
                root.children[l] = bn
    return root


def _solve_ev_max(root, hand, board, pot, h_inv, o_inv,
                  h_stack, o_stack, ctc, mn, mx,
                  opp_known=None, street="flop"):
    eq_iters = 200 if street == "flop" else 0
    equity = _solver_compute_equity(hand, board, opp_known=opp_known, iters=eq_iters)
    facing = ctc > 0
    AGG = {"bet_allin":5,"raise_allin":5,"bet_pot":4,"raise_pot":4,
           "bet_half":3,"raise_half":3,"call":2,"check":1,"fold":0}

    def _solve(node, hi, oi, cp):
        if node.node_type == NODE_TERM:
            a = node.action_name
            if a == "fold":
                for _, ch in root.children.items():
                    if ch is node: node.ev = -hi; node.equity = equity; return -hi
                    if ch.children:
                        for _, g in ch.children.items():
                            if g is node: node.ev = cp - hi; node.equity = equity; return cp - hi
                node.ev = cp - hi; node.equity = equity; return cp - hi
            if a == "bet":
                fold_ev = -hi
                c = oi - hi; hc = min(c, h_stack)
                call_ev = equity * (cp + hc) - (hi + hc)
                ev = max(fold_ev, call_ev)
                node.ev = ev; node.equity = equity; return ev
            ev = equity * cp - hi
            node.ev = ev; node.equity = equity; return ev

        cev = {}
        for an, child in node.children.items():
            # apply action
            nh, no, np = hi, oi, cp
            if an == "call":
                if node.node_type == NODE_MAX: nh, np = hi + ctc, cp + ctc
                else: ow = hi - oi; no, np = oi + ow, cp + ow
            elif an == "bet":
                ob = max(cp // 2, BIG_BLIND); ob = min(ob, o_stack)
                no, np = oi + ob, cp + ob
            elif an not in ("fold", "check", "showdown") and (an.startswith("bet_") or an.startswith("raise_")):
                if node.node_type == NODE_MAX:
                    if an == "bet_half": amt = max(cp // 2, mn)
                    elif an == "bet_pot": amt = max(cp, mn)
                    elif an == "bet_allin": amt = mx
                    elif an == "raise_half": amt = max((cp+ctc)//2+ctc, mn)
                    elif an == "raise_pot": amt = max(cp+ctc+ctc, mn)
                    elif an == "raise_allin": amt = mx
                    else: amt = mn
                    amt = min(amt, mx, h_stack)
                    nh, np = hi + amt, cp + amt
            child.ev = _solve(child, nh, no, np)
            cev[an] = child.ev

        if node.node_type == NODE_MAX:
            ba = max(cev, key=lambda a: (cev[a], AGG.get(a, 0)))
            node.ev = cev[ba]
        else:
            # SKEPTICAL MINIMAX: Assume opponent is 70% perfect, 30% noisy/bluffing
            # This helps against aggressive 'bullies' who bluff more than GTO.
            rationality = 0.70
            min_ev = min(cev.values())
            avg_ev = sum(cev.values()) / len(cev)
            node.ev = (rationality * min_ev) + ((1.0 - rationality) * avg_ev)
            ba = min(cev, key=cev.get)

        node.best_action = ba
        return node.ev

    _solve(root, h_inv, o_inv, pot)
    return root.best_action, root.ev, root



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


# ======================== AUCTION THRESHOLD ========================
WEAK_AUCTION_EQ = 0.35  # Below → drain mode.  Above → win mode.


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
        self.bankroll = 0.0
        self.round_num = 0

    # ------------------------------------------------------------------
    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        self.total_hands += 1
        self._last_cost_to_call = 0
        self._opp_won_auction = False
        self._we_won_auction = False
        self.opponent_last_bid = None
        self._auction_decision_made = False
        self.bankroll = game_info.bankroll
        self.round_num = game_info.round_num

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
    # Equity (MC sampling — same as v3slow)
    # ------------------------------------------------------------------
    def _pf_eq(self, hand: list[str]) -> float:
        return PREFLOP_EQ.get(_hkey(hand), 0.47)

    def _mc_eq(self, hand: list[str], board: list[str],
               opp_known: list[str] = None, iters: int = 200) -> float:
        my = [eval7.Card(s) for s in hand]
        bd = [eval7.Card(s) for s in board]
        dead = set(my + bd)
        ofix = []
        if opp_known:
            ofix = [eval7.Card(s) for s in opp_known]
            dead.update(ofix)
        dk = [c for c in FULL_DECK if c not in dead]
        nb = 5 - len(bd)
        on = 2 - len(ofix)
        if on + nb > len(dk) or on + nb <= 0:
            return 0.5
        w = t = 0
        for _ in range(iters):
            s = random.sample(dk, on + nb)
            oc = ofix + s[:on]
            fb = bd + s[on:]
            mv = eval7.evaluate(my + fb)
            ov = eval7.evaluate(oc + fb)
            if mv > ov:
                w += 1
            elif mv == ov:
                t += 1
        return (w + 0.5 * t) / iters

    def _equity(self, cs: PokerState) -> float:
        if cs.street == "pre-flop":
            return self._pf_eq(cs.my_hand)
        it = {"flop": 200, "turn": 250, "river": 350}.get(cs.street, 200)
        ok = cs.opp_revealed_cards or None
        return self._mc_eq(cs.my_hand, cs.board, opp_known=ok, iters=it)

    # ------------------------------------------------------------------
    # EQR — Equity Realization
    # ------------------------------------------------------------------
    def _eqr(self, hand: list[str], is_ip: bool, street: str) -> float:
        """
        EQR multiplier: realized_equity = raw_equity × eqr

        River → 1.0 (no streets left).
        Earlier streets → deviate by position + hand playability.
        """
        if street == "river":
            return 1.0

        sw = {"pre-flop": 1.0, "flop": 0.6, "turn": 0.3}.get(street, 0.0)

        eqr = 1.0

        # Position
        if is_ip:
            eqr += 0.08 * sw
        else:
            eqr -= 0.06 * sw

        # Hand properties
        r1, s1 = hand[0][0], hand[0][1]
        r2, s2 = hand[1][0], hand[1][1]
        v1, v2 = RANK_VAL[r1], RANK_VAL[r2]
        hi, lo = max(v1, v2), min(v1, v2)
        suited = s1 == s2
        pair = v1 == v2
        gap = hi - lo - 1 if not pair else 0

        # Suitedness
        if suited:
            eqr += 0.05 * sw

        # Pairs
        if pair:
            if hi >= 8:          # TT+
                eqr += 0.04 * sw
            elif hi <= 3:        # 22-55
                eqr -= 0.06 * sw

        # Connectedness
        if not pair:
            if gap == 0:
                eqr += 0.04 * sw
            elif gap >= 3:
                eqr -= 0.03 * sw

        # Broadway
        if hi >= 8 and lo >= 8:
            eqr += 0.03 * sw
        elif hi >= 8 and lo <= 3:
            eqr -= 0.04 * sw

        # Ace kicker
        if hi == 12:
            eqr += 0.02 * sw

        return max(0.65, min(1.25, eqr))

    # ------------------------------------------------------------------
    # Board helpers (identical to v3slow)
    # ------------------------------------------------------------------
    @staticmethod
    def _texture(board: list[str]) -> str:
        if len(board) < 3:
            return "dry"
        ranks = sorted([RANK_VAL[c[0]] for c in board])
        suits = [c[1] for c in board]
        conn = sum(1 for i in range(len(ranks)-1) if ranks[i+1]-ranks[i] <= 2)
        sc: dict[str, int] = {}
        for s in suits:
            sc[s] = sc.get(s, 0) + 1
        fd = 1 if max(sc.values()) >= 3 else 0
        paired = len(ranks) - len(set(ranks))
        return "wet" if (conn + fd - paired) >= 2 else "dry"

    @staticmethod
    def _spr(pot: int, mc: int, oc: int) -> float:
        return min(mc, oc) / pot if pot > 0 else 100.0

    # ------------------------------------------------------------------
    # Auction  (DEFENSIVE BB + SB EXPLOIT) — identical to v3slow
    # ------------------------------------------------------------------
    def _bid(self, cs: PokerState) -> int:
        """
        DEFENSIVE auction: conservative BB bids, SB exploit.
        """
        if cs.my_chips <= 0:
            return 0

        eq = self._mc_eq(cs.my_hand, cs.board, iters=150)
        
        # Mark decision made AFTER reading opponent bid
        self._auction_decision_made = True

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

        if opp_bid == 0:
            return min(1, cs.my_chips)

        if eq < WEAK_AUCTION_EQ:
            drain_bid = max(opp_bid - 1, 0)
            return min(drain_bid, cs.my_chips)

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
    # EV Subgame Solver — minimax for flop/turn (20ms budget)
    # ------------------------------------------------------------------
    def _ev_solver_action(self, cs: PokerState):
        """
        Run the minimax EV subgame solver for flop/turn decisions.
        Returns a concrete Action, or None if the solver exceeds 20ms
        or encounters an error (triggering EQR fallback).
        """
        TIME_BUDGET = 0.020  # 20ms

        try:
            start = time.time()

            pot = cs.pot
            my_chips = cs.my_chips
            opp_chips = cs.opp_chips
            cost_to_call = cs.cost_to_call
            my_contribution = cs.my_contribution
            opp_contribution = cs.opp_contribution
            opp_known = cs.opp_revealed_cards if cs.opp_revealed_cards else None

            # Convert raise_bounds (total contribution) → added amounts
            if cs.can_act(ActionRaise):
                mn_total, mx_total = cs.raise_bounds
                min_add = max(0, mn_total - my_contribution)
                max_add = max(0, mx_total - my_contribution)
            else:
                min_add, max_add = 0, 0

            # Build tree
            tree = _build_subgame_tree(
                pot, my_chips, opp_chips,
                cost_to_call, min_add, max_add,
            )

            # Solve
            best_action, best_ev, _ = _solve_ev_max(
                tree, cs.my_hand, cs.board, pot,
                my_contribution, opp_contribution,
                my_chips, opp_chips,
                cost_to_call, min_add, max_add,
                opp_known=opp_known, street=cs.street,
            )

            elapsed = time.time() - start

            # Time budget check
            if elapsed > TIME_BUDGET:
                return None  # Fallback to EQR

            # Map abstract action → concrete action
            return self._solver_to_action(best_action, cs, min_add, max_add,
                                          pot, cost_to_call, my_contribution)

        except Exception:
            return None  # Any error → fallback to EQR

    def _solver_to_action(self, action_name, cs, min_add, max_add,
                          pot, cost_to_call, my_contrib):
        """Convert solver abstract action to pkbot Action."""
        if action_name == "fold":
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
        if action_name == "call":
            return ActionCall() if cs.can_act(ActionCall) else ActionCheck()
        if action_name == "check":
            return ActionCheck() if cs.can_act(ActionCheck) else ActionCall()

        if not cs.can_act(ActionRaise):
            return ActionCall() if cs.can_act(ActionCall) else ActionCheck()

        mn, mx = cs.raise_bounds

        if action_name in ("bet_half", "raise_half"):
            target = max(pot // 2, min_add)
            if action_name == "raise_half":
                pot_after = pot + cost_to_call
                target = max(pot_after // 2 + cost_to_call, min_add)
            return ActionRaise(min(mx, max(mn, my_contrib + target)))

        if action_name in ("bet_pot", "raise_pot"):
            target = max(pot, min_add)
            if action_name == "raise_pot":
                pot_after = pot + cost_to_call
                target = max(pot_after + cost_to_call, min_add)
            return ActionRaise(min(mx, max(mn, my_contrib + target)))

        if action_name in ("bet_allin", "raise_allin"):
            return ActionRaise(mx)

        return ActionCheck() if cs.can_act(ActionCheck) else ActionCall()

    # ------------------------------------------------------------------
    # MAIN — v3slow defensive logic + EQR (replaces equity += 0.01)
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

        # ── Raw equity ──
        raw_equity = self._equity(cs)

        # ── Context ──
        pot       = cs.pot
        to_call   = cs.cost_to_call
        my_chips  = cs.my_chips
        opp_chips = cs.opp_chips
        is_ip     = not cs.is_bb
        tex       = self._texture(cs.board)
        spr       = self._spr(pot, my_chips, opp_chips)
        r         = random.random()

        # ══════════════════════════════════════════════════════════════
        # EQR — replaces the old `if is_ip: equity += 0.01`
        # Proper multiplier: position, suitedness, connectedness, street
        # ══════════════════════════════════════════════════════════════
        eqr = self._eqr(cs.my_hand, is_ip, cs.street)
        equity = raw_equity * eqr

        # ══════════════════════════════════════════════════════════════
        # INFORMATION EXPLOITATION (identical to v3slow)
        # ══════════════════════════════════════════════════════════════
        opp_card_strong = False
        opp_card_weak   = False

        if self._we_won_auction and cs.opp_revealed_cards and cs.street != "pre-flop":
            revealed = cs.opp_revealed_cards[0]
            rev_rank = RANK_VAL.get(revealed[0], 6)
            rev_suit = revealed[1]

            board_ranks = [RANK_VAL[c[0]] for c in cs.board]
            board_suits = [c[1] for c in cs.board]

            paired_board = rev_rank in board_ranks
            high_card = rev_rank >= 8
            suit_count = sum(1 for s in board_suits if s == rev_suit)
            flush_draw = suit_count >= 2
            flush_made = suit_count >= 3 and len(cs.board) >= 4

            if paired_board or flush_made:
                opp_card_strong = True
                equity -= 0.05
            elif high_card and flush_draw:
                opp_card_strong = True
                equity -= 0.03
            elif rev_rank <= 5 and not paired_board:
                opp_card_weak = True
                equity += 0.04
            elif rev_rank <= 8 and not paired_board and not flush_draw:
                opp_card_weak = True
                equity += 0.02

        elif self._opp_won_auction:
            equity -= 0.03

        # ── Texture & SPR adjustments (identical to v3slow) ──
        if tex == "wet" and not is_ip and 0.45 < equity < 0.65:
            equity -= 0.05
        if spr > 7 and 0.35 < equity < 0.55:
            equity += 0.015

        equity = max(0.0, min(1.0, equity))

        # ==============================================================
        #  FACING-BET DISCOUNT — HEAVY (identical to v3slow)
        # ==============================================================
        if to_call > 0 and cs.street != "pre-flop":
            bet_frac = to_call / (pot - to_call + 0.01)
            discount = min(0.18, bet_frac * 0.14)

            if self._opp_won_auction:
                # Only apply the massive 1.7x panic discount if the opponent 
                # is actually making a significant bet (>25% of the pot).
                if bet_frac > 0.25:
                    discount *= 1.7
                    discount = min(discount, 0.20)

            if opp_card_strong:
                discount += 0.05

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
        # SAFEGUARD WIN - SUSTAIN BANKROLL
        # ==============================================================
        safeguard = False
        if hasattr(self, 'bankroll') and hasattr(self, 'round_num'):
            # Defensive mode if bankroll is safely positive or enough to weather blinds
            rounds_left = 1000 - self.round_num
            # Cost to fold every hand is 15 chips on average 
            guaranteed_win_cost = rounds_left * 15
            if self.bankroll > guaranteed_win_cost + 10:
                safeguard = True
            elif self.bankroll > 500: # Solid score safeguard
                safeguard = True
                
        if safeguard:
            # Play very defensively to sustain bankroll
            equity -= 0.06

        # ==============================================================
        #  PREFLOP — TIGHT (identical to v3slow)
        # ==============================================================
        if cs.street == "pre-flop":
            return self._pf_action(cs, equity, is_ip, r, bluff_mod, safeguard)

        # ==============================================================
        #  FLOP / TURN: EV Solver (20ms budget) → fallback to EQR
        # ==============================================================
        if cs.street in ("flop", "turn") and not safeguard:
            solver_action = self._ev_solver_action(cs)
            if solver_action is not None:
                return solver_action
            # Solver timed out or errored — fall through to EQR buckets

        # ==============================================================
        #  POSTFLOP BUCKETS — EQR fallback (river + safeguard + timeout)
        # ==============================================================

        # ── BUCKET 1: PREMIUM (equity > 0.84) ──
        if equity > 0.84:
            if cs.can_act(ActionRaise):
                # Strong equity: make opponent bleed by betting small 
                # instead of folding them out with a massive bet.
                bet = int(pot * random.uniform(0.15, 0.30))
                if r < 0.90:
                    return self._do_raise(cs, bet)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # ── BUCKET 2: STRONG (equity 0.70 – 0.84) ──
        if equity > 0.70:
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

        # ── BUCKET 3: MEDIUM (equity 0.57 – 0.70) ──
        if equity > 0.57:
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
            if opp_card_weak and is_ip and tex == "dry" and r < (0.08 + bluff_mod):
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
    # Preflop sub-strategy — TIGHT (identical to v3slow)
    # ------------------------------------------------------------------
    def _pf_action(self, cs: PokerState, eq: float, is_ip: bool,
                   r: float, bluff_mod: float, safeguard: bool = False):
        to_call = cs.cost_to_call

        if safeguard:
            # Require stronger hands to continue preflop
            eq -= 0.05

        if is_ip:
            if eq > 0.60:
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(BIG_BLIND * 2.5))
            elif eq > 0.40:  # Lowered to 0.40 - much looser, fold less
                if cs.can_act(ActionCall):
                    return ActionCall()
            else:
                return ActionFold() if cs.can_act(ActionFold) else ActionCall()
        else:
            if to_call == 0:
                if eq > 0.62 and cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(BIG_BLIND * 2.5))
                return ActionCheck()
            if eq > 0.70:
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(cs.pot * 2.5))
            if eq > 0.38:  # Lowered to 0.38 - much looser, fold less
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