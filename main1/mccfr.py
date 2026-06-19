from pkbot.base import BaseBot
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
import random
import json
import gzip
import os

# =============================================================================
# CONSTANTS (must match offline.cpp)
# =============================================================================
STARTING_STACK = 5000
PREFLOP_BUCKETS = 169
FLOP_BUCKETS = 10
TURN_BUCKETS = 10
RIVER_BUCKETS = 10
MC_SIMULATIONS = 500  # Number of Monte Carlo simulations for equity

# =============================================================================
# CFR STRATEGY LOADING
# =============================================================================
# Load strategy from compressed JSON file (created by convert_strategy.py)
# This is much more efficient than embedding the dict in Python source

def load_strategy():
    """Load CFR strategy from compressed JSON file."""
    import sys
    strategy_file = os.path.join(os.path.dirname(__file__), "cfr_strategy.json.gz")
    
    if not os.path.exists(strategy_file):
        print(f"Warning: Strategy file not found: {strategy_file}", file=sys.stderr)
        print("Bot will use equity-based fallback strategy.", file=sys.stderr)
        return {}
    
    try:
        with gzip.open(strategy_file, 'rt', encoding='utf-8') as f:
            strategy = json.load(f)
        print(f"Loaded {len(strategy):,} strategy entries from {strategy_file}", file=sys.stderr)
        return strategy
    except Exception as e:
        print(f"Error loading strategy file: {e}", file=sys.stderr)
        return {}

# Load strategy at module import time
STRATEGY = load_strategy()

# Street mapping (string -> int) - matches engine's get_street_name()
STREET_MAP = {
    'pre-flop': 0,  # Engine uses hyphen!
    'auction': 1,
    'flop': 2,
    'turn': 3,
    'river': 4
}

# =============================================================================
# CARD UTILITIES
# =============================================================================
RANKS = '23456789TJQKA'
SUITS = 'cdhs'

def card_to_int(card_str: str) -> int:
    """Convert card string like 'Ah' to integer 0-51."""
    rank = RANKS.index(card_str[0].upper())
    suit = SUITS.index(card_str[1].lower())
    return rank * 4 + suit

def int_to_card(card_int: int) -> tuple:
    """Convert integer to (rank, suit) where rank is 0-12, suit is 0-3."""
    return (card_int // 4, card_int % 4)

# =============================================================================
# HAND EVALUATION (7-card evaluator)
# =============================================================================
def evaluate_hand(cards: list) -> int:
    """
    Evaluate a 7-card hand. Returns a score where higher is better.
    Score format: hand_rank * 10^10 + kickers
    Hand ranks: 1=high card, 2=pair, 3=two pair, 4=trips, 5=straight,
                6=flush, 7=full house, 8=quads, 9=straight flush
    """
    # Convert to (rank, suit) tuples
    hand = [int_to_card(c) for c in cards]
    ranks = sorted([r for r, s in hand], reverse=True)
    suits = [s for r, s in hand]
    
    # Count ranks and suits
    rank_counts = {}
    suit_counts = {}
    for r, s in hand:
        rank_counts[r] = rank_counts.get(r, 0) + 1
        suit_counts[s] = suit_counts.get(s, 0) + 1
    
    # Check for flush
    flush_suit = None
    for s, count in suit_counts.items():
        if count >= 5:
            flush_suit = s
            break
    
    # Get flush cards if flush exists
    flush_ranks = None
    if flush_suit is not None:
        flush_ranks = sorted([r for r, s in hand if s == flush_suit], reverse=True)[:5]
    
    # Check for straight (including ace-low)
    def find_straight(rank_set):
        for high in range(12, 3, -1):  # 12 down to 4 (A-high to 5-high)
            if all((high - i) in rank_set for i in range(5)):
                return high
        # Check A-2-3-4-5 (wheel)
        if all(r in rank_set for r in [12, 0, 1, 2, 3]):
            return 3  # 5-high straight
        return None
    
    unique_ranks = set(ranks)
    straight_high = find_straight(unique_ranks)
    
    # Check for straight flush
    if flush_suit is not None:
        flush_set = set(flush_ranks)
        sf_high = find_straight(flush_set)
        if sf_high is not None:
            return 9 * 10**10 + sf_high
    
    # Get rank groups sorted by count then rank
    groups = sorted(rank_counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    
    # Four of a kind
    if groups[0][1] == 4:
        quad_rank = groups[0][0]
        kicker = max(r for r in ranks if r != quad_rank)
        return 8 * 10**10 + quad_rank * 100 + kicker
    
    # Full house
    if groups[0][1] == 3 and groups[1][1] >= 2:
        trip_rank = groups[0][0]
        pair_rank = groups[1][0]
        return 7 * 10**10 + trip_rank * 100 + pair_rank
    
    # Flush
    if flush_ranks is not None:
        score = 6 * 10**10
        for i, r in enumerate(flush_ranks):
            score += r * (13 ** (4 - i))
        return score
    
    # Straight
    if straight_high is not None:
        return 5 * 10**10 + straight_high
    
    # Three of a kind
    if groups[0][1] == 3:
        trip_rank = groups[0][0]
        kickers = sorted([r for r in ranks if r != trip_rank], reverse=True)[:2]
        return 4 * 10**10 + trip_rank * 1000 + kickers[0] * 13 + kickers[1]
    
    # Two pair
    if groups[0][1] == 2 and groups[1][1] == 2:
        high_pair = groups[0][0]
        low_pair = groups[1][0]
        kicker = max(r for r in ranks if r != high_pair and r != low_pair)
        return 3 * 10**10 + high_pair * 200 + low_pair * 13 + kicker
    
    # One pair
    if groups[0][1] == 2:
        pair_rank = groups[0][0]
        kickers = sorted([r for r in ranks if r != pair_rank], reverse=True)[:3]
        return 2 * 10**10 + pair_rank * 10000 + kickers[0] * 169 + kickers[1] * 13 + kickers[2]
    
    # High card
    top5 = sorted(ranks, reverse=True)[:5]
    score = 1 * 10**10
    for i, r in enumerate(top5):
        score += r * (13 ** (4 - i))
    return score

# =============================================================================
# EQUITY CALCULATION (Monte Carlo)
# =============================================================================
def calculate_equity(hole_cards: list, board: list, num_sims: int = MC_SIMULATIONS) -> float:
    """
    Calculate hand equity via Monte Carlo simulation.
    hole_cards and board are lists of integer card values (0-51).
    Returns equity as float 0.0 to 1.0.
    """
    used = set(hole_cards + board)
    deck = [i for i in range(52) if i not in used]
    
    wins = 0
    ties = 0
    
    for _ in range(num_sims):
        random.shuffle(deck)
        
        # Complete the board
        full_board = board + deck[:5 - len(board)]
        
        # Deal opponent cards
        opp_hole = deck[5 - len(board):7 - len(board)]
        
        # Evaluate hands
        my_hand = hole_cards + full_board
        opp_hand = opp_hole + full_board
        
        my_score = evaluate_hand(my_hand)
        opp_score = evaluate_hand(opp_hand)
        
        if my_score > opp_score:
            wins += 1
        elif my_score == opp_score:
            ties += 1
    
    return (wins + 0.5 * ties) / num_sims

def equity_to_bucket(equity: float, num_buckets: int) -> int:
    """Convert equity (0-1) to bucket number (0 to num_buckets-1)."""
    bucket = int(equity * num_buckets)
    return min(bucket, num_buckets - 1)

# =============================================================================
# AUCTION STRATEGY MODULE
# =============================================================================
"""
Second-Price (Vickrey) Auction Strategy for Sneak Peek Hold'em

Key factors:
1. Information Value - How much does seeing opponent's card help us?
2. Information Leakage - What does our bid reveal about our hand?
3. Opponent Patterns - Track and exploit opponent bidding tendencies
4. Risk/Reward - Cost of info vs. benefit of making better decisions

Scenarios:
- WIN AUCTION: Pay second-highest bid, see one opponent card
- LOSE AUCTION: Opponent pays our bid, sees one of our cards  
- TIE: Both pay their bids, both see one card
"""

# Auction constants
AUCTION_MC_SIMS = 200  # Fewer sims for speed
MIN_BID_PCT = 0.0
MAX_BID_PCT = 1.0
AGGRESSION_MULTIPLIER = 1.3  # Be aggressive in bidding

def calculate_information_value(hole_cards: list, board: list, num_sims: int = AUCTION_MC_SIMS) -> float:
    """
    Calculate how valuable opponent card information is via Monte Carlo.
    
    Simulates: How much does our equity change on average when we know one opponent card?
    High variance in equity change = high information value
    """
    used = set(hole_cards + board)
    deck = [i for i in range(52) if i not in used]
    
    equity_deltas = []
    
    for _ in range(num_sims):
        random.shuffle(deck)
        
        # Complete board
        full_board = board + deck[:5 - len(board)]
        remaining_deck = deck[5 - len(board):]
        
        # Deal random opponent cards
        opp_card1, opp_card2 = remaining_deck[0], remaining_deck[1]
        
        # Calculate equity NOT knowing opponent cards
        unknown_equity = 0
        unknown_sims = 20
        for j in range(unknown_sims):
            sim_deck = [c for c in remaining_deck[2:]]
            random.shuffle(sim_deck)
            sim_opp = sim_deck[:2]
            
            my_score = evaluate_hand(hole_cards + full_board)
            opp_score = evaluate_hand(sim_opp + full_board)
            
            if my_score > opp_score:
                unknown_equity += 1
            elif my_score == opp_score:
                unknown_equity += 0.5
        unknown_equity /= unknown_sims
        
        # Calculate equity KNOWING one opponent card (opp_card1)
        known_equity = 0
        for j in range(unknown_sims):
            sim_deck = [c for c in remaining_deck[2:] if c != opp_card1]
            random.shuffle(sim_deck)
            sim_opp2 = sim_deck[0]  # Random second card
            
            my_score = evaluate_hand(hole_cards + full_board)
            opp_score = evaluate_hand([opp_card1, sim_opp2] + full_board)
            
            if my_score > opp_score:
                known_equity += 1
            elif my_score == opp_score:
                known_equity += 0.5
        known_equity /= unknown_sims
        
        # The delta tells us how much our decision quality improves
        equity_deltas.append(abs(known_equity - unknown_equity))
    
    # Average information value (0-1 scale)
    return sum(equity_deltas) / len(equity_deltas) if equity_deltas else 0.0

def get_hand_category(hole_cards: list) -> str:
    """Categorize hand strength for auction strategy."""
    c1, c2 = int_to_card(hole_cards[0]), int_to_card(hole_cards[1])
    r1, r2 = c1[0], c2[0]
    s1, s2 = c1[1], c2[1]
    
    high_rank = max(r1, r2)
    low_rank = min(r1, r2)
    suited = s1 == s2
    paired = r1 == r2
    
    # Premium hands (AA, KK, QQ, AKs, AKo)
    if paired and high_rank >= 10:  # QQ+
        return "premium"
    if high_rank == 12 and low_rank == 11:  # AK
        return "premium"
    
    # Strong hands (JJ-99, AQ, AJ, KQ)
    if paired and high_rank >= 7:  # 99+
        return "strong"
    if high_rank == 12 and low_rank >= 9:  # AT+
        return "strong"
    if high_rank == 11 and low_rank >= 10:  # KQ, KJ
        return "strong"
    
    # Medium hands (suited connectors, medium pairs, broadway)
    if paired and high_rank >= 4:  # 66+
        return "medium"
    if suited and abs(r1 - r2) <= 2 and low_rank >= 6:  # Suited connectors
        return "medium"
    if high_rank >= 9 and low_rank >= 8:  # Broadway
        return "medium"
    
    # Speculative hands (suited aces, small pairs, suited connectors)
    if suited and high_rank == 12:  # Suited ace
        return "speculative"
    if paired:  # Small pairs
        return "speculative"
    if suited and abs(r1 - r2) <= 2:  # Low suited connectors
        return "speculative"
    
    # Weak hands
    return "weak"

def calculate_bid_ev(our_bid: int, expected_opp_bid: int, info_value: float, pot: int) -> float:
    """
    Calculate expected value of a bid.
    
    If we win (our_bid > opp_bid): We pay opp_bid, gain info_value * pot
    If we lose (our_bid < opp_bid): Opponent pays our_bid, loses info_value * pot
    If tie: Both pay, both gain partial info
    """
    if pot == 0:
        return 0
    
    # Probability we win auction (simplified model)
    if our_bid > expected_opp_bid:
        win_prob = min(0.9, 0.5 + (our_bid - expected_opp_bid) / pot)
    else:
        win_prob = max(0.1, 0.5 - (expected_opp_bid - our_bid) / pot)
    
    # EV when we win: gain info value, pay second price
    ev_win = info_value * pot - expected_opp_bid
    
    # EV when we lose: opponent gains info on us (negative)
    ev_lose = -info_value * pot * 0.5  # They gain half info value on us
    
    return win_prob * ev_win + (1 - win_prob) * ev_lose

class AuctionTracker:
    """Track opponent bidding patterns across hands."""
    
    def __init__(self):
        self.opp_bids = []  # List of (bid_amount, pot_size) tuples
        self.opp_bid_pcts = []  # Bid as % of pot
        
    def record_opp_bid(self, bid: int, pot: int):
        """Record opponent's bid for pattern analysis."""
        if pot > 0:
            self.opp_bids.append((bid, pot))
            self.opp_bid_pcts.append(bid / pot)
    
    def get_expected_opp_bid_pct(self) -> float:
        """Estimate opponent's likely bid percentage based on history."""
        if len(self.opp_bid_pcts) < 3:
            return 0.25  # Default: assume 25% of pot
        
        # Use recent bids with more weight
        recent = self.opp_bid_pcts[-10:]
        if len(recent) >= 5:
            # Weighted average: recent bids count more
            weights = [1 + i * 0.2 for i in range(len(recent))]
            weighted_sum = sum(b * w for b, w in zip(recent, weights))
            return weighted_sum / sum(weights)
        
        return sum(recent) / len(recent)
    
    def is_opp_aggressive_bidder(self) -> bool:
        """Check if opponent bids aggressively (>40% pot on average)."""
        if len(self.opp_bid_pcts) < 5:
            return False
        return sum(self.opp_bid_pcts[-5:]) / 5 > 0.4
    
    def is_opp_passive_bidder(self) -> bool:
        """Check if opponent bids passively (<15% pot on average)."""
        if len(self.opp_bid_pcts) < 5:
            return False
        return sum(self.opp_bid_pcts[-5:]) / 5 < 0.15

def compute_auction_bid(hole_cards: list, board: list, pot: int, my_chips: int,
                        auction_tracker: AuctionTracker, 
                        base_equity: float = None) -> int:
    """
    Compute optimal auction bid using Monte Carlo and game theory.
    
    Strategy:
    1. Calculate information value (how much does seeing card help?)
    2. Adjust for hand category (medium hands benefit most from info)
    3. Consider opponent patterns (exploit passive/aggressive bidders)
    4. Apply aggression multiplier (bid slightly above value)
    
    Returns bid amount in chips.
    """
    if pot == 0:
        return 0
    
    # Calculate base equity if not provided
    if base_equity is None:
        base_equity = calculate_equity(hole_cards, board, MC_SIMULATIONS)
    
    # Calculate information value via Monte Carlo
    info_value = calculate_information_value(hole_cards, board)
    
    # Get hand category
    hand_cat = get_hand_category(hole_cards)
    
    # Base bid percentage based on info value (0-100% of pot)
    base_bid_pct = info_value * 2  # Scale: 0.5 info_value -> 100% pot
    
    # Adjust for hand category
    # Medium hands benefit MOST from information (high uncertainty)
    # Premium/weak hands benefit LESS (outcome more certain)
    category_multipliers = {
        "premium": 0.6,      # We're likely ahead, less info needed
        "strong": 0.8,       # Good but info still valuable
        "medium": 1.2,       # Maximum info value - high uncertainty
        "speculative": 1.1,  # Info helps us know when to continue
        "weak": 0.5          # Probably folding anyway
    }
    bid_pct = base_bid_pct * category_multipliers.get(hand_cat, 1.0)
    
    # Adjust for opponent patterns
    expected_opp_pct = auction_tracker.get_expected_opp_bid_pct()
    
    if auction_tracker.is_opp_passive_bidder():
        # Opponent bids low - we can win cheaply, bid just above their expected
        bid_pct = max(bid_pct * 0.8, expected_opp_pct + 0.05)
    elif auction_tracker.is_opp_aggressive_bidder():
        # Opponent bids high - either outbid significantly or concede
        if hand_cat in ["premium", "strong", "medium"]:
            bid_pct = min(bid_pct * 1.3, 1.0)  # Go aggressive
        else:
            bid_pct = bid_pct * 0.5  # Concede with weak hands
    
    # Apply aggression multiplier (always bid slightly above neutral)
    bid_pct *= AGGRESSION_MULTIPLIER
    
    # Clamp to valid range
    bid_pct = max(MIN_BID_PCT, min(MAX_BID_PCT, bid_pct))
    
    # Convert to chips
    bid_amount = int(bid_pct * pot)
    
    # Don't bid more than we have
    bid_amount = min(bid_amount, my_chips)
    
    # Minimum viable bid (at least 1 chip if we're bidding)
    if bid_amount > 0:
        bid_amount = max(1, bid_amount)
    
    return bid_amount

def adjust_strategy_after_auction(won_auction: bool, my_bid: int, opp_bid: int,
                                   opp_revealed_card: int = None) -> dict:
    """
    Return strategy adjustments to make after auction resolves.
    
    If we WON auction:
    - We paid opp_bid (second-price)
    - We see one of opponent's cards
    - Adjust ranges based on what we see
    
    If we LOST auction:
    - Opponent paid our bid
    - Opponent sees one of our cards
    - Opponent has info advantage - play tighter
    """
    adjustments = {
        "play_tighter": False,
        "play_looser": False,
        "bluff_less": False,
        "value_bet_thinner": False,
        "opp_likely_range": None
    }
    
    if won_auction:
        # We have info advantage
        adjustments["value_bet_thinner"] = True  # Can bet marginal hands
        
        if opp_revealed_card is not None:
            opp_rank = opp_revealed_card // 4
            # High card revealed -> opponent likely has strong range
            if opp_rank >= 10:  # J, Q, K, A
                adjustments["opp_likely_range"] = "strong"
                adjustments["play_tighter"] = True
            elif opp_rank <= 5:  # 2-7
                adjustments["opp_likely_range"] = "weak_or_pair"
                adjustments["play_looser"] = True
                adjustments["value_bet_thinner"] = True
            else:
                adjustments["opp_likely_range"] = "medium"
    
    else:
        # Opponent has info advantage on us
        adjustments["bluff_less"] = True  # They can call lighter
        adjustments["play_tighter"] = True  # Reduce marginal spots
        
        # If opponent bid high, they valued the info (medium hand?)
        if opp_bid > my_bid * 2:
            adjustments["opp_likely_range"] = "uncertain_medium"
    
    return adjustments

# =============================================================================
# INFORMATION SET KEY GENERATION
# =============================================================================
def get_info_set_key(state: PokerState, action_history: str, is_bb: bool, has_info_advantage: bool) -> str:
    """
    Build information set key matching offline.cpp format:
    {street}_{equity_bucket}_{pot_bucket}_{position}_{action_history}_{has_info}
    """
    # Street
    street = STREET_MAP.get(state.street, 0)
    
    # Convert cards to integers
    hole_cards = [card_to_int(c) for c in state.my_hand]
    
    # Get visible board based on street
    visible_board = []
    if state.street in ['flop', 'auction']:
        visible_board = [card_to_int(c) for c in state.board[:3]] if len(state.board) >= 3 else []
    elif state.street == 'turn':
        visible_board = [card_to_int(c) for c in state.board[:4]] if len(state.board) >= 4 else []
    elif state.street == 'river':
        visible_board = [card_to_int(c) for c in state.board]
    
    # Calculate equity and bucket
    equity = calculate_equity(hole_cards, visible_board)
    
    if state.street == 'pre-flop':
        num_buckets = PREFLOP_BUCKETS
    elif state.street in ['auction', 'flop']:
        num_buckets = FLOP_BUCKETS
    elif state.street == 'turn':
        num_buckets = TURN_BUCKETS
    else:
        num_buckets = RIVER_BUCKETS
    
    equity_bucket = equity_to_bucket(equity, num_buckets)
    
    # Pot bucket
    pot_bucket = state.pot // (STARTING_STACK // 10)  # pot / 500
    
    # Position: 0 = SB/OOP, 1 = BB/IP
    position = 1 if is_bb else 0
    
    # Has info advantage (won auction)
    has_info = 1 if has_info_advantage else 0
    
    return f"{street}_{equity_bucket}_{pot_bucket}_{position}_{action_history}_{has_info}"

# =============================================================================
# ACTION ENCODING (must match offline.cpp)
# =============================================================================
# Betting actions: 0=fold, 1=check/call, 2=half pot raise, 3=pot raise, 4=all-in
# Auction actions: 0=0%, 1=25%, 2=50%, 3=75%, 4=100% of pot

def encode_action(action, state: PokerState) -> str:
    """Convert an action to its numeric code for action history."""
    if isinstance(action, ActionFold):
        return "0"
    elif isinstance(action, (ActionCheck, ActionCall)):
        return "1"
    elif isinstance(action, ActionBid):
        # Bid as percentage of pot
        pot = state.pot
        if pot == 0:
            return "0"
        pct = action.amount / pot
        if pct <= 0.125:
            return "0"
        elif pct <= 0.375:
            return "1"
        elif pct <= 0.625:
            return "2"
        elif pct <= 0.875:
            return "3"
        else:
            return "4"
    elif isinstance(action, ActionRaise):
        # Raise size relative to pot
        pot = state.pot
        raise_amt = action.amount - state.my_wager
        if state.my_chips - raise_amt <= 0:  # All-in
            return "4"
        elif pot > 0:
            ratio = raise_amt / pot
            if ratio <= 0.75:
                return "2"  # Half pot
            else:
                return "3"  # Pot
        return "3"
    return "1"  # Default to check/call

# =============================================================================
# ACTION TRANSLATION & GUARDRAILS
# =============================================================================
"""
This module ensures the bot ALWAYS returns a valid action.

Rules from the engine:
1. legal_actions is a set of action CLASSES (not instances)
2. Use state.can_act(ActionClass) to check legality
3. ActionRaise(amount) - amount is TOTAL wager (raise TO), not raise BY
4. raise_bounds = (min_raise_to, max_raise_to) 
5. Bids must be 0 <= bid <= my_chips
6. Invalid actions cause automatic fold

Key formulas from rules:
- Minimum Raise TO = Current Wager + Cost to Call + max(Big Blind, Cost to Call)
- Maximum Raise TO = min(Your Chips + Your Wager, Opponent's Chips + Opponent's Wager)
"""

BIG_BLIND = 20

def validate_and_fix_action(action, state: PokerState):
    """
    Validate an action and fix it if invalid.
    Returns a guaranteed valid action.
    """
    # Terminal state - shouldn't happen but handle it
    if state.is_terminal:
        return ActionCheck() if state.can_act(ActionCheck) else ActionFold()
    
    # Handle auction separately
    if state.street == 'auction':
        if isinstance(action, ActionBid):
            # Clamp bid to valid range [0, my_chips]
            bid = max(0, min(action.amount, state.my_chips))
            return ActionBid(int(bid))
        else:
            # Non-bid action in auction - return 0 bid
            return ActionBid(0)
    
    # Validate betting actions
    if isinstance(action, ActionFold):
        if state.can_act(ActionFold):
            return action
        # Can't fold when there's nothing to call - check instead
        if state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()  # Should be legal if we reach here
    
    if isinstance(action, ActionCheck):
        if state.can_act(ActionCheck):
            return action
        # Can't check - try call
        if state.can_act(ActionCall):
            return ActionCall()
        return ActionFold()
    
    if isinstance(action, ActionCall):
        if state.can_act(ActionCall):
            return action
        # Can't call - try check
        if state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()
    
    if isinstance(action, ActionRaise):
        if state.can_act(ActionRaise):
            min_raise, max_raise = state.raise_bounds
            # Clamp to valid range
            amount = max(min_raise, min(action.amount, max_raise))
            # Must be an integer
            amount = int(amount)
            return ActionRaise(amount)
        # Can't raise - try call/check
        if state.can_act(ActionCall):
            return ActionCall()
        if state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()
    
    # Unknown action type - return safest option
    if state.can_act(ActionCheck):
        return ActionCheck()
    if state.can_act(ActionCall):
        return ActionCall()
    return ActionFold()

def get_safe_raise(state: PokerState, target_size: str) -> ActionRaise | ActionCall | ActionCheck | ActionFold:
    """
    Get a valid raise action for target size, with fallbacks.
    target_size: 'min', 'half_pot', 'pot', 'all_in', or specific amount
    """
    if not state.can_act(ActionRaise):
        # Can't raise - fall back
        if state.can_act(ActionCall):
            return ActionCall()
        if state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()
    
    min_raise, max_raise = state.raise_bounds
    pot = state.pot
    
    if target_size == 'min':
        amount = min_raise
    elif target_size == 'half_pot':
        # Half pot raise = current wager + cost_to_call + pot/2
        amount = state.my_wager + state.cost_to_call + (pot // 2)
    elif target_size == 'pot':
        # Pot raise = current wager + cost_to_call + pot
        amount = state.my_wager + state.cost_to_call + pot
    elif target_size == 'all_in':
        amount = max_raise
    else:
        # Specific amount
        amount = int(target_size) if isinstance(target_size, (int, float)) else min_raise
    
    # Clamp to valid range
    amount = max(min_raise, min(amount, max_raise))
    return ActionRaise(int(amount))

def get_safe_bid(state: PokerState, bid_amount: int) -> ActionBid:
    """
    Get a valid bid action, clamped to valid range.
    """
    bid = max(0, min(bid_amount, state.my_chips))
    return ActionBid(int(bid))

def action_from_strategy_index(strategy_index: int, state: PokerState):
    """
    Convert CFR strategy action index to actual action.
    
    For betting: 0=fold, 1=check/call, 2=half pot, 3=pot, 4=all-in
    For auction: 0=0%, 1=25%, 2=50%, 3=75%, 4=100% of pot
    """
    if state.street == 'auction':
        # Auction bid percentages
        pot = state.pot
        bid_pcts = [0.0, 0.25, 0.50, 0.75, 1.0]
        pct = bid_pcts[min(strategy_index, 4)]
        bid_amount = int(pct * pot)
        return get_safe_bid(state, bid_amount)
    
    # Betting actions
    if strategy_index == 0:
        # Fold
        if state.can_act(ActionFold):
            return ActionFold()
        return ActionCheck() if state.can_act(ActionCheck) else ActionCall()
    
    elif strategy_index == 1:
        # Check/Call
        if state.can_act(ActionCheck):
            return ActionCheck()
        if state.can_act(ActionCall):
            return ActionCall()
        return ActionFold()
    
    elif strategy_index == 2:
        # Half pot raise
        return get_safe_raise(state, 'half_pot')
    
    elif strategy_index == 3:
        # Pot raise
        return get_safe_raise(state, 'pot')
    
    elif strategy_index == 4:
        # All-in
        return get_safe_raise(state, 'all_in')
    
    # Default: check/call
    if state.can_act(ActionCheck):
        return ActionCheck()
    if state.can_act(ActionCall):
        return ActionCall()
    return ActionFold()

def sample_action_from_strategy(strategy: list, state: PokerState):
    """
    Sample an action from a probability distribution over actions.
    strategy: list of probabilities [p_fold, p_check/call, p_half_pot, p_pot, p_all_in]
    or for auction: [p_0%, p_25%, p_50%, p_75%, p_100%]
    
    Returns a valid action.
    """
    if not strategy or len(strategy) == 0:
        # No strategy - return safe default
        if state.street == 'auction':
            return get_safe_bid(state, state.pot // 4)
        if state.can_act(ActionCheck):
            return ActionCheck()
        if state.can_act(ActionCall):
            return ActionCall()
        return ActionFold()
    
    # Normalize strategy (in case it doesn't sum to 1)
    total = sum(strategy)
    if total == 0:
        # Uniform distribution
        strategy = [1.0 / len(strategy)] * len(strategy)
    else:
        strategy = [p / total for p in strategy]
    
    # Sample action
    r = random.random()
    cumulative = 0.0
    chosen_index = len(strategy) - 1
    
    for i, prob in enumerate(strategy):
        cumulative += prob
        if r < cumulative:
            chosen_index = i
            break
    
    return action_from_strategy_index(chosen_index, state)

def get_default_action(state: PokerState, equity: float = None):
    """
    Return a reasonable default action when no strategy is available.
    Uses simple equity-based logic.
    """
    if state.street == 'auction':
        # Default auction: bid 25% of pot
        return get_safe_bid(state, state.pot // 4)
    
    # Calculate equity if not provided
    if equity is None:
        hole_cards = [card_to_int(c) for c in state.my_hand]
        board = [card_to_int(c) for c in state.board]
        equity = calculate_equity(hole_cards, board, MC_SIMULATIONS // 2)
    
    # If we can check, check with weak hands
    if state.can_act(ActionCheck):
        if equity < 0.6:
            return ActionCheck()
        # Strong hand - bet
        if state.can_act(ActionRaise):
            return get_safe_raise(state, 'half_pot')
        return ActionCheck()
    
    # Facing a bet - calculate pot odds
    if state.cost_to_call > 0:
        pot_odds = state.cost_to_call / (state.pot + state.cost_to_call)
        
        # Need better equity than pot odds to call
        if equity > pot_odds + 0.05:  # 5% margin
            if state.can_act(ActionCall):
                # Consider raising with strong hands
                if equity > 0.7 and state.can_act(ActionRaise):
                    return get_safe_raise(state, 'half_pot')
                return ActionCall()
        
        # Not enough equity - fold
        if state.can_act(ActionFold):
            return ActionFold()
    
    # Fallback
    if state.can_act(ActionCall):
        return ActionCall()
    if state.can_act(ActionCheck):
        return ActionCheck()
    return ActionFold()


class MCCFRBot(BaseBot):
    '''
    MCCFR (Monte Carlo Counterfactual Regret Minimization) Poker Bot.
    Uses precomputed CFR strategies from offline training.
    '''

    def __init__(self):
        self.action_history = ""
        self.has_info_advantage = False
        self.last_street = None
        
        # Auction tracking
        self.auction_tracker = AuctionTracker()
        self.my_last_bid = 0
        self.opp_last_bid = 0
        self.auction_adjustments = None
        self.cached_equity = None  # Cache equity for current hand
        
        # Track revealed card info
        self.opp_revealed_card_int = None
        
        # Track pot at auction for opponent bid inference
        self.pot_at_auction = 0

    def on_hand_start(self, game_info: GameInfo, current_state: PokerState) -> None:
        '''Called when a new hand starts. Reset tracking variables.'''
        self.action_history = ""
        self.has_info_advantage = False
        self.last_street = "pre-flop"
        self.my_last_bid = 0
        self.opp_last_bid = 0
        self.auction_adjustments = None
        self.cached_equity = None
        self.opp_revealed_card_int = None
        self.pot_at_auction = 0

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        '''Called when a hand ends. Record opponent's auction behavior.'''
        # Record opponent bid if we can infer it
        # If we won auction (have info advantage), we paid opp's bid (second-price)
        # If we lost auction, opponent paid our bid
        if self.pot_at_auction > 0:
            if self.has_info_advantage:
                # We won - we don't know exact opp bid, but it was <= our bid
                # Estimate as 50-75% of our bid
                estimated_opp_bid = int(self.my_last_bid * 0.6)
                self.auction_tracker.record_opp_bid(estimated_opp_bid, self.pot_at_auction)
            elif not self.has_info_advantage and self.opp_revealed_card_int is None:
                # Neither saw cards - tie or both bid 0
                # This is harder to infer, assume similar to our bid
                self.auction_tracker.record_opp_bid(self.my_last_bid, self.pot_at_auction)
    
    def _update_info_advantage(self, state: PokerState) -> None:
        """Check if we won the auction and have info advantage."""
        if state.opp_revealed_cards and len(state.opp_revealed_cards) > 0:
            self.has_info_advantage = True
            # Convert revealed card to int for strategy adjustment
            if self.opp_revealed_card_int is None:
                self.opp_revealed_card_int = card_to_int(state.opp_revealed_cards[0])
                # Calculate auction adjustments now that we know result
                self.auction_adjustments = adjust_strategy_after_auction(
                    won_auction=True,
                    my_bid=self.my_last_bid,
                    opp_bid=self.opp_last_bid,
                    opp_revealed_card=self.opp_revealed_card_int
                )

    def _reset_action_history_on_new_street(self, state: PokerState) -> None:
        """Reset action history when moving to a new street."""
        if state.street != self.last_street:
            # If we just left auction without seeing opponent's card, we lost
            if self.last_street == "auction" and not self.has_info_advantage:
                self.auction_adjustments = adjust_strategy_after_auction(
                    won_auction=False,
                    my_bid=self.my_last_bid,
                    opp_bid=self.opp_last_bid
                )
            self.last_street = state.street

    def _get_auction_bid(self, state: PokerState) -> ActionBid:
        """
        Compute aggressive auction bid using Monte Carlo and opponent modeling.
        """
        # Convert hole cards to integers
        hole_cards = [card_to_int(c) for c in state.my_hand]
        
        # Get flop cards (auction happens after flop is dealt)
        board = [card_to_int(c) for c in state.board[:3]] if len(state.board) >= 3 else []
        
        # Save pot at auction time for later bid inference
        self.pot_at_auction = state.pot
        
        # Calculate equity with the flop (not preflop!)
        flop_equity = calculate_equity(hole_cards, board, MC_SIMULATIONS)
        self.cached_equity = flop_equity  # Cache for later decisions
        
        # Compute optimal bid
        bid_amount = compute_auction_bid(
            hole_cards=hole_cards,
            board=board,
            pot=state.pot,
            my_chips=state.my_chips,
            auction_tracker=self.auction_tracker,
            base_equity=flop_equity
        )
        
        self.my_last_bid = bid_amount
        # Validate bid before returning
        return get_safe_bid(state, bid_amount)

    def get_move(self, game_info: GameInfo, current_state: PokerState) -> ActionFold | ActionCall | ActionCheck | ActionRaise | ActionBid:
        '''
        Returns the bot's action based on CFR strategy lookup.
        
        GUARANTEED to return a valid action - uses guardrails to validate
        and fix any invalid actions before returning.
        '''
        try:
            self._update_info_advantage(current_state)
            self._reset_action_history_on_new_street(current_state)
            
            # Handle auction with sophisticated strategy
            if current_state.street == 'auction':
                return self._get_auction_bid(current_state)
            
            # Calculate equity for decision making
            hole_cards = [card_to_int(c) for c in current_state.my_hand]
            board = [card_to_int(c) for c in current_state.board]
            equity = calculate_equity(hole_cards, board, MC_SIMULATIONS // 2)
            
            # Build info set key for strategy lookup
            key = get_info_set_key(
                current_state,
                self.action_history,
                current_state.is_bb,
                self.has_info_advantage
            )
            
            # Try to use CFR strategy if available
            if STRATEGY and key in STRATEGY:
                strategy = STRATEGY[key]
                action = sample_action_from_strategy(strategy, current_state)
                action = validate_and_fix_action(action, current_state)
                self.action_history += encode_action(action, current_state)
                return action
            
            # No strategy found - use equity-based fallback with auction adjustments
            action = self._get_equity_based_action(current_state, equity)
            
            # GUARDRAIL: Validate and fix action before returning
            action = validate_and_fix_action(action, current_state)
            
            # Update action history
            self.action_history += encode_action(action, current_state)
            
            return action
            
        except Exception as e:
            # ULTIMATE FALLBACK: Never crash, always return valid action
            # This catches any unexpected errors
            return self._get_safe_fallback_action(current_state)
    
    def _get_equity_based_action(self, state: PokerState, equity: float):
        """
        Get action based on equity and auction adjustments.
        Returns a preliminary action (will be validated by guardrails).
        """
        # Apply auction adjustments to strategy
        tighter = False
        looser = False
        bluff_less = False
        
        if self.auction_adjustments:
            adj = self.auction_adjustments
            tighter = adj.get("play_tighter", False)
            looser = adj.get("play_looser", False)
            bluff_less = adj.get("bluff_less", False)
        
        # Adjust equity thresholds based on auction result
        call_threshold_bonus = 0.1 if tighter else (-0.05 if looser else 0)
        bet_threshold_bonus = 0.1 if bluff_less else 0
        
        # No bet to face - can check or bet
        if state.cost_to_call == 0:
            if state.can_act(ActionCheck):
                # Strong hand - bet for value
                if equity > 0.65 + bet_threshold_bonus and state.can_act(ActionRaise):
                    if equity > 0.85:
                        return get_safe_raise(state, 'pot')
                    return get_safe_raise(state, 'half_pot')
                # Medium hand - sometimes bet as bluff (unless bluff_less)
                if not bluff_less and equity > 0.45 and equity < 0.55:
                    if random.random() < 0.3 and state.can_act(ActionRaise):
                        return get_safe_raise(state, 'half_pot')
                return ActionCheck()
            # Can't check - try call, then fold
            if state.can_act(ActionCall):
                return ActionCall()
            return ActionFold()
        
        # Facing a bet - calculate pot odds
        pot_odds = state.cost_to_call / (state.pot + state.cost_to_call)
        
        # Strong hand - raise
        if equity > 0.75 + call_threshold_bonus:
            if state.can_act(ActionRaise):
                if equity > 0.85:
                    return get_safe_raise(state, 'pot')
                return get_safe_raise(state, 'half_pot')
            if state.can_act(ActionCall):
                return ActionCall()
        
        # Decent hand - call if good pot odds
        if equity > pot_odds + call_threshold_bonus:
            if state.can_act(ActionCall):
                return ActionCall()
        
        # Weak hand - fold
        if state.can_act(ActionFold):
            return ActionFold()
        
        # Fallback
        if state.can_act(ActionCheck):
            return ActionCheck()
        if state.can_act(ActionCall):
            return ActionCall()
        return ActionFold()
    
    def _get_safe_fallback_action(self, state: PokerState):
        """
        Ultimate fallback - returns a valid action no matter what.
        Used when an exception occurs.
        """
        if state.street == 'auction':
            # Safe auction bid: 0 chips
            return ActionBid(0)
        
        # Try actions in order of safety
        if state.can_act(ActionCheck):
            return ActionCheck()
        if state.can_act(ActionCall):
            return ActionCall()
        if state.can_act(ActionFold):
            return ActionFold()
        
        # This should never happen, but just in case
        return ActionFold()


# =============================================================================
# PLAYER ALIAS AND MAIN ENTRY POINT
# =============================================================================
# Alias for compatibility with engine (expects Player class)
Player = MCCFRBot


if __name__ == "__main__":
    from pkbot.runner import parse_args, run_bot
    run_bot(Player(), parse_args())
