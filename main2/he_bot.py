import pkbot
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot
import random
import eval7
import time

class Player(BaseBot):
    '''
    Tier 2 Blueprint Bot (SmartBot_v5)
    Bucket-based decisions enhanced with range-aware equity, positional modifiers,
    texture/SPR awareness, and competitive auction bidding.
    '''
    def __init__(self) -> None:
        self.total_hands = 0
        random.seed(time.time() + id(self))

        self.full_deck = [eval7.Card(s) for s in [
            '2c', '2d', '2h', '2s', '3c', '3d', '3h', '3s', '4c', '4d', '4h', '4s',
            '5c', '5d', '5h', '5s', '6c', '6d', '6h', '6s', '7c', '7d', '7h', '7s',
            '8c', '8d', '8h', '8s', '9c', '9d', '9h', '9s', 'Tc', 'Td', 'Th', 'Ts',
            'Jc', 'Jd', 'Jh', 'Js', 'Qc', 'Qd', 'Qh', 'Qs', 'Kc', 'Kd', 'Kh', 'Ks',
            'Ac', 'Ad', 'Ah', 'As'
        ]]

    def calculate_equity(self, my_hand_strs, board_strs, iters=200):
        """Monte Carlo equity calculation against a random opponent range."""
        if not my_hand_strs:
            return 0.0
        my_cards = [eval7.Card(s) for s in my_hand_strs]
        board_cards = [eval7.Card(s) for s in board_strs]
        dead_cards = set(my_cards + board_cards)
        deck = [c for c in self.full_deck if c not in dead_cards]

        wins = 0
        ties = 0
        for _ in range(iters):
            opp_cards = random.sample(deck, 2)
            needed = 5 - len(board_cards)
            remaining = [c for c in deck if c not in opp_cards]
            add_board = random.sample(remaining, needed) if needed > 0 else []
            my_val = eval7.evaluate(my_cards + board_cards + add_board)
            op_val = eval7.evaluate(opp_cards + board_cards + add_board)
            if my_val > op_val:
                wins += 1
            elif my_val == op_val:
                ties += 1
        return (wins + 0.5 * ties) / iters

    # ── Board texture ────────────────────────────────────────────
    def get_board_texture(self, board_strs):
        """Returns 'wet' or 'dry'."""
        if not board_strs:
            return "dry"
        cards = [eval7.Card(s) for s in board_strs]
        ranks = sorted([c.rank for c in cards])
        suits = [c.suit for c in cards]
        connected = sum(1 for i in range(len(ranks) - 1) if ranks[i+1] - ranks[i] <= 2)
        suit_counts = {}
        for s in suits:
            suit_counts[s] = suit_counts.get(s, 0) + 1
        max_suit = max(suit_counts.values()) if suit_counts else 0
        paired = len(ranks) - len(set(ranks))
        wet_score = connected + (1 if max_suit >= 3 else 0) - paired
        return "wet" if wet_score >= 2 else "dry"

    def get_spr(self, pot, my_chips, opp_chips):
        eff = min(my_chips, opp_chips)
        return eff / pot if pot > 0 else 100.0

    # ── Auction bidding ──────────────────────────────────────────
    def estimate_peek_ev(self, equity, current_state):
        """Information value based on uncertainty. Capped at 15% of pot to limit variance."""
        pot = current_state.pot
        uncertainty = 1.0 - abs(equity - 0.5) * 2.0  # peaks at 1.0 when eq=0.5
        info_value = uncertainty * pot * 0.18  # moderate bid coefficient
        if 0.42 < equity < 0.58:
            info_value *= 1.15  # slight boost for truly marginal spots
        # Hard cap: never bid more than 15% of pot
        max_bid = int(pot * 0.15)
        bid = int(min(current_state.my_chips, min(info_value, max_bid)))
        return max(0, bid)

    # ── Hooks ────────────────────────────────────────────────────
    def on_hand_start(self, game_info: GameInfo, current_state: PokerState) -> None:
        self.total_hands += 1

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        pass

    # ── Main decision ────────────────────────────────────────────
    def get_move(self, game_info: GameInfo, current_state: PokerState):
        # ── Auction ──────────────────────────────────────────────
        if current_state.street == 'auction':
            eq = self.calculate_equity(current_state.my_hand, current_state.board, iters=80)
            bid = self.estimate_peek_ev(eq, current_state)
            return ActionBid(int(bid))

        # ── Equity calculation ───────────────────────────────────
        iters = 50 if current_state.street == 'pre-flop' else 100
        equity = self.calculate_equity(current_state.my_hand, current_state.board, iters=iters)

        # ── Revealed card adjustment ─────────────────────────────
        if current_state.opp_revealed_cards:
            for card in current_state.opp_revealed_cards:
                val = card[0]
                if val == 'A':
                    equity -= 0.12
                elif val in ('K', 'Q'):
                    equity -= 0.08
                elif val in ('J', 'T'):
                    equity -= 0.05
                elif val in ('2', '3', '4', '5'):
                    equity += 0.04
            equity = max(0.0, min(1.0, equity))

        # ── Context ──────────────────────────────────────────────
        pot = current_state.pot
        to_call = current_state.cost_to_call
        is_ip = not current_state.is_bb  # button = IP in HU
        texture = self.get_board_texture(current_state.board)
        spr = self.get_spr(pot, current_state.my_chips, current_state.opp_chips)
        pot_odds = to_call / (pot + to_call + 1)
        r = random.random()

        # ── Positional & texture adjustments ─────────────────────
        # IP gets a small equity boost (positional advantage)
        if is_ip:
            equity += 0.03
        # Wet boards with draws: penalize slightly (reverse implied odds)
        if texture == "wet" and not is_ip and 0.50 < equity < 0.70:
            equity -= 0.05
        # Deep stacks with draws: boost (implied odds)
        if 0.35 < equity < 0.55 and spr > 12:
            equity += 0.04
        elif 0.35 < equity < 0.55 and spr > 8:
            equity += 0.02
        equity = max(0.0, min(1.0, equity))

        # ── Bucket 1: Premium (Equity > 0.78) ────────────────────
        if equity > 0.78:
            if current_state.can_act(ActionRaise):
                min_r, max_r = current_state.raise_bounds
                # Large value bet: pot-sized
                bet = int(pot * 1.0)
                raise_amt = min(max_r, max(min_r, bet))
                if r < 0.85:
                    return ActionRaise(raise_amt)
            if current_state.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # ── Bucket 2: Strong (Equity 0.62-0.78) ──────────────────
        if equity > 0.62:
            if current_state.can_act(ActionRaise) and r < 0.40:
                min_r, max_r = current_state.raise_bounds
                bet = int(pot * 0.60)
                return ActionRaise(min(max_r, max(min_r, bet)))
            if current_state.can_act(ActionCall):
                return ActionCall()
            return ActionCheck()

        # ── Bucket 3: Marginal (Equity 0.48-0.62) ────────────────
        if equity > 0.48:
            # Only call if pot odds justify it (tighter threshold)
            if pot_odds < equity - 0.08:
                if current_state.can_act(ActionCall):
                    return ActionCall()
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            # Small bluff raise when IP (reduced freq)
            if r < 0.08 and is_ip and current_state.can_act(ActionRaise):
                min_r, _ = current_state.raise_bounds
                return ActionRaise(min_r)
            return ActionFold() if current_state.can_act(ActionFold) else ActionCheck()

        # ── Bucket 4: Weak (Equity < 0.48) ──────────────────────
        if current_state.can_act(ActionCheck):
            return ActionCheck()
        # Semi-bluff: small % of the time with suited connectors etc.
        if r < 0.04 and current_state.can_act(ActionRaise):
            min_r, max_r = current_state.raise_bounds
            bet = int(pot * 0.55)
            return ActionRaise(min(max_r, max(min_r, bet)))
        return ActionFold() if current_state.can_act(ActionFold) else ActionCheck()


if __name__ == '__main__':
    run_bot(Player(), parse_args())