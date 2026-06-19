#!/usr/bin/env python3
"""Analyze all .glog files in GAME_LOGS to extract opponent patterns."""

import os, re
from collections import defaultdict

GLOG_DIR = os.path.join(os.path.dirname(__file__), "LOSSES")
OUR_NAME = "67"

def parse_glog(filepath):
    with open(filepath) as f:
        lines = f.readlines()
    first_line = lines[0].strip()
    m = re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (.+) vs (.+)', first_line)
    if not m:
        return None, []
    p1, p2 = m.group(1).strip(), m.group(2).strip()
    opp_name = p2 if p1 == OUR_NAME else p1

    rounds = []
    current_round = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rm = re.match(r'Round #(\d+), (.+?) \((-?\d+)\), (.+?) \((-?\d+)\)', line)
        if rm:
            if current_round:
                rounds.append(current_round)
            current_round = {
                'num': int(rm.group(1)), 'actions': [], 'hands': {},
                'bids': {}, 'revealed': {}, 'showdown': {}, 'awards': {},
                'street': 'preflop', 'board': []
            }
            continue
        if not current_round:
            continue
        hm = re.match(r'(.+?) received \[(.+?)\]', line)
        if hm:
            current_round['hands'][hm.group(1)] = hm.group(2).split()
            continue
        if 'posts blind' in line:
            continue
        bm = re.match(r'(.+?) bids (\d+)', line)
        if bm:
            current_round['bids'][bm.group(1)] = int(bm.group(2))
            continue
        if 'won the auction and was revealed' in line:
            am2 = re.match(r'(.+?) won the auction and was revealed \[(.+?)\]', line)
            if am2:
                current_round['revealed'][am2.group(1)] = am2.group(2)
            continue
        fm = re.match(r'(Flop|Turn|River) \[(.+?)\]', line)
        if fm:
            current_round['street'] = fm.group(1).lower()
            current_round['board'] = fm.group(2).split()
            continue
        sm = re.match(r'(.+?) shows \[(.+?)\]', line)
        if sm:
            current_round['showdown'][sm.group(1)] = sm.group(2).split()
            continue
        awm = re.match(r'(.+?) awarded (-?\d+)', line)
        if awm:
            current_round['awards'][awm.group(1)] = int(awm.group(2))
            continue
        for action_pat in [
            (r'(.+?) folds', 'fold', False),
            (r'(.+?) checks', 'check', False),
            (r'(.+?) calls', 'call', False),
            (r'(.+?) raises to (\d+)', 'raise', True),
            (r'(.+?) bets (\d+)', 'bet', True),
        ]:
            am = re.match(action_pat[0], line)
            if am:
                current_round['actions'].append({
                    'player': am.group(1), 'action': action_pat[1],
                    'amount': int(am.group(2)) if action_pat[2] else 0,
                    'street': current_round['street']
                })
                break
    if current_round:
        rounds.append(current_round)
    return opp_name, rounds


def analyze_opponent(opp_name, rounds):
    s = {
        'name': opp_name, 'total_rounds': len(rounds),
        'final_score_us': 0, 'final_score_opp': 0,
        'pf_fold': 0, 'pf_call': 0, 'pf_raise': 0, 'pf_check': 0,
        'pf_raise_sizes': [], 'pf_allin_count': 0, 'allin_shove_with': [],
        'pf_raise_hands': [],
        'post_fold': 0, 'post_call': 0, 'post_raise': 0,
        'post_bet': 0, 'post_check': 0, 'post_bet_sizes': [],
        'flop_actions': defaultdict(int), 'turn_actions': defaultdict(int),
        'river_actions': defaultdict(int),
        'bids': [], 'auction_wins': 0, 'auction_total': 0,
        'showdown_count': 0, 'showdown_wins': 0,
        'allin_results': [], 'never_bets_postflop': 0,
    }
    for rnd in rounds:
        for player, score in rnd.get('awards', {}).items():
            if player == OUR_NAME:
                s['final_score_us'] += score
            elif player == opp_name:
                s['final_score_opp'] += score
        opp_acts = [a for a in rnd['actions'] if a['player'] == opp_name]
        pf = [a for a in opp_acts if a['street'] == 'preflop']
        for a in pf:
            if a['action'] == 'fold': s['pf_fold'] += 1
            elif a['action'] == 'call': s['pf_call'] += 1
            elif a['action'] == 'raise':
                s['pf_raise'] += 1
                s['pf_raise_sizes'].append(a['amount'])
                if a['amount'] >= 4000:
                    s['pf_allin_count'] += 1
                    if opp_name in rnd['hands']:
                        s['allin_shove_with'].append(rnd['hands'][opp_name])
                if opp_name in rnd['hands']:
                    s['pf_raise_hands'].append((rnd['hands'][opp_name], a['amount']))
            elif a['action'] == 'check': s['pf_check'] += 1

        post = [a for a in opp_acts if a['street'] != 'preflop']
        opp_bet = False
        for a in post:
            sk = a['street'] + '_actions'
            if sk in s: s[sk][a['action']] += 1
            if a['action'] == 'fold': s['post_fold'] += 1
            elif a['action'] == 'call': s['post_call'] += 1
            elif a['action'] == 'raise': s['post_raise'] += 1; opp_bet = True
            elif a['action'] == 'bet': s['post_bet'] += 1; s['post_bet_sizes'].append(a['amount']); opp_bet = True
            elif a['action'] == 'check': s['post_check'] += 1
        if post and not opp_bet:
            s['never_bets_postflop'] += 1

        if rnd['bids']:
            s['auction_total'] += 1
            if opp_name in rnd['bids']:
                s['bids'].append(rnd['bids'][opp_name])
            if opp_name in rnd.get('revealed', {}):
                s['auction_wins'] += 1

        if rnd['showdown'] and opp_name in rnd['showdown']:
            s['showdown_count'] += 1
            if rnd['awards'].get(opp_name, 0) > 0:
                s['showdown_wins'] += 1

        has_allin = any(a['action'] == 'raise' and a['amount'] >= 4000 for a in [a for a in opp_acts if a['street'] == 'preflop'])
        if has_allin:
            s['allin_results'].append(rnd['awards'].get(opp_name, 0))
    return s


def fmt(cards):
    return ' '.join(cards) if isinstance(cards, list) else str(cards)

def print_analysis(s):
    total_pf = s['pf_fold'] + s['pf_call'] + s['pf_raise'] + s['pf_check']
    vpip = (s['pf_call'] + s['pf_raise']) / max(total_pf, 1) * 100
    pfr = s['pf_raise'] / max(total_pf, 1) * 100
    total_post = s['post_fold'] + s['post_call'] + s['post_raise'] + s['post_bet'] + s['post_check']
    post_agg = (s['post_bet'] + s['post_raise']) / max(total_post, 1) * 100

    print(f"\n{'='*70}")
    print(f"  OPPONENT: {s['name']}")
    print(f"  Rounds: {s['total_rounds']}  |  Our P/L: {s['final_score_us']:+d}  |  Opp P/L: {s['final_score_opp']:+d}")
    print(f"{'='*70}")
    print(f"\n--- PREFLOP ---")
    print(f"  VPIP: {vpip:.1f}%  |  PFR: {pfr:.1f}%")
    print(f"  Fold: {s['pf_fold']}  Call: {s['pf_call']}  Raise: {s['pf_raise']}  Check: {s['pf_check']}")
    if s['pf_raise_sizes']:
        print(f"  Raise sizes: avg={sum(s['pf_raise_sizes'])/len(s['pf_raise_sizes']):.0f}  min={min(s['pf_raise_sizes'])}  max={max(s['pf_raise_sizes'])}")
    if s['pf_allin_count']:
        print(f"  ⚠️  ALL-IN SHOVES: {s['pf_allin_count']} ({s['pf_allin_count']/max(total_pf,1)*100:.1f}%)")
        if s['allin_shove_with']:
            print(f"  Shoved with: {', '.join([fmt(h) for h in s['allin_shove_with'][:20]])}{'...' if len(s['allin_shove_with'])>20 else ''}")

    print(f"\n--- POSTFLOP ---")
    print(f"  Check: {s['post_check']}  Bet: {s['post_bet']}  Raise: {s['post_raise']}  Call: {s['post_call']}  Fold: {s['post_fold']}")
    if total_post > 0:
        print(f"  Aggression: {post_agg:.1f}%  |  Check-through hands: {s['never_bets_postflop']}")
    if s['post_bet_sizes']:
        print(f"  Bet sizes: avg={sum(s['post_bet_sizes'])/len(s['post_bet_sizes']):.0f}  range=[{min(s['post_bet_sizes'])}-{max(s['post_bet_sizes'])}]")
    for street, key in [('Flop', 'flop_actions'), ('Turn', 'turn_actions'), ('River', 'river_actions')]:
        if s[key]: print(f"  {street}: {dict(s[key])}")

    print(f"\n--- AUCTION ---")
    if s['bids']:
        avg_bid = sum(s['bids']) / len(s['bids'])
        zero_bids = sum(1 for b in s['bids'] if b == 0)
        print(f"  Avg bid: {avg_bid:.1f}  |  Zero bids: {zero_bids}/{len(s['bids'])} ({zero_bids/len(s['bids'])*100:.0f}%)")
        print(f"  Bid values seen: {sorted(set(s['bids']))}")
        print(f"  Auction wins: {s['auction_wins']}/{s['auction_total']}")

    print(f"\n--- SHOWDOWN ---")
    print(f"  Showdowns: {s['showdown_count']}  Won: {s['showdown_wins']} ({s['showdown_wins']/max(s['showdown_count'],1)*100:.1f}%)")

    if s['allin_results']:
        wins = sum(1 for r in s['allin_results'] if r > 0)
        losses = sum(1 for r in s['allin_results'] if r < 0)
        print(f"\n--- ALL-IN ANALYSIS ---")
        print(f"  Shoves: {len(s['allin_results'])}  Won: {wins}  Lost: {losses}  Net: {sum(s['allin_results']):+d}")

    print(f"\n--- PATTERNS ---")
    if s['pf_allin_count'] > 5 and s['pf_allin_count'] / max(total_pf, 1) > 0.15:
        print(f"  🔴 SHOVE-BOT: {s['pf_allin_count']/max(total_pf,1)*100:.0f}% preflop shove rate")
    if s['bids'] and sum(1 for b in s['bids'] if b == 0) / len(s['bids']) > 0.8:
        print(f"  🔴 AUCTION-IGNORER: {sum(1 for b in s['bids'] if b==0)/len(s['bids'])*100:.0f}% zero bids")
    if total_post > 0 and post_agg < 5:
        print(f"  🔴 POSTFLOP-PASSIVE: {post_agg:.1f}% aggression")
    if total_post > 0 and s['post_check'] / total_post > 0.85:
        print(f"  🔴 CHECK-MACHINE: {s['post_check']/total_post*100:.0f}% checks")
    if total_pf > 0 and s['pf_fold'] / total_pf > 0.5:
        print(f"  🟡 TIGHT: {s['pf_fold']/total_pf*100:.0f}% preflop fold")
    if vpip > 70:
        print(f"  🟡 LOOSE: VPIP {vpip:.0f}%")
    if total_post > 0 and s['post_fold'] / total_post > 0.3:
        print(f"  🟡 POSTFLOP-FOLDER: {s['post_fold']/total_post*100:.0f}% fold rate")
    print()


def main():
    all_glogs = []
    if not os.path.exists(GLOG_DIR):
        print(f"Directory not found: {GLOG_DIR}")
        return

    # Look for .log files directly in LOSSES or in subdirectories
    for root, dirs, files in os.walk(GLOG_DIR):
        for f in files:
            if f.endswith('.log') and not f.endswith('.gz'):
                all_glogs.append(os.path.join(root, f))

    print(f"Found {len(all_glogs)} game logs\n")
    opp_rounds = defaultdict(list)
    for glog in all_glogs:
        opp_name, rounds = parse_glog(glog)
        if opp_name:
            opp_rounds[opp_name].extend(rounds)
    print(f"Unique opponents: {list(opp_rounds.keys())}\n")

    all_stats = []
    for opp_name, rounds in sorted(opp_rounds.items()):
        stats = analyze_opponent(opp_name, rounds)
        all_stats.append(stats)
        print_analysis(stats)

    print(f"\n{'='*80}")
    print(f"  SUMMARY TABLE")
    print(f"{'='*80}")
    print(f"{'Opponent':<20} {'Rnds':>5} {'Our P/L':>8} {'VPIP%':>6} {'PFR%':>6} {'Shv%':>5} {'PostAgg%':>8} {'AvgBid':>6} {'SDWin%':>6}")
    print('-'*80)
    for s in all_stats:
        tp = s['pf_fold'] + s['pf_call'] + s['pf_raise'] + s['pf_check']
        v = (s['pf_call'] + s['pf_raise']) / max(tp, 1) * 100
        p = s['pf_raise'] / max(tp, 1) * 100
        sh = s['pf_allin_count'] / max(tp, 1) * 100
        tpost = s['post_fold'] + s['post_call'] + s['post_raise'] + s['post_bet'] + s['post_check']
        pa = (s['post_bet'] + s['post_raise']) / max(tpost, 1) * 100
        ab = sum(s['bids']) / max(len(s['bids']), 1) if s['bids'] else 0
        sw = s['showdown_wins'] / max(s['showdown_count'], 1) * 100
        print(f"{s['name']:<20} {s['total_rounds']:>5} {s['final_score_us']:>+8} {v:>5.1f}% {p:>5.1f}% {sh:>4.0f}% {pa:>7.1f}% {ab:>5.1f} {sw:>5.0f}%")


if __name__ == '__main__':
    main()
