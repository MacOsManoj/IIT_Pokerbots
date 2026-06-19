"""
allin_bot.py — All-In Every Time Bot

Simple aggressive bot that goes all-in on every betting decision.

Strategy:
  - Auction: Bid 0 (save chips for actual betting)
  - Betting: Always raise maximum possible (all remaining chips)
  - If can't raise (opponent already all-in), call
  - Never fold, never check unless forced
"""

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot


class Player(BaseBot):

    def __init__(self) -> None:
        pass

    def on_hand_start(self, game_info: GameInfo, cs: PokerState) -> None:
        pass

    def on_hand_end(self, game_info: GameInfo, cs: PokerState) -> None:
        pass

    def get_move(self, game_info: GameInfo, cs: PokerState):
        """
        Go all-in every single time.
        """
        # ══════════════════════════════════════════════════════════════
        # AUCTION: Bid 0 to save chips for betting
        # ══════════════════════════════════════════════════════════════
        if cs.street == "auction":
            return ActionBid(0)

        # ══════════════════════════════════════════════════════════════
        # BETTING: Always go all-in
        # ══════════════════════════════════════════════════════════════
        
        # If we can raise, raise to maximum (all our chips)
        if cs.can_act(ActionRaise):
            min_raise, max_raise = cs.raise_bounds
            # Go all-in: raise to maximum possible
            return ActionRaise(max_raise)
        
        # If we can't raise but can call, call
        if cs.can_act(ActionCall):
            return ActionCall()
        
        # If we can only check, check
        if cs.can_act(ActionCheck):
            return ActionCheck()
        
        # Shouldn't happen, but fold as fallback
        return ActionFold()


if __name__ == "__main__":
    run_bot(Player(), parse_args())
