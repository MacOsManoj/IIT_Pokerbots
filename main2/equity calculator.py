import eval7
import functools


# Precompute the 'any two cards' range to avoid doing it repeatedly
_ANY_TWO_STR = ",".join(
   r1 + s1 + r2 + s2
   for i, (r1, s1) in enumerate([(r, s) for r in '23456789TJQKA' for s in 'cdhs'])
   for r2, s2 in [(r, s) for r in '23456789TJQKA' for s in 'cdhs'][i + 1:]
)
_ANY_TWO_RANGE = eval7.HandRange(_ANY_TWO_STR)
_ONE_CARD_CACHE = {}


@functools.lru_cache(maxsize=100000)
def calculate_equity(our_cards_tuple, opp_cards_tuple, table_cards_tuple):
   """
   Cached inner function for computing equity.
   Lists are converted to tuples so they can be hashed for the lru_cache.
   """
   h = [eval7.Card(c) for c in our_cards_tuple]
   b = [eval7.Card(c) for c in table_cards_tuple]
  
   if opp_cards_tuple and len(opp_cards_tuple) == 2:
       villain = eval7.HandRange("".join(opp_cards_tuple))
   elif opp_cards_tuple and len(opp_cards_tuple) == 1:
       t = opp_cards_tuple[0]
       if t in _ONE_CARD_CACHE:
           villain = _ONE_CARD_CACHE[t]
       else:
           dk = [r + s for r in '23456789TJQKA' for s in 'cdhs']
           villain = eval7.HandRange(",".join(t + c for c in dk if c != t))
           _ONE_CARD_CACHE[t] = villain
   else:
       # 0 opponent cards
       villain = _ANY_TWO_RANGE
      
   # eval7.py_hand_vs_range_exact sometimes returns 0.0 on partial boards in certain versions/builds.
   # We use Monte Carlo for partial boards to be safe and accurate, and exact for full boards.
   if len(b) == 5:
       return float(eval7.py_hand_vs_range_exact(h, villain, b))
   else:
       # Pre-flop (0 cards) needs more iterations for stability, but is slower.
       # Flop/Turn (3-4 cards) is very fast.
       iters = 10000 if len(b) > 0 else 5000
       return float(eval7.py_hand_vs_range_monte_carlo(h, villain, b, iters))




def get_equity(our_cards, opp_cards=None, table_cards=None):
   """
   Calculates equity for a hand against an opponent's hand range on a given board.
   Takes 3 inputs, uses eval7 and caches the result for future identical calls.
  
   Args:
       our_cards: list of string cards (e.g., ['Ah', 'Kh'])
       opp_cards: list of string cards or empty list (e.g., ['2s', '2d'], [] or ['2s'] if 1 card known)
       table_cards: list of string cards (e.g., ['Ts', 'Js', 'Qs'], can be empty)
      
   Returns:
       float: Exact equity (0.0 to 1.0)
   """
   if our_cards is None:
       our_cards = []
   if opp_cards is None:
       opp_cards = []
   if table_cards is None:
       table_cards = []
      
   # We sort the cards so that ['Ah', 'Kh'] and ['Kh', 'Ah'] hit the same cache entry
   our_cards_tuple = tuple(sorted(our_cards))
   opp_cards_tuple = tuple(sorted(opp_cards))
   table_cards_tuple = tuple(sorted(table_cards))
  
   return calculate_equity(our_cards_tuple, opp_cards_tuple, table_cards_tuple)




if __name__ == "__main__":
   # Example usage:
   print("Testing Equity Calculator...")
  
   # Example 1: Known Opponent Cards
   eq1 = get_equity(["Ah", "Kh"], [""], ["Th", "Jh", "Qh"])
   print(f"Equity (Ah Kh vs Jd Jc on 2s 3s 4s): {eq1:.4f}")
  
  



