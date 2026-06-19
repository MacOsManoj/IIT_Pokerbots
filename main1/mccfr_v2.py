from pkbot.base import BaseBot
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
import random

MC_SIMS = 300
BUCKETS = {0: 169, 1: 10, 2: 10, 3: 10, 4: 10}
RANKS, SUITS = '23456789TJQKA', 'cdhs'
STREET_MAP = {'pre-flop': 0, 'auction': 1, 'flop': 2, 'turn': 3, 'river': 4}

STRATEGY = {}

def card_to_int(card: str) -> int:
    return RANKS.index(card[0].upper()) * 4 + SUITS.index(card[1].lower())

def int_to_card(c: int) -> tuple:
    return (c // 4, c % 4)

def evaluate_hand(cards: list) -> int:
    hand = [int_to_card(c) for c in cards]
    ranks = sorted([r for r, _ in hand], reverse=True)
    rank_counts, suit_counts = {}, {}
    for r, s in hand:
        rank_counts[r] = rank_counts.get(r, 0) + 1
        suit_counts[s] = suit_counts.get(s, 0) + 1
    
    flush_suit = next((s for s, c in suit_counts.items() if c >= 5), None)
    flush_ranks = sorted([r for r, s in hand if s == flush_suit], reverse=True)[:5] if flush_suit else None
    
    def find_straight(rank_set):
        for high in range(12, 3, -1):
            if all((high - i) in rank_set for i in range(5)): return high
        return 3 if all(r in rank_set for r in [12, 0, 1, 2, 3]) else None
    
    straight_high = find_straight(set(ranks))
    if flush_suit and (sf := find_straight(set(flush_ranks or []))): return 9e10 + sf
    groups = sorted(rank_counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    if groups[0][1] == 4: return 8e10 + groups[0][0] * 100 + max(r for r in ranks if r != groups[0][0])
    if groups[0][1] == 3 and groups[1][1] >= 2: return 7e10 + groups[0][0] * 100 + groups[1][0]
    if flush_ranks: return 6e10 + sum(r * (13 ** (4 - i)) for i, r in enumerate(flush_ranks))
    if straight_high: return 5e10 + straight_high
    if groups[0][1] == 3:
        k = sorted([r for r in ranks if r != groups[0][0]], reverse=True)[:2]
        return 4e10 + groups[0][0] * 1000 + k[0] * 13 + k[1]
    if groups[0][1] == 2 and groups[1][1] == 2:
        k = max(r for r in ranks if r != groups[0][0] and r != groups[1][0])
        return 3e10 + groups[0][0] * 200 + groups[1][0] * 13 + k
    if groups[0][1] == 2:
        k = sorted([r for r in ranks if r != groups[0][0]], reverse=True)[:3]
        return 2e10 + groups[0][0] * 10000 + k[0] * 169 + k[1] * 13 + k[2]
    top5 = sorted(ranks, reverse=True)[:5]
    return 1e10 + sum(r * (13 ** (4 - i)) for i, r in enumerate(top5))

def calc_equity(hole: list, board: list, sims: int = MC_SIMS) -> float:
    used = set(hole + board)
    deck = [i for i in range(52) if i not in used]
    wins, ties = 0, 0
    for _ in range(sims):
        random.shuffle(deck)
        fb = board + deck[:5 - len(board)]
        opp = deck[5 - len(board):7 - len(board)]
        ms, os = evaluate_hand(hole + fb), evaluate_hand(opp + fb)
        if ms > os: wins += 1
        elif ms == os: ties += 1
    return (wins + 0.5 * ties) / sims

def get_hand_cat(hole: list) -> str:
    r1, r2 = hole[0] // 4, hole[1] // 4
    suited = (hole[0] % 4) == (hole[1] % 4)
    high, low, paired = max(r1, r2), min(r1, r2), r1 == r2
    if paired and high >= 10: return "premium"
    if high == 12 and low == 11: return "premium"
    if paired and high >= 7: return "strong"
    if high == 12 and low >= 9: return "strong"
    if high == 11 and low >= 10: return "strong"
    if paired and high >= 4: return "medium"
    if suited and abs(r1 - r2) <= 2 and low >= 6: return "medium"
    if high >= 9 and low >= 8: return "medium"
    if suited and high == 12: return "speculative"
    if paired: return "speculative"
    if suited and abs(r1 - r2) <= 2: return "speculative"
    return "weak"

def compute_bid(hole: list, pot: int, chips: int, eq: float) -> int:
    if pot == 0: return 0
    cat_mult = {"premium": 0.15, "strong": 0.25, "medium": 0.40, "speculative": 0.30, "weak": 0.10}
    uncertainty = 1.0 - abs(eq - 0.5) * 2
    bid = int(uncertainty * cat_mult.get(get_hand_cat(hole), 0.25) * pot * 1.2)
    return max(0, min(bid, chips))

def get_key(state: PokerState, history: str, has_info: bool) -> str:
    street = STREET_MAP.get(state.street, 0)
    hole = [card_to_int(c) for c in state.my_hand]
    board = []
    if state.street in ['flop', 'auction'] and len(state.board) >= 3: board = [card_to_int(c) for c in state.board[:3]]
    elif state.street == 'turn' and len(state.board) >= 4: board = [card_to_int(c) for c in state.board[:4]]
    elif state.street == 'river': board = [card_to_int(c) for c in state.board]
    eq = calc_equity(hole, board)
    eq_bucket = min(int(eq * BUCKETS.get(street, 10)), BUCKETS.get(street, 10) - 1)
    return f"{street}_{eq_bucket}_{state.pot // 500}_{1 if state.is_bb else 0}_{history}_{1 if has_info else 0}"

def encode(action, state: PokerState) -> str:
    if isinstance(action, ActionFold): return "0"
    if isinstance(action, (ActionCheck, ActionCall)): return "1"
    if isinstance(action, ActionBid):
        pct = action.amount / state.pot if state.pot > 0 else 0
        if pct <= 0.125: return "0"
        if pct <= 0.375: return "1"
        if pct <= 0.625: return "2"
        if pct <= 0.875: return "3"
        return "4"
    if isinstance(action, ActionRaise):
        ra = action.amount - state.my_wager
        if state.my_chips - ra <= 0: return "4"
        if state.pot > 0 and ra / state.pot <= 0.75: return "2"
        return "3"
    return "1"

def safe_raise(state: PokerState, size: str):
    if not state.can_act(ActionRaise):
        return ActionCall() if state.can_act(ActionCall) else (ActionCheck() if state.can_act(ActionCheck) else ActionFold())
    min_r, max_r = state.raise_bounds
    if size == 'half': amt = state.my_wager + state.cost_to_call + state.pot // 2
    elif size == 'pot': amt = state.my_wager + state.cost_to_call + state.pot
    elif size == 'allin': amt = max_r
    else: amt = min_r
    return ActionRaise(int(max(min_r, min(amt, max_r))))

def safe_bid(state: PokerState, amount: int) -> ActionBid:
    return ActionBid(max(0, min(int(amount), state.my_chips)))

def action_from_idx(idx: int, state: PokerState):
    if state.street == 'auction':
        return safe_bid(state, int([0.0, 0.25, 0.50, 0.75, 1.0][min(idx, 4)] * state.pot))
    if idx == 0: return ActionFold() if state.can_act(ActionFold) else (ActionCheck() if state.can_act(ActionCheck) else ActionCall())
    if idx == 1: return ActionCheck() if state.can_act(ActionCheck) else (ActionCall() if state.can_act(ActionCall) else ActionFold())
    if idx == 2: return safe_raise(state, 'half')
    if idx == 3: return safe_raise(state, 'pot')
    if idx == 4: return safe_raise(state, 'allin')
    return ActionCheck() if state.can_act(ActionCheck) else ActionFold()

def sample_action(strat: list, state: PokerState):
    if not strat:
        if state.street == 'auction': return safe_bid(state, state.pot // 4)
        return ActionCheck() if state.can_act(ActionCheck) else (ActionCall() if state.can_act(ActionCall) else ActionFold())
    total = sum(strat)
    strat = [1.0 / len(strat)] * len(strat) if total == 0 else [p / total for p in strat]
    r, cum = random.random(), 0.0
    for i, p in enumerate(strat):
        cum += p
        if r < cum: return action_from_idx(i, state)
    return action_from_idx(len(strat) - 1, state)

class MCCFRBot(BaseBot):
    def __init__(self):
        self.history, self.has_info, self.last_street = "", False, None

    def on_hand_start(self, gi: GameInfo, s: PokerState):
        self.history, self.has_info, self.last_street = "", False, "pre-flop"

    def on_hand_end(self, gi: GameInfo, s: PokerState): pass

    def get_move(self, gi: GameInfo, s: PokerState):
        try:
            if s.opp_revealed_cards: self.has_info = True
            if s.street != self.last_street: self.last_street = s.street
            
            if s.street == 'auction':
                hole = [card_to_int(c) for c in s.my_hand]
                board = [card_to_int(c) for c in s.board[:3]] if len(s.board) >= 3 else []
                return safe_bid(s, compute_bid(hole, s.pot, s.my_chips, calc_equity(hole, board)))
            
            key = get_key(s, self.history, self.has_info)
            if STRATEGY and key in STRATEGY:
                action = sample_action(STRATEGY[key], s)
                self.history += encode(action, s)
                return action
            
            hole = [card_to_int(c) for c in s.my_hand]
            board = [card_to_int(c) for c in s.board]
            eq = calc_equity(hole, board, MC_SIMS // 2)
            action = self._eq_action(s, eq)
            self.history += encode(action, s)
            return action
        except: return self._fallback(s)
    
    def _eq_action(self, s: PokerState, eq: float):
        if s.cost_to_call == 0:
            if eq > 0.65 and s.can_act(ActionRaise): return safe_raise(s, 'pot' if eq > 0.85 else 'half')
            return ActionCheck() if s.can_act(ActionCheck) else ActionFold()
        pot_odds = s.cost_to_call / (s.pot + s.cost_to_call)
        if eq > 0.75 and s.can_act(ActionRaise): return safe_raise(s, 'pot' if eq > 0.85 else 'half')
        if eq > pot_odds + 0.05 and s.can_act(ActionCall): return ActionCall()
        return ActionFold() if s.can_act(ActionFold) else ActionCheck()
    
    def _fallback(self, s: PokerState):
        if s.street == 'auction': return ActionBid(0)
        if s.can_act(ActionCheck): return ActionCheck()
        if s.can_act(ActionCall): return ActionCall()
        return ActionFold()

Player = MCCFRBot

if __name__ == "__main__":
    from pkbot.runner import parse_args, run_bot
    run_bot(Player(), parse_args())
