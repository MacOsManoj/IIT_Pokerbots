"""
Bayesian Opponent Modeling Module

Implements real-time opponent profiling through:
1. Action Frequency Tracking (36 categories: 3 actions × 3 costs × 4 streets)
2. Probability Triples (f, c, r) for fold/call/raise likelihoods
3. Bayesian Range Updating via P(Hand|Action) inference
4. Weighted Monte Carlo sampling exploiting opponent leaks

Based on Billings et al. opponent modeling research.
"""

import eval7
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

RV = {r: i for i, r in enumerate('23456789TJQKA', 2)}
DECK = [eval7.Card(r + s) for r in '23456789TJQKA' for s in 'cdhs']

PFE = {
    "AA":.852,"KK":.824,"QQ":.799,"JJ":.774,"TT":.750,
    "99":.720,"88":.692,"77":.663,"66":.633,"55":.603,
    "44":.570,"33":.537,"22":.503,
    "AKs":.670,"AQs":.662,"AJs":.654,"ATs":.646,"A9s":.628,
    "A8s":.620,"A7s":.610,"A6s":.599,"A5s":.599,"A4s":.590,
    "A3s":.582,"A2s":.574,
    "AKo":.653,"AQo":.644,"AJo":.636,"ATo":.627,"A9o":.608,
    "A8o":.599,"A7o":.588,"A6o":.577,"A5o":.577,"A4o":.567,
    "A3o":.558,"A2o":.549,
    "KQs":.634,"KJs":.625,"KTs":.618,"K9s":.600,"K8s":.583,
    "K7s":.575,"K6s":.566,"K5s":.558,"K4s":.549,"K3s":.541,"K2s":.532,
    "KQo":.615,"KJo":.606,"KTo":.598,"K9o":.578,"K8o":.560,
    "K7o":.552,"K6o":.542,"K5o":.533,"K4o":.523,"K3o":.514,"K2o":.505,
    "QJs":.603,"QTs":.595,"Q9s":.577,"Q8s":.560,"Q7s":.543,
    "Q6s":.536,"Q5s":.528,"Q4s":.518,"Q3s":.510,"Q2s":.502,
    "QJo":.581,"QTo":.573,"Q9o":.554,"Q8o":.536,"Q7o":.518,
    "Q6o":.510,"Q5o":.502,"Q4o":.491,"Q3o":.482,"Q2o":.473,
    "JTs":.575,"J9s":.557,"J8s":.540,"J7s":.523,"J6s":.506,
    "J5s":.500,"J4s":.491,"J3s":.482,"J2s":.474,
    "JTo":.552,"J9o":.533,"J8o":.515,"J7o":.497,"J6o":.478,
    "J5o":.471,"J4o":.462,"J3o":.453,"J2o":.443,
    "T9s":.540,"T8s":.523,"T7s":.506,"T6s":.489,"T5s":.472,
    "T4s":.466,"T3s":.457,"T2s":.448,
    "T9o":.515,"T8o":.497,"T7o":.479,"T6o":.461,"T5o":.443,
    "T4o":.435,"T3o":.426,"T2o":.417,
    "98s":.508,"97s":.491,"96s":.474,"95s":.457,"94s":.439,
    "93s":.433,"92s":.424,
    "98o":.481,"97o":.463,"96o":.445,"95o":.427,"94o":.406,
    "93o":.400,"92o":.391,
    "87s":.479,"86s":.462,"85s":.445,"84s":.427,"83s":.409,"82s":.403,
    "87o":.451,"86o":.433,"85o":.414,"84o":.394,"83o":.375,"82o":.368,
    "76s":.454,"75s":.437,"74s":.419,"73s":.400,"72s":.382,
    "76o":.423,"75o":.405,"74o":.386,"73o":.366,"72o":.346,
    "65s":.431,"64s":.414,"63s":.395,"62s":.377,
    "65o":.400,"64o":.380,"63o":.361,"62o":.341,
    "54s":.414,"53s":.397,"52s":.379,
    "54o":.382,"53o":.363,"52o":.343,
    "43s":.386,"42s":.368,"43o":.351,"42o":.332,
    "32s":.360,"32o":.323,
}


def hk(h: List[str]) -> str:
    """Convert hand to canonical key (e.g., ['Ah','Kd'] -> 'AKo')"""
    if len(h) != 2:
        return ""
    r0, s0, r1, s1 = h[0][0], h[0][1], h[1][0], h[1][1]
    v0, v1 = RV[r0], RV[r1]
    if v0 < v1:
        r0, r1, s0, s1 = r1, r0, s1, s0
    if v0 == v1:
        return r0 + r1
    return r0 + r1 + ('s' if s0 == s1 else 'o')


def all_starting_hands() -> List[Tuple[str, str]]:
    """Generate all 1326 starting hand combinations"""
    hands = []
    for i in range(52):
        for j in range(i+1, 52):
            c1 = DECK[i]
            c2 = DECK[j]
            hands.append((str(c1), str(c2)))
    return hands


class OpponentModel:
    """
    Bayesian opponent model tracking action frequencies and updating hand ranges.
    """
    
    def __init__(self):
        self.action_counts = defaultdict(lambda: {'fold': 0, 'call': 0, 'raise': 0})
        self.total_observations = 0
        self.hands_played = 0
        self.showdown_hands = []
        
    def _state_key(self, street: str, cost_category: str) -> str:
        """
        Create state key for 36-category system.
        Streets: preflop, flop, turn, river
        Cost categories: '0bb', '1bb', '2+bb'
        """
        return f"{street}_{cost_category}"
    
    def _cost_category(self, cost_to_call: int, pot: int) -> str:
        """Discretize cost into categories"""
        if cost_to_call == 0:
            return '0bb'
        bb_cost = cost_to_call / 20
        if bb_cost <= 1.5:
            return '1bb'
        return '2+bb'
    
    def observe_action(self, action: str, street: str, cost_to_call: int, pot: int):
        """
        Record opponent action into frequency matrix.
        action: 'fold', 'call', 'check', 'raise'
        """
        if action == 'check':
            action = 'call'
        
        cost_cat = self._cost_category(cost_to_call, pot)
        key = self._state_key(street, cost_cat)
        self.action_counts[key][action] += 1
        self.total_observations += 1
    
    def get_probability_triple(self, street: str, cost_to_call: int, pot: int) -> Tuple[float, float, float]:
        """
        Return (fold_prob, call_prob, raise_prob) for the given state.
        If insufficient data, use global baseline.
        """
        cost_cat = self._cost_category(cost_to_call, pot)
        key = self._state_key(street, cost_cat)
        counts = self.action_counts[key]
        total = sum(counts.values())
        
        if total < 5:
            if cost_to_call == 0:
                return (0.05, 0.70, 0.25)
            return (0.35, 0.50, 0.15)
        
        f = counts['fold'] / total
        c = counts['call'] / total
        r = counts['raise'] / total
        return (f, c, r)
    
    def get_aggression_factor(self) -> float:
        """Ratio of raises to total actions"""
        total_raises = sum(c['raise'] for c in self.action_counts.values())
        if self.total_observations < 10:
            return 0.25
        return total_raises / self.total_observations
    
    def get_vpip(self) -> float:
        """Voluntarily put money in pot (preflop call/raise rate)"""
        pf_keys = [k for k in self.action_counts.keys() if k.startswith('preflop') or k.startswith('pre-flop')]
        total = sum(sum(self.action_counts[k].values()) for k in pf_keys)
        if total < 5:
            return 0.50
        voluntary = sum(self.action_counts[k]['call'] + self.action_counts[k]['raise'] for k in pf_keys)
        return voluntary / total
    
    def classify_opponent(self) -> str:
        """
        Classify opponent into archetypal player types:
        - nit: tight-passive (low VPIP, low aggression)
        - lag: loose-aggressive (high VPIP, high aggression)
        - tag: tight-aggressive (low VPIP, high aggression)
        - fish: loose-passive (high VPIP, low aggression)
        """
        if self.total_observations < 20:
            return 'unknown'
        
        vpip = self.get_vpip()
        agg = self.get_aggression_factor()
        
        if vpip < 0.35 and agg < 0.25:
            return 'nit'
        if vpip > 0.55 and agg > 0.35:
            return 'lag'
        if vpip < 0.40 and agg > 0.30:
            return 'tag'
        if vpip > 0.50 and agg < 0.25:
            return 'fish'
        return 'reg'
    
    def update_range_bayesian(self, action: str, street: str, cost_to_call: int, pot: int,
                              prior_weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """
        Bayesian update of opponent hand range.
        
        P(Hand|Action) = P(Action|Hand) * P(Hand) / P(Action)
        
        Returns: Dictionary mapping hand keys to posterior probabilities
        """
        if prior_weights is None:
            all_hands = list(PFE.keys())
            prior_weights = {h: 1.0/len(all_hands) for h in all_hands}
        
        f_prob, c_prob, r_prob = self.get_probability_triple(street, cost_to_call, pot)
        
        action_probs = {'fold': f_prob, 'call': c_prob, 'raise': r_prob}
        if action == 'check':
            action = 'call'
        
        base_prob = action_probs[action]
        
        posterior = {}
        for hand_key, prior in prior_weights.items():
            hand_eq = PFE.get(hand_key, 0.40)
            
            if action == 'fold':
                likelihood = max(0.01, 1.0 - hand_eq ** 1.5)
            elif action == 'call':
                likelihood = max(0.1, 1.0 - abs(hand_eq - 0.5) * 2)
            else:
                likelihood = max(0.01, hand_eq ** 1.8)
            
            posterior[hand_key] = likelihood * prior
        
        total = sum(posterior.values())
        if total > 0:
            for k in posterior:
                posterior[k] /= total
        
        return posterior
    
    def weighted_monte_carlo(self, my_hand: List[str], board: List[str], 
                           hand_weights: Dict[str, float], iterations: int = 150) -> float:
        """
        Monte Carlo simulation with Bayesian-weighted opponent range.
        
        Instead of uniform sampling, we sample opponent hands according to
        posterior probabilities from Bayesian inference.
        """
        my_cards = [eval7.Card(c) for c in my_hand]
        bd_cards = [eval7.Card(c) for c in board]
        dead = set(my_cards + bd_cards)
        
        all_combos = all_starting_hands()
        valid_combos = []
        weights = []
        
        for c1_str, c2_str in all_combos:
            c1 = eval7.Card(c1_str)
            c2 = eval7.Card(c2_str)
            if c1 in dead or c2 in dead:
                continue
            
            hand_key = hk([c1_str, c2_str])
            weight = hand_weights.get(hand_key, 0.0)
            if weight > 0:
                valid_combos.append((c1, c2))
                weights.append(weight)
        
        if not valid_combos:
            return 0.5
        
        total_weight = sum(weights)
        weights = [w/total_weight for w in weights]
        
        dk = [c for c in DECK if c not in dead]
        nb = 5 - len(bd_cards)
        
        w = t = 0
        for _ in range(iterations):
            idx = _weighted_choice(weights)
            opp_c1, opp_c2 = valid_combos[idx]
            
            remaining = [c for c in dk if c not in [opp_c1, opp_c2]]
            if len(remaining) < nb:
                continue
            
            import random
            add_board = random.sample(remaining, nb) if nb > 0 else []
            
            my_val = eval7.evaluate(my_cards + bd_cards + add_board)
            opp_val = eval7.evaluate([opp_c1, opp_c2] + bd_cards + add_board)
            
            if my_val > opp_val:
                w += 1
            elif my_val == opp_val:
                t += 1
        
        return (w + 0.5 * t) / iterations if iterations > 0 else 0.5


def _weighted_choice(weights: List[float]) -> int:
    """Select index according to weights"""
    import random
    r = random.random() * sum(weights)
    cumsum = 0
    for i, w in enumerate(weights):
        cumsum += w
        if r < cumsum:
            return i
    return len(weights) - 1


class ActionTracker:
    """
    Tracks opponent actions during a single hand to enable
    street-by-street Bayesian updates.
    """
    
    def __init__(self):
        self.actions = []
        self.current_weights = None
    
    def reset(self):
        """Call at hand start"""
        self.actions = []
        all_hands = list(PFE.keys())
        self.current_weights = {h: 1.0/len(all_hands) for h in all_hands}
    
    def record_and_update(self, action: str, street: str, cost_to_call: int, 
                         pot: int, opp_model: OpponentModel):
        """
        Record action and update weights via Bayesian inference.
        """
        self.actions.append((action, street, cost_to_call, pot))
        self.current_weights = opp_model.update_range_bayesian(
            action, street, cost_to_call, pot, self.current_weights
        )
    
    def get_current_range(self) -> Dict[str, float]:
        """Get current posterior distribution over opponent hands"""
        return self.current_weights


###############################################################################
# BAYESIAN GTO BOT
###############################################################################

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot
import random

BB = 20


class Player(BaseBot):
    def __init__(self):
        self.hn = 0
        self.opp_bets = 0
        self.opp_chks = 0
        self.opp_fold_cnt = 0
        self.opp_showdown = 0
        self.hands_done = 0
        self.won_auc = False
        self._prow = 0
        self._folded = False
        
        self.opp_model = OpponentModel()
        self.action_tracker = ActionTracker()
        self._last_street = 'pre-flop'
        self._last_opp_wager = 0

    def on_hand_start(self, gi: GameInfo, s: PokerState):
        self.hn += 1
        self.won_auc = False
        self._prow = s.opp_wager
        self._folded = False
        self.action_tracker.reset()
        self._last_street = s.street
        self._last_opp_wager = s.opp_wager

    def on_hand_end(self, gi: GameInfo, s: PokerState):
        self.hands_done += 1
        if s.street != 'river':
            if s.payoff > 0 and not self._folded:
                self.opp_fold_cnt += 1
        else:
            if not self._folded:
                self.opp_showdown += 1

    @staticmethod
    def _hk(h):
        r0, s0, r1, s1 = h[0][0], h[0][1], h[1][0], h[1][1]
        v0, v1 = RV[r0], RV[r1]
        if v0 < v1:
            r0, r1, s0, s1 = r1, r0, s1, s0
        if v0 == v1:
            return r0 + r1
        return r0 + r1 + ('s' if s0 == s1 else 'o')

    def _pf_eq(self, h):
        return PFE.get(self._hk(h), 0.40)

    def _mc(self, h, b, opp=None, n=150):
        my = [eval7.Card(c) for c in h]
        bd = [eval7.Card(c) for c in b]
        dead = set(my + bd)
        ox = []
        if opp:
            ox = [eval7.Card(c) for c in opp]
            dead.update(ox)
        dk = [c for c in DECK if c not in dead]
        no = 2 - len(ox)
        nb = 5 - len(bd)
        w = t = 0
        for _ in range(n):
            s = random.sample(dk, no + nb)
            oc = ox + s[:no]
            fb = bd + s[no:]
            mv = eval7.evaluate(my + fb)
            ov = eval7.evaluate(oc + fb)
            if mv > ov:
                w += 1
            elif mv == ov:
                t += 1
        return (w + .5 * t) / n

    def _eq(self, s):
        if s.street == 'pre-flop':
            return self._pf_eq(s.my_hand)
        
        it = {'flop': 300, 'turn': 400, 'river': 600}.get(s.street, 300)
        op = s.opp_revealed_cards if s.opp_revealed_cards else None
        
        if self.opp_model.total_observations >= 30 and not op:
            current_range = self.action_tracker.get_current_range()
            if current_range and sum(current_range.values()) > 0:
                return self.opp_model.weighted_monte_carlo(
                    s.my_hand, s.board, current_range, iterations=it
                )
        
        return self._mc(s.my_hand, s.board, opp=op, n=it)

    def _spr(self, s):
        return min(s.my_chips, s.opp_chips) / max(1, s.pot)

    def _ip(self, s):
        return s.is_bb if s.street == 'pre-flop' else (not s.is_bb)

    def _suits(self, cards):
        sc = {}
        for c in cards:
            sc[c[1]] = sc.get(c[1], 0) + 1
        return sc

    def _wet(self, b):
        if len(b) < 3:
            return False
        rk = sorted(RV[c[0]] for c in b)
        sc = self._suits(b)
        flush_d = max(sc.values()) >= 3
        conn = sum(1 for i in range(len(rk) - 1) if rk[i + 1] - rk[i] <= 2)
        return flush_d or conn >= 2

    def _flush_draw(self, h, b):
        sc = self._suits(h + b)
        return max(sc.values()) >= 4

    def _straight_draw(self, h, b):
        vs = sorted(set(RV[c[0]] for c in h + b))
        if any(c[0] == 'A' for c in h + b):
            vs = sorted(set(vs + [1]))
        for i in range(len(vs)):
            cnt = 1
            for j in range(i + 1, len(vs)):
                if vs[j] - vs[i] <= 4:
                    cnt += 1
                else:
                    break
            if cnt >= 4:
                return True
        return False

    def _nfb(self, h, b):
        sc = self._suits(b)
        for su, cnt in sc.items():
            if cnt >= 3 and any(c[0] == 'A' and c[1] == su for c in h):
                return True
        return False

    def _blocks_calls(self, h, b):
        if len(b) < 3:
            return False
        top = max(RV[c[0]] for c in b)
        return any(RV[c[0]] == top for c in h)

    def _blocks_bluffs(self, h, b):
        if len(b) < 3:
            return False
        sc = self._suits(b)
        for su, cnt in sc.items():
            if cnt >= 3 and any(c[1] == su for c in h):
                return True
        return False

    def _cat(self, h, b):
        if len(b) < 3:
            return 'pf', {}
        cards = [eval7.Card(c) for c in h + b]
        val = eval7.evaluate(cards)
        ht = eval7.handtype(val)
        hr = [RV[c[0]] for c in h]
        br = sorted(RV[c[0]] for c in b)
        top = max(br)
        wet = self._wet(b)
        fd = self._flush_draw(h, b)
        sd = self._straight_draw(h, b)
        nfb = self._nfb(h, b)
        bcr = self._blocks_calls(h, b)

        if ht in ('Straight Flush', 'Four of a Kind', 'Full House', 'Flush', 'Straight', 'Three of a Kind'):
            vuln = wet and ht in ('Straight', 'Three of a Kind')
            nut = ht in ('Straight Flush', 'Four of a Kind', 'Full House')
            return 'strong', {'vuln': vuln, 'nut': nut, 'bcr': bcr}

        if ht == 'Two Pair':
            return 'strong', {'vuln': wet, 'nut': False, 'bcr': bcr}

        if ht == 'Pair':
            all_r = hr + br
            cnt = {}
            for r in all_r:
                cnt[r] = cnt.get(r, 0) + 1
            pr = [r for r, c in cnt.items() if c >= 2]
            if pr:
                overpair = hr[0] == hr[1] and min(hr) > top
                top_pair = max(pr) == top and top in hr
                if overpair:
                    return 'strong', {'vuln': wet and min(hr) <= 12, 'nut': False, 'bcr': bcr}
                if top_pair and max(hr) >= 12:
                    return 'strong', {'vuln': True, 'nut': False, 'bcr': bcr}
                if top_pair:
                    return 'medium', {'draws': fd or sd}
                return 'medium', {'draws': fd or sd}
            return 'medium', {'draws': fd or sd}

        if max(hr) == 14:
            return 'medium', {'draws': fd or sd}
        if fd or sd:
            return 'weak', {'fd': fd, 'sd': sd, 'nfb': nfb}
        return 'weak', {'fd': False, 'sd': False, 'nfb': nfb}

    def _otype(self):
        if self.opp_model.total_observations >= 30:
            return self.opp_model.classify_opponent()
        
        tot = self.opp_bets + self.opp_chks
        if tot < 25:
            return 'unknown'
        agg = self.opp_bets / max(1, tot)
        fr = self.opp_fold_cnt / max(1, self.hands_done)
        if fr > 0.50:
            return 'nit'
        if fr < 0.20 and agg > 0.40:
            return 'maniac'
        if fr < 0.25:
            return 'station'
        return 'reg'

    def _track(self, s):
        if s.street != self._last_street:
            self._last_street = s.street
        
        if s.opp_wager > self._last_opp_wager:
            if s.cost_to_call > 0:
                action = 'raise'
            else:
                action = 'call'
            
            prev_pot = s.pot - (s.opp_wager - self._last_opp_wager)
            cost = s.opp_wager - self._last_opp_wager
            
            self.opp_model.observe_action(action, s.street, cost, prev_pot)
            self.action_tracker.record_and_update(action, s.street, cost, prev_pot, self.opp_model)
            
            self._last_opp_wager = s.opp_wager
        
        if s.opp_wager > self._prow:
            self.opp_bets += 1
        else:
            self.opp_chks += 1
        self._prow = s.opp_wager

    def _rt(self, s, amt):
        if s.can_act(ActionRaise):
            mn, mx = s.raise_bounds
            return ActionRaise(min(mx, max(mn, int(amt))))
        if s.can_act(ActionCall):
            return ActionCall()
        if s.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()

    def _cc(self, s):
        if s.can_act(ActionCheck):
            return ActionCheck()
        if s.can_act(ActionCall):
            return ActionCall()
        return ActionFold()

    def _cf(self, s):
        if s.can_act(ActionCheck):
            return ActionCheck()
        if s.can_act(ActionFold):
            return ActionFold()
        return self._cc(s)

    def _sz(self, pot, spr, kind):
        if kind == 'value':
            return pot * (random.uniform(0.65, 0.90) if spr > 5 else random.uniform(0.80, 1.1))
        if kind == 'bluff':
            return pot * (random.uniform(0.50, 0.70) if spr > 5 else random.uniform(0.60, 0.80))
        return pot * random.uniform(0.30, 0.45)

    def _bid(self, s):
        eq = self._mc(s.my_hand, s.board, n=200)
        pot = s.pot
        unc = 1.0 - (2.0 * abs(eq - 0.5)) ** 1.3
        ot = self._otype()
        if eq > 0.75:
            v = pot * random.uniform(0.35, 0.55)
        elif eq > 0.60:
            v = pot * random.uniform(0.20, 0.35)
        elif eq < 0.30:
            v = pot * random.uniform(0.00, 0.04)
        else:
            v = unc * pot * 0.30
        v = min(v, s.my_chips * 0.12, pot * 0.55)
        if ot == 'nit':
            v *= 0.7
        elif ot == 'station':
            v *= 1.2 if eq > 0.6 else 0.6
        v *= random.uniform(0.85, 1.15)
        return ActionBid(max(0, min(s.my_chips, int(v))))

    def _preflop(self, s, eq, ip, ot):
        tc = s.cost_to_call
        pot = s.pot
        r = random.random()

        if ip:
            if tc == 0:
                if eq > 0.56:
                    return self._rt(s, pot + 30)
                if eq > 0.48 and r < 0.30:
                    return self._rt(s, pot + 20)
                return ActionCheck() if s.can_act(ActionCheck) else self._cc(s)
            po = tc / (pot + tc)
            if eq > 0.65:
                return self._rt(s, int(pot * 2.5 + tc))
            if eq > 0.52:
                return ActionCall() if s.can_act(ActionCall) else self._cf(s)
            if eq > 0.44 and po < eq - 0.02:
                return ActionCall() if s.can_act(ActionCall) else self._cf(s)
            if eq > 0.38 and tc <= 30:
                return ActionCall() if s.can_act(ActionCall) else self._cf(s)
            return self._cf(s)
        else:
            if tc <= 10:
                if eq > 0.50:
                    return self._rt(s, 50)
                if eq > 0.36:
                    return ActionCall() if s.can_act(ActionCall) else self._cf(s)
                if ot == 'nit' and eq > 0.30 and r < 0.20:
                    return self._rt(s, 50)
                if eq > 0.33:
                    return ActionCall() if s.can_act(ActionCall) else self._cf(s)
                return self._cf(s)
            po = tc / (pot + tc)
            if eq > 0.68:
                return self._rt(s, int(pot * 2 + tc))
            if eq > 0.55:
                return ActionCall() if s.can_act(ActionCall) else self._cf(s)
            if eq > 0.48 and po < eq - 0.03:
                return ActionCall() if s.can_act(ActionCall) else self._cf(s)
            return self._cf(s)

    def _postflop(self, s, eq, ip, ot):
        pot = s.pot
        tc = s.cost_to_call
        spr = self._spr(s)
        r = random.random()
        cat, info = self._cat(s.my_hand, s.board)
        fd = self._flush_draw(s.my_hand, s.board)
        sd = self._straight_draw(s.my_hand, s.board)
        nfb = self._nfb(s.my_hand, s.board)
        bcr = self._blocks_calls(s.my_hand, s.board)
        bbl = self._blocks_bluffs(s.my_hand, s.board)
        draws = fd or sd
        wet = self._wet(s.board)

        if cat == 'strong':
            vuln = info.get('vuln', False)
            nut = info.get('nut', False)
            trap = (nut and not wet) or (bcr and not vuln) or (ot == 'maniac' and nut)

            if tc > 0:
                if eq > 0.58 and r < 0.65:
                    return self._rt(s, self._sz(pot, spr, 'value') + tc)
                return ActionCall() if s.can_act(ActionCall) else self._cf(s)

            if trap and r < 0.40:
                return ActionCheck() if s.can_act(ActionCheck) else self._cc(s)

            if vuln or ot == 'station':
                return self._rt(s, self._sz(pot, spr, 'value'))

            if r < 0.85:
                return self._rt(s, self._sz(pot, spr, 'value'))
            return self._cc(s)

        if cat == 'medium':
            if tc > 0:
                po = tc / (pot + tc)
                if bbl and eq < 0.45:
                    return self._cf(s)
                if po < eq - 0.06:
                    return ActionCall() if s.can_act(ActionCall) else self._cf(s)
                if draws and po < eq + 0.03:
                    return ActionCall() if s.can_act(ActionCall) else self._cf(s)
                return self._cf(s)

            if ip and r < 0.20 and ot not in ('maniac', 'station'):
                return self._rt(s, self._sz(pot, spr, 'probe'))
            return ActionCheck() if s.can_act(ActionCheck) else self._cc(s)

        if tc > 0:
            if draws:
                po = tc / (pot + tc)
                dr_eq = (0.18 if fd else 0) + (0.12 if sd else 0) + (0.06 if spr > 5 else 0)
                if po < dr_eq + 0.02:
                    return ActionCall() if s.can_act(ActionCall) else self._cf(s)
                if nfb and r < 0.14 and s.can_act(ActionRaise):
                    return self._rt(s, self._sz(pot, spr, 'bluff') + tc)
            return self._cf(s)

        bf = 0.0
        if nfb:
            bf += 0.15
        if draws:
            bf += 0.10
        if ip:
            bf += 0.06
        if ot == 'nit':
            bf += 0.12
        if ot == 'station':
            bf -= 0.15
        if r < bf and s.can_act(ActionRaise):
            return self._rt(s, self._sz(pot, spr, 'bluff'))
        return ActionCheck() if s.can_act(ActionCheck) else self._cf(s)

    def get_move(self, gi: GameInfo, s: PokerState):
        if s.street == 'auction':
            return self._bid(s)

        self._track(s)
        eq = self._eq(s)
        ip = self._ip(s)
        ot = self._otype()

        if s.opp_revealed_cards:
            self.won_auc = True

        if not self.won_auc and s.street not in ('pre-flop', 'auction'):
            eq -= 0.03

        if ip:
            eq += 0.02

        if s.street != 'pre-flop' and s.cost_to_call > 0:
            bf = s.cost_to_call / (s.pot - s.cost_to_call + 0.01)
            disc = min(0.22, bf * bf * 0.18)
            if not self.won_auc:
                disc = min(0.28, disc * 1.5)
            if ot == 'maniac':
                disc *= 0.5
            eq -= disc

        eq = max(0.0, min(1.0, eq))

        if s.street == 'pre-flop':
            act = self._preflop(s, eq, ip, ot)
        else:
            act = self._postflop(s, eq, ip, ot)

        if isinstance(act, ActionFold):
            self._folded = True
        return act


if __name__ == '__main__':
    run_bot(Player(), parse_args())
