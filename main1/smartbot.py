import pkbot
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot
import random
import eval7
import math

class Player(BaseBot):
    '''
    Equity + Mixed Heuristic Bot
    '''
    def __init__(self) -> None:
        self.opp_fold_count = 0
        self.opp_raise_count = 0
        self.total_hands = 0
        
        # Precompute the deck for eval7
        self.full_deck = [eval7.Card(s) for s in [
            '2c', '2d', '2h', '2s', '3c', '3d', '3h', '3s', '4c', '4d', '4h', '4s',
            '5c', '5d', '5h', '5s', '6c', '6d', '6h', '6s', '7c', '7d', '7h', '7s',
            '8c', '8d', '8h', '8s', '9c', '9d', '9h', '9s', 'Tc', 'Td', 'Th', 'Ts',
            'Jc', 'Jd', 'Jh', 'Js', 'Qc', 'Qd', 'Qh', 'Qs', 'Kc', 'Kd', 'Kh', 'Ks',
            'Ac', 'Ad', 'Ah', 'As'
        ]]

    def calculate_equity(self, my_hand_strs, board_strs, iters=300):
        """
        Calculates hand equity using eval7 Monte Carlo rollouts.
        """
        if not my_hand_strs:
            return 0.0

        my_cards = [eval7.Card(s) for s in my_hand_strs]
        board_cards = [eval7.Card(s) for s in board_strs]

        dead_cards = set(my_cards + board_cards)
        deck = [c for c in self.full_deck if c not in dead_cards]

        wins = 0
        ties = 0

        for _ in range(iters):
            # Sample opponent cards
            opp_cards = random.sample(deck, 2)
            
            # Sample remaining board cards
            needed_board = 5 - len(board_cards)
            remaining_deck = [c for c in deck if c not in opp_cards]
            additional_board = random.sample(remaining_deck, needed_board)
            
            # Evaluate hands
            my_eval = eval7.evaluate(my_cards + board_cards + additional_board)
            opp_eval = eval7.evaluate(opp_cards + board_cards + additional_board)
            
            if my_eval > opp_eval:
                wins += 1
            elif my_eval == opp_eval:
                ties += 1

        return (wins + 0.5 * ties) / iters

    def estimate_peek_ev(self, equity, current_state):
        """
        Estimates the exact expected value (in chips) of seeing the opponent's card.
        Vickrey Auction truthful bidding strategy.
        """
        pot = current_state.pot
        cost_to_call = current_state.cost_to_call
        
        # Base value: Information is more valuable when the pot is large and decision is hard
        # If equity is near 0.5 (marginal), info is extremely valuable to save mistakes
        uncertainty = 1.0 - abs(equity - 0.5) * 2.0  # Peaks at 1.0 when equity=0.5, 0 when 0 or 1
        
        # Simplified Model: The worth of a peek is roughly the fraction of the pot we might save 
        # from a bad call or make from a good value bet we would have otherwise missed.
        # Generally, seeing 1 card gives you ~50% confidence on their hand strength.
        info_value = uncertainty * pot * 0.15 # Max 15% of pot value
        
        return int(min(current_state.my_chips, info_value))

    def on_hand_start(self, game_info: GameInfo, current_state: PokerState) -> None:
        self.total_hands += 1

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        pass # Implement opponent tracking here if desired

    def get_move(self, game_info: GameInfo, current_state: PokerState) -> ActionFold | ActionCall | ActionCheck | ActionRaise | ActionBid:
        # 1. Handle Auction (Truthful Bidding)
        if current_state.street == 'auction':
            equity = self.calculate_equity(current_state.my_hand, current_state.board, iters=100) # Fast check
            ev_of_info = self.estimate_peek_ev(equity, current_state)
            return ActionBid(int(ev_of_info))

        # 2. Main Betting Logic
        # Time Management: We must stay under 20s total (0.02s per hand average)
        # 30-50 iterations guarantees we clear the strict 20s total match limit
        iters = 30 if current_state.street == 'preflop' else 50
        equity = self.calculate_equity(current_state.my_hand, current_state.board, iters=iters)
        
        # If opponent card revealed, adjust equity intuitively (if they have an Ace, drop equity)
        if current_state.opp_revealed_cards:
            for card in current_state.opp_revealed_cards:
                if card[0] in ['A', 'K', 'Q']:
                    equity -= 0.15

        pot_odds = current_state.cost_to_call / (current_state.pot + current_state.cost_to_call + 1)
        r = random.random()

        # Bucket 1: Premium (Equity > 0.75)
        if equity > 0.75:
            if current_state.can_act(ActionRaise):
                min_r, max_r = current_state.raise_bounds
                # Value bet: bet 75% of pot
                bet = current_state.pot * 0.75
                raise_amount = min(max_r, max(min_r, int(bet)))
                if r < 0.8: # 80% raise, 20% slowplay/call
                    return ActionRaise(raise_amount)
            if current_state.can_act(ActionCall): return ActionCall()
            return ActionCheck()

        # Bucket 2: Strong (Equity 0.60 - 0.75)
        elif equity > 0.60:
            if current_state.can_act(ActionRaise) and r < 0.4:
                min_r, max_r = current_state.raise_bounds
                bet = current_state.pot * 0.50
                return ActionRaise(min(max_r, max(min_r, int(bet))))
            if current_state.can_act(ActionCall): return ActionCall()
            return ActionCheck()

        # Bucket 3: Marginal/Bluff-Catchers (Equity 0.45 - 0.60)
        elif equity > 0.45:
            # Call if pot odds are favorable
            if pot_odds < equity:
                if current_state.can_act(ActionCall): return ActionCall()
            if current_state.can_act(ActionCheck): return ActionCheck()
            # Default to fold if bet is too huge
            if r < 0.1 and current_state.can_act(ActionRaise): # Tiny bluff frequency
                min_r, _ = current_state.raise_bounds
                return ActionRaise(min_r)
            return ActionFold() if current_state.can_act(ActionFold) else ActionCheck()

        # Bucket 4 & 5: Weak/Trash (Equity < 0.45)
        else:
            if current_state.can_act(ActionCheck): return ActionCheck()
            
            # Semi-bluff a small fraction (e.g. 5%)
            if r < 0.05 and current_state.can_act(ActionRaise):
                min_r, max_r = current_state.raise_bounds
                bet = current_state.pot * 0.66
                return ActionRaise(min(max_r, max(min_r, int(bet))))
                
            return ActionFold() if current_state.can_act(ActionFold) else ActionCheck()

if __name__ == '__main__':
    run_bot(Player(), parse_args())