"""
speedbotv2.py — IIT Pokerbots 2026 "Sneak Peek Hold'em" Bot v4

Improvements over v3 (speedbot.py):
  1. Monster bid denial: Bid HIGH with strong hands to deny opponent cheap info.
  2. Anti-tell bids: Add noise to bids so opponent can't reverse-engineer equity.
  3. Post-auction awareness: If opponent won the auction and then overbets,
     apply a heavier discount — they're playing with extra info about our hand.
  4. Higher auction caps: Second-price auction means we only pay 2nd-highest bid,
     so we can bid closer to true value without overpaying.
"""

import random
import time

import eval7

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

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
        # FIX 3: Track auction outcomes per hand
        self._opp_won_auction = False
        self._we_won_auction = False

    # ------------------------------------------------------------------
    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        self.total_hands += 1
        self._last_cost_to_call = 0
        self._opp_won_auction = False
        self._we_won_auction = False

    def on_hand_end(self, game_info: GameInfo, cs: PokerState) -> None:
        pass

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
    # Equity
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
    # Board helpers
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
    # Auction  (FIXES 1, 2, 4)
    # ------------------------------------------------------------------
    def _bid(self, cs: PokerState) -> int:
        eq = self._mc_eq(cs.my_hand, cs.board, iters=120)
        pot = cs.pot

        # ── Core value: uncertainty-based info value ──
        unc = max(0.0, 1.0 - (2.0 * abs(eq - 0.5)) ** 1.4)

        # FIX 1: MONSTER BID DENIAL
        # With strong hands, bid HIGH to deny opponent cheap information.
        # If they see our card cheaply, they fold and we lose value.
        # By bidding high, we either: (a) win and see their card, or
        # (b) they pay a high price (second-price) to see ours.
        if eq > 0.80:
            # Bid aggressively — deny info. Value = protecting a big pot.
            iv = pot * random.uniform(0.30, 0.45)
        elif eq > 0.70:
            # Strong but not monster — moderate denial bid
            iv = pot * random.uniform(0.18, 0.30)
        elif eq < 0.25:
            # Trash — no value in info, bid near zero
            iv = pot * random.uniform(0.00, 0.03)
        elif eq < 0.35:
            # Weak — info might help but not worth much
            iv = pot * random.uniform(0.02, 0.06)
        else:
            # Marginal (0.35 – 0.70): uncertainty-based, info is most valuable
            iv = unc * pot * 0.25

        # FIX 4: HIGHER CAPS FOR SECOND-PRICE AUCTION
        # In Vickrey (second-price) you pay the LOSER's bid, not yours.
        # So bidding closer to true value is optimal — we won't overpay.
        # Old caps: 3% stack / 20% pot → way too conservative.
        iv = min(iv, cs.my_chips * 0.08, pot * 0.50)

        # Opponent bid exploitation (keep from v3)
        if len(self.opp_bids) >= 10:
            avg = self.opp_avg_bid
            if avg > iv * 2.5:
                iv *= 0.5   # they overbid, let them win sometimes
            elif avg < iv * 0.3:
                iv = max(iv, avg + 2)  # they underbid, outbid by a hair

        # FIX 2: ANTI-TELL NOISE
        # Add random noise so bids don't map cleanly to equity tiers.
        # ±15% jitter makes bid tracking useless for opponent.
        noise = random.uniform(0.85, 1.15)
        iv *= noise

        return int(max(0, iv))

    # ------------------------------------------------------------------
    @staticmethod
    def _do_raise(cs: PokerState, amt: int):
        mn, mx = cs.raise_bounds
        return ActionRaise(min(mx, max(mn, amt)))

    # ------------------------------------------------------------------
    # MAIN
    # ------------------------------------------------------------------
    def get_move(self, game_info: GameInfo, cs: PokerState):
        # ── Auction ──
        if cs.street == "auction":
            return ActionBid(self._bid(cs))

        # ── FIX 3: Detect auction outcome ──
        # After auction street, if opponent has revealed cards, we won
        # the auction (or tied). If we see our opp_revealed_cards appear
        # for the first time on flop, track it.
        if cs.street == "flop" and not self._opp_won_auction and not self._we_won_auction:
            if cs.opp_revealed_cards:
                self._we_won_auction = True
            else:
                # We don't see their card → either they won or nobody won.
                # If cost_to_call is 0 and pot is small, likely no auction winner.
                # Conservative: assume opponent may have seen our card.
                self._opp_won_auction = True

        # ── Equity ──
        equity = self._equity(cs)

        # ── Context ──
        pot       = cs.pot
        to_call   = cs.cost_to_call
        my_chips  = cs.my_chips
        opp_chips = cs.opp_chips
        is_ip     = not cs.is_bb
        tex       = self._texture(cs.board)
        spr       = self._spr(pot, my_chips, opp_chips)
        r         = random.random()

        # ── Position boost ──
        if is_ip:
            equity += 0.02

        # ── Revealed card bonus (MC already handles it, tiny quality edge) ──
        if cs.opp_revealed_cards:
            equity += 0.015

        # ── Texture & SPR adjustments ──
        if tex == "wet" and not is_ip and 0.45 < equity < 0.65:
            equity -= 0.03
        if spr > 8 and 0.35 < equity < 0.55:
            equity += 0.02

        equity = max(0.0, min(1.0, equity))

        # ==============================================================
        #  FACING-BET DISCOUNT (from v3)
        # ==============================================================
        if to_call > 0 and cs.street != "pre-flop":
            bet_frac = to_call / (pot - to_call + 0.01)
            discount = min(0.12, bet_frac * 0.08)

            # FIX 3: POST-AUCTION AWARENESS
            # If opponent won the auction (they see one of our cards)
            # and are now betting aggressively, they're using that info.
            # Their aggression is MORE informed → apply heavier discount.
            if self._opp_won_auction:
                # Scale up discount by 50% when opponent has extra info
                discount *= 1.5
                discount = min(discount, 0.18)

            equity -= discount
            equity = max(0.0, equity)

        # ── Pot odds ──
        pot_odds = to_call / (pot + to_call + 0.01) if to_call > 0 else 0.0

        # ── Opponent exploitation ──
        bluff_mod = 0.0
        if self.total_hands > 40:
            if self.opp_fold_pct > 0.45:
                bluff_mod = 0.06
            elif self.opp_fold_pct < 0.20:
                bluff_mod = -0.03

        # ==============================================================
        #  PREFLOP
        # ==============================================================
        if cs.street == "pre-flop":
            return self._pf_action(cs, equity, is_ip, r, bluff_mod)

        # ==============================================================
        #  POSTFLOP BUCKETS
        # ==============================================================

        # ── BUCKET 1: PREMIUM (equity > 0.78) ──
        if equity > 0.78:
            if cs.can_act(ActionRaise):
                bet = int(pot * random.uniform(0.75, 1.1))
                if r < 0.87:
                    return self._do_raise(cs, bet)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # ── BUCKET 2: STRONG (equity 0.63 – 0.78) ──
        if equity > 0.63:
            if cs.can_act(ActionRaise):
                bet = int(pot * random.uniform(0.50, 0.70))
                if r < 0.50:
                    return self._do_raise(cs, bet)
            if cs.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # ── BUCKET 3: MARGINAL (equity 0.48 – 0.63) ──
        if equity > 0.48:
            if to_call > 0:
                if pot_odds < equity - 0.02:
                    if cs.can_act(ActionCall):
                        return ActionCall()
                elif pot_odds < equity + 0.06 and r < 0.20:
                    if cs.can_act(ActionCall):
                        return ActionCall()
                if cs.can_act(ActionCheck):
                    return ActionCheck()
                return ActionFold() if cs.can_act(ActionFold) else ActionCheck()
            if is_ip and r < 0.30 and cs.can_act(ActionRaise):
                return self._do_raise(cs, int(pot * random.uniform(0.33, 0.50)))
            return ActionCheck() if cs.can_act(ActionCheck) else ActionCall()

        # ── BUCKET 4: WEAK (equity 0.33 – 0.48) ──
        if equity > 0.33:
            if cs.can_act(ActionCheck):
                if is_ip and tex == "dry" and r < (0.10 + bluff_mod):
                    if cs.can_act(ActionRaise):
                        return self._do_raise(cs, int(pot * random.uniform(0.55, 0.75)))
                return ActionCheck()
            if to_call > 0 and pot_odds < equity - 0.03 and r < 0.20:
                if cs.can_act(ActionCall):
                    return ActionCall()
            return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

        # ── BUCKET 5: TRASH (equity < 0.33) ──
        if cs.can_act(ActionCheck):
            if r < (0.04 + bluff_mod) and cs.can_act(ActionRaise):
                return self._do_raise(cs, int(pot * random.uniform(0.60, 0.80)))
            return ActionCheck()
        return ActionFold() if cs.can_act(ActionFold) else ActionCheck()

    # ------------------------------------------------------------------
    # Preflop sub-strategy
    # ------------------------------------------------------------------
    def _pf_action(self, cs: PokerState, eq: float, is_ip: bool,
                   r: float, bluff_mod: float):
        to_call = cs.cost_to_call

        # SB / Button (acts first preflop in HU)
        if is_ip:
            if eq > 0.54:
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(BIG_BLIND * 2.5))
            elif eq > 0.46:
                if cs.can_act(ActionCall):
                    return ActionCall()
            else:
                if eq > 0.40 and r < (0.12 + bluff_mod):
                    if cs.can_act(ActionRaise):
                        return self._do_raise(cs, int(BIG_BLIND * 2.5))
                return ActionFold() if cs.can_act(ActionFold) else ActionCall()

        # BB (acts second preflop)
        else:
            if to_call == 0:
                if eq > 0.56 and cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(BIG_BLIND * 3))
                return ActionCheck()
            if eq > 0.64:
                if cs.can_act(ActionRaise):
                    return self._do_raise(cs, int(cs.pot * 3))
            if eq > 0.45:
                if cs.can_act(ActionCall):
                    return ActionCall()
            elif eq > 0.38 and r < 0.15:
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
if __name__ == "__main__":
    run_bot(Player(), parse_args())
