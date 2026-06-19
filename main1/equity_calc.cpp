// ============================================================================
// equity_calc.cpp
// Preflop Equity Calculator for all 169 distinct starting hands
//
// Three calculation modes:
//   Mode 1 (mc):      Pure Monte Carlo — randomize villain + board
//   Mode 2 (hybrid):  Enumerate all villain hands, MC the board (recommended)
//   Mode 3 (exact):   Full exhaustive — enumerate ALL villain + board combos
//
// Compile:
//   g++ -O3 -std=c++17 -o equity_calc equity_calc.cpp
//   (with OpenMP):  g++ -O3 -std=c++17 -fopenmp -o equity_calc equity_calc.cpp
//
// Usage:
//   ./equity_calc mc      [sims]          # Pure MC (default 1000000 sims)
//   ./equity_calc hybrid  [board_sims]    # Enum villains + MC board (default 1000)
//   ./equity_calc exact                   # Full exhaustive (no sampling)
//   ./equity_calc                         # Defaults to hybrid 1000
//
// Output:
//   Console table + preflop_equities.txt (Python dict) + preflop_equities.csv
// ============================================================================

#include <iostream>
#include <vector>
#include <random>
#include <chrono>
#include <iomanip>
#include <string>
#include <algorithm>
#include <fstream>
#include <cstring>
#include <cstdlib>
#include <atomic>

#ifdef _OPENMP
#include <omp.h>
#else
inline int omp_get_thread_num() { return 0; }
inline int omp_get_max_threads() { return 1; }
#endif

using namespace std;

// -------------------------------------------------------------------------
// Card encoding:  card_id = rank * 4 + suit
//   rank: 0=2, 1=3, ..., 8=T, 9=J, 10=Q, 11=K, 12=A
//   suit: 0-3
// -------------------------------------------------------------------------
static inline int card_rank(int c) { return c >> 2; }
static inline int card_suit(int c) { return c & 3;  }

// -------------------------------------------------------------------------
// Hand categories (higher = better)
// -------------------------------------------------------------------------
enum HandCategory {
    HIGH_CARD       = 0,
    ONE_PAIR        = 1,
    TWO_PAIR        = 2,
    THREE_OF_A_KIND = 3,
    STRAIGHT        = 4,
    FLUSH           = 5,
    FULL_HOUSE      = 6,
    FOUR_OF_A_KIND  = 7,
    STRAIGHT_FLUSH  = 8
};

// -------------------------------------------------------------------------
// Score encoding:  category * 13^5 + kickers
// -------------------------------------------------------------------------
static inline int encode_score(int cat, int k1=0, int k2=0, int k3=0, int k4=0, int k5=0) {
    return cat * 371293 + k1 * 28561 + k2 * 2197 + k3 * 169 + k4 * 13 + k5;
}

// -------------------------------------------------------------------------
// Find the highest straight top-card rank from a bitmask of ranks present.
// -------------------------------------------------------------------------
static inline int find_straight_high(int rank_bits) {
    for (int top = 12; top >= 4; --top) {
        int mask = 0x1F << (top - 4);
        if ((rank_bits & mask) == mask) return top;
    }
    const int wheel = (1 << 12) | 0xF;
    if ((rank_bits & wheel) == wheel) return 3;
    return -1;
}

// -------------------------------------------------------------------------
// Evaluate best 5-card hand from 7 cards.  Returns score (higher = better).
// -------------------------------------------------------------------------
static int evaluate_7(int c0, int c1, int c2, int c3, int c4, int c5, int c6) {
    int rank_count[13] = {};
    int suit_count[4]  = {};
    int suit_ranks[4]  = {};
    int all_ranks      = 0;

    int cards[7] = {c0,c1,c2,c3,c4,c5,c6};
    for (int i = 0; i < 7; ++i) {
        int r = card_rank(cards[i]);
        int s = card_suit(cards[i]);
        rank_count[r]++;
        suit_count[s]++;
        suit_ranks[s] |= (1 << r);
        all_ranks |= (1 << r);
    }

    // 1. Straight Flush
    for (int s = 0; s < 4; ++s) {
        if (suit_count[s] >= 5) {
            int sh = find_straight_high(suit_ranks[s]);
            if (sh >= 0) return encode_score(STRAIGHT_FLUSH, sh);
        }
    }

    // 2. Four of a Kind
    for (int r = 12; r >= 0; --r) {
        if (rank_count[r] == 4) {
            int kicker = 0;
            for (int k = 12; k >= 0; --k)
                if (k != r && rank_count[k] > 0) { kicker = k; break; }
            return encode_score(FOUR_OF_A_KIND, r, kicker);
        }
    }

    // 3. Full House
    {
        int best_trips = -1, best_pair = -1;
        for (int r = 12; r >= 0; --r) {
            if (rank_count[r] >= 3 && best_trips < 0) best_trips = r;
            else if (rank_count[r] >= 2 && best_pair < 0) best_pair = r;
        }
        if (best_trips >= 0 && best_pair >= 0)
            return encode_score(FULL_HOUSE, best_trips, best_pair);
    }

    // 4. Flush
    for (int s = 0; s < 4; ++s) {
        if (suit_count[s] >= 5) {
            int fk[5]; int fi = 0;
            for (int r = 12; r >= 0 && fi < 5; --r)
                if (suit_ranks[s] & (1 << r)) fk[fi++] = r;
            return encode_score(FLUSH, fk[0], fk[1], fk[2], fk[3], fk[4]);
        }
    }

    // 5. Straight
    {
        int sh = find_straight_high(all_ranks);
        if (sh >= 0) return encode_score(STRAIGHT, sh);
    }

    // 6. Three of a Kind
    for (int r = 12; r >= 0; --r) {
        if (rank_count[r] >= 3) {
            int k[2]; int ki = 0;
            for (int j = 12; j >= 0 && ki < 2; --j)
                if (j != r && rank_count[j] > 0) k[ki++] = j;
            return encode_score(THREE_OF_A_KIND, r, k[0], k[1]);
        }
    }

    // 7. Two Pair
    {
        int pairs[3]; int pi = 0;
        for (int r = 12; r >= 0 && pi < 3; --r)
            if (rank_count[r] >= 2) pairs[pi++] = r;
        if (pi >= 2) {
            int kicker = 0;
            for (int r = 12; r >= 0; --r)
                if (r != pairs[0] && r != pairs[1] && rank_count[r] > 0) { kicker = r; break; }
            return encode_score(TWO_PAIR, pairs[0], pairs[1], kicker);
        }
    }

    // 8. One Pair
    for (int r = 12; r >= 0; --r) {
        if (rank_count[r] >= 2) {
            int k[3]; int ki = 0;
            for (int j = 12; j >= 0 && ki < 3; --j)
                if (j != r && rank_count[j] > 0) k[ki++] = j;
            return encode_score(ONE_PAIR, r, k[0], k[1], k[2]);
        }
    }

    // 9. High Card
    {
        int k[5]; int ki = 0;
        for (int r = 12; r >= 0 && ki < 5; --r)
            if (rank_count[r] > 0) k[ki++] = r;
        return encode_score(HIGH_CARD, k[0], k[1], k[2], k[3], k[4]);
    }
}

// -------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------
static string rank_char(int r) {
    const char ranks[] = "23456789TJQKA";
    return string(1, ranks[r]);
}

struct DistinctHand {
    int card1, card2;
    string name;
    double equity;
};

// =========================================================================
// MODE 1:  Pure Monte Carlo
//   Randomize villain + board.  Total samples = sims per hand.
// =========================================================================
static void run_pure_mc(vector<DistinctHand>& hands, int sims) {
    #pragma omp parallel for schedule(dynamic)
    for (size_t h = 0; h < hands.size(); ++h) {
        int hc1 = hands[h].card1, hc2 = hands[h].card2;

        int deck[50]; int di = 0;
        for (int c = 0; c < 52; ++c)
            if (c != hc1 && c != hc2) deck[di++] = c;

        unsigned seed = 42u + (unsigned)h * 7919u;
        #ifdef _OPENMP
        seed += (unsigned)omp_get_thread_num() * 104729u;
        #endif
        mt19937 rng(seed);

        long long wins = 0, ties = 0;
        for (int s = 0; s < sims; ++s) {
            int d[50]; memcpy(d, deck, sizeof(deck));
            for (int k = 0; k < 7; ++k) {
                uniform_int_distribution<int> dist(k, 49);
                swap(d[k], d[dist(rng)]);
            }
            int hs = evaluate_7(hc1, hc2, d[2],d[3],d[4],d[5],d[6]);
            int vs = evaluate_7(d[0],d[1], d[2],d[3],d[4],d[5],d[6]);
            if      (hs > vs) ++wins;
            else if (hs == vs) ++ties;
        }
        hands[h].equity = (wins + ties / 2.0) / (double)sims;
    }
}

// =========================================================================
// MODE 2:  Enumerate all villain hands + Monte Carlo the board
//   For each hero hand: loop over all C(50,2)=1225 villain combos,
//   and for each villain hand sample `board_sims` random boards.
//   Total samples per hero hand = 1225 * board_sims.
// =========================================================================
static void run_hybrid(vector<DistinctHand>& hands, int board_sims) {
    #pragma omp parallel for schedule(dynamic)
    for (size_t h = 0; h < hands.size(); ++h) {
        int hc1 = hands[h].card1, hc2 = hands[h].card2;

        // Build remaining 50-card deck
        int deck50[50]; int di = 0;
        for (int c = 0; c < 52; ++c)
            if (c != hc1 && c != hc2) deck50[di++] = c;

        unsigned seed = 42u + (unsigned)h * 7919u;
        #ifdef _OPENMP
        seed += (unsigned)omp_get_thread_num() * 104729u;
        #endif
        mt19937 rng(seed);

        long long total_wins = 0, total_ties = 0, total_trials = 0;

        // Enumerate all villain hand combos from the remaining 50 cards
        for (int vi = 0; vi < 49; ++vi) {
            int vc1 = deck50[vi];
            for (int vj = vi + 1; vj < 50; ++vj) {
                int vc2 = deck50[vj];

                // Build 48-card sub-deck (exclude hero + villain)
                int deck48[48]; int d48i = 0;
                for (int k = 0; k < 50; ++k)
                    if (k != vi && k != vj) deck48[d48i++] = deck50[k];

                // Monte Carlo the board
                for (int s = 0; s < board_sims; ++s) {
                    int b[48]; memcpy(b, deck48, sizeof(deck48));
                    for (int k = 0; k < 5; ++k) {
                        uniform_int_distribution<int> dist(k, 47);
                        swap(b[k], b[dist(rng)]);
                    }
                    int hs = evaluate_7(hc1, hc2, b[0],b[1],b[2],b[3],b[4]);
                    int vs = evaluate_7(vc1, vc2, b[0],b[1],b[2],b[3],b[4]);
                    if      (hs > vs) ++total_wins;
                    else if (hs == vs) ++total_ties;
                }
                total_trials += board_sims;
            }
        }
        hands[h].equity = (total_wins + total_ties / 2.0) / (double)total_trials;
    }
}

// =========================================================================
// MODE 3:  Full Exhaustive Enumeration
//   For each hero hand: loop over all C(50,2)=1225 villain combos,
//   and for each villain hand loop over ALL C(48,5)=1,712,304 boards.
//   Total per hero hand = 1225 * 1,712,304 = 2,097,572,400 evaluations.
//   Grand total = 169 * 2.1B ~ 354 Billion.
// =========================================================================
static void run_exact(vector<DistinctHand>& hands) {
    atomic<int> hands_done(0);
    int total_hands = (int)hands.size();

    #pragma omp parallel for schedule(dynamic)
    for (size_t h = 0; h < hands.size(); ++h) {
        int hc1 = hands[h].card1, hc2 = hands[h].card2;

        int deck50[50]; int di = 0;
        for (int c = 0; c < 52; ++c)
            if (c != hc1 && c != hc2) deck50[di++] = c;

        long long total_wins = 0, total_ties = 0, total_trials = 0;

        for (int vi = 0; vi < 49; ++vi) {
            int vc1 = deck50[vi];
            for (int vj = vi + 1; vj < 50; ++vj) {
                int vc2 = deck50[vj];

                int deck48[48]; int d48i = 0;
                for (int k = 0; k < 50; ++k)
                    if (k != vi && k != vj) deck48[d48i++] = deck50[k];

                // Enumerate ALL C(48,5) boards
                for (int a = 0; a < 44; ++a) {
                    for (int b = a+1; b < 45; ++b) {
                        for (int c = b+1; c < 46; ++c) {
                            for (int d = c+1; d < 47; ++d) {
                                for (int e = d+1; e < 48; ++e) {
                                    int hs = evaluate_7(hc1, hc2,
                                        deck48[a], deck48[b], deck48[c], deck48[d], deck48[e]);
                                    int vs = evaluate_7(vc1, vc2,
                                        deck48[a], deck48[b], deck48[c], deck48[d], deck48[e]);
                                    if      (hs > vs) ++total_wins;
                                    else if (hs == vs) ++total_ties;
                                }
                            }
                        }
                    }
                }
                total_trials += 1712304LL; // C(48,5)
            }
        }
        hands[h].equity = (total_wins + total_ties / 2.0) / (double)total_trials;

        int done = ++hands_done;
        if (done % 5 == 0 || done == total_hands) {
            #pragma omp critical
            cout << "\r  Progress: " << done << "/" << total_hands
                 << " hands (" << (done * 100 / total_hands) << "%)" << flush;
        }
    }
    cout << endl;
}

// =========================================================================
// Output helpers
// =========================================================================
static void write_results(const vector<DistinctHand>& hands, double elapsed,
                          const string& mode_str) {
    vector<size_t> order(hands.size());
    for (size_t i = 0; i < order.size(); ++i) order[i] = i;
    sort(order.begin(), order.end(),
         [&](size_t a, size_t b){ return hands[a].equity > hands[b].equity; });

    // Console table
    cout << "\nRank  Hand    Equity" << endl;
    cout << "----  ----    ------" << endl;
    for (size_t i = 0; i < order.size(); ++i) {
        auto& h = hands[order[i]];
        cout << setw(4) << (i+1) << "  "
             << setw(4) << left << h.name << right << "    "
             << fixed << setprecision(2) << (h.equity * 100) << "%" << endl;
    }

    // Python dict
    {
        const string fn = "preflop_equities.txt";
        ofstream out(fn);
        if (out.is_open()) {
            out << "# Mode: " << mode_str << "\n";
            out << "PREFLOP_EQUITIES = {\n";
            for (size_t i = 0; i < hands.size(); ++i) {
                out << "    \"" << hands[i].name << "\": "
                    << fixed << setprecision(6) << hands[i].equity;
                if (i + 1 < hands.size()) out << ",";
                out << "\n";
            }
            out << "}\n";
            out.close();
            cout << "\nSaved to " << fn << " (Python dict)" << endl;
        }
    }

    // CSV
    {
        const string fn = "preflop_equities.csv";
        ofstream csv(fn);
        if (csv.is_open()) {
            csv << "rank,hand,equity_pct\n";
            for (size_t i = 0; i < order.size(); ++i) {
                auto& h = hands[order[i]];
                csv << (i+1) << "," << h.name << ","
                    << fixed << setprecision(4) << (h.equity * 100) << "\n";
            }
            csv.close();
            cout << "Saved to " << fn << " (CSV)" << endl;
        }
    }

    cout << "\nTime elapsed: " << fixed << setprecision(3) << elapsed << " seconds" << endl;
}

// =========================================================================
//  MAIN
// =========================================================================
int main(int argc, char* argv[]) {
    // -----------------------------------------------------------------
    // Parse CLI
    // -----------------------------------------------------------------
    string mode = "hybrid";
    int sims = 0;

    if (argc >= 2) mode = argv[1];
    if (argc >= 3) sims = atoi(argv[2]);

    if (mode == "mc"     && sims <= 0) sims = 1000000;
    if (mode == "hybrid" && sims <= 0) sims = 1000;

    // -----------------------------------------------------------------
    // Print header
    // -----------------------------------------------------------------
    cout << "=== Preflop Equity Calculator ===" << endl;
    #ifdef _OPENMP
    cout << "OpenMP enabled: " << omp_get_max_threads() << " threads" << endl;
    #else
    cout << "Single-threaded (compile with -fopenmp for parallelism)" << endl;
    #endif

    if (mode == "mc") {
        long long total = 169LL * sims;
        cout << "Mode: PURE MONTE CARLO" << endl;
        cout << "Sims per hand: " << sims
             << " | Total evals: " << total / 1'000'000.0 << "M" << endl;
    } else if (mode == "hybrid") {
        long long per_hand = 1225LL * sims;
        long long total = 169LL * per_hand;
        cout << "Mode: HYBRID (enumerate villains + MC board)" << endl;
        cout << "Board sims per villain combo: " << sims
             << " | Evals per hand: " << per_hand / 1000.0 << "K"
             << " | Total: " << total / 1'000'000.0 << "M" << endl;
    } else if (mode == "exact") {
        cout << "Mode: FULL EXHAUSTIVE ENUMERATION" << endl;
        cout << "Evals per hand: ~2.1 Billion | Total: ~354 Billion" << endl;
        cout << "*** WARNING: This will take a LONG time (est. ~1 hour). ***" << endl;
    } else {
        cerr << "\nUnknown mode: \"" << mode << "\"\n" << endl;
        cerr << "Usage: " << argv[0] << " [mc|hybrid|exact] [sims]\n" << endl;
        cerr << "Modes:" << endl;
        cerr << "  mc     [sims]        Pure Monte Carlo (default 1,000,000 sims/hand)" << endl;
        cerr << "  hybrid [board_sims]  Enum all 1225 villain hands + MC board (default 1000)" << endl;
        cerr << "  exact                Full exhaustive — enumerate all combos (exact results)" << endl;
        cerr << "\nExamples:" << endl;
        cerr << "  " << argv[0] << "                   # hybrid mode, 1000 board sims" << endl;
        cerr << "  " << argv[0] << " mc 500000         # MC with 500K sims per hand" << endl;
        cerr << "  " << argv[0] << " hybrid 5000       # hybrid with 5000 board sims" << endl;
        cerr << "  " << argv[0] << " exact             # exact (many hours)" << endl;
        return 1;
    }
    cout << "Running...\n" << endl;

    // -----------------------------------------------------------------
    // Generate the 169 distinct preflop hands
    // -----------------------------------------------------------------
    vector<DistinctHand> hands;
    hands.reserve(169);
    for (int r1 = 12; r1 >= 0; --r1) {
        for (int r2 = r1; r2 >= 0; --r2) {
            if (r1 == r2) {
                hands.push_back({r1*4, r1*4+1,
                    rank_char(r1) + rank_char(r2), 0.0});
            } else {
                hands.push_back({r1*4, r2*4,
                    rank_char(r1) + rank_char(r2) + "s", 0.0});
                hands.push_back({r1*4, r2*4+1,
                    rank_char(r1) + rank_char(r2) + "o", 0.0});
            }
        }
    }

    // -----------------------------------------------------------------
    // Run selected mode
    // -----------------------------------------------------------------
    auto t0 = chrono::high_resolution_clock::now();

    if      (mode == "mc")     run_pure_mc(hands, sims);
    else if (mode == "hybrid") run_hybrid(hands, sims);
    else if (mode == "exact")  run_exact(hands);

    auto t1 = chrono::high_resolution_clock::now();
    double elapsed = chrono::duration<double>(t1 - t0).count();

    // -----------------------------------------------------------------
    // Output
    // -----------------------------------------------------------------
    string mode_str;
    if      (mode == "mc")     mode_str = "mc " + to_string(sims);
    else if (mode == "hybrid") mode_str = "hybrid " + to_string(sims);
    else                       mode_str = "exact";

    write_results(hands, elapsed, mode_str);

    return 0;
}
