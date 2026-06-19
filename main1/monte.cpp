#include <iostream>
#include <vector>
#include <random>
#include <chrono>
#include <iomanip>
#include <string>
#include <algorithm>

#ifdef _OPENMP
    #include <omp.h>
#else
    inline int omp_get_thread_num() { return 0; }
#endif

using namespace std;

// -------------------------------------------------------------------------
// PLACEHOLDER EVALUATOR: Replace with OMPEval or TwoPlusTwo table.
// Lower evaluation functions usually return HIGHER integer scores for better hands.
// -------------------------------------------------------------------------
inline int evaluate_7_cards(int c1, int c2, int c3, int c4, int c5, int c6, int c7) {
    // Dummy score to prevent compiler optimization
    return c1 + c2 + c3 + c4 + c5 + c6 + c7; 
}

string get_rank(int r) {
    const char ranks[] = "23456789TJQKA";
    return string(1, ranks[r]);
}

struct DistinctHand {
    int card1;
    int card2;
    string name;
    double equity;
};

int main() {
    const int SIMULATIONS_PER_HAND = 10000;
    
    // Generate exactly the 169 distinct preflop hands
    vector<DistinctHand> distinct_hands;
    distinct_hands.reserve(169);

    for (int r1 = 12; r1 >= 0; --r1) { // Ranks: 12=Ace down to 0=2
        for (int r2 = r1; r2 >= 0; --r2) { // Only r1 >= r2 to prevent duplicates
            if (r1 == r2) {
                // Pocket Pair (e.g. As Ah) => Pick Suits 0 and 1
                int c1 = r1 * 4 + 0;
                int c2 = r1 * 4 + 1;
                distinct_hands.push_back({c1, c2, get_rank(r1) + get_rank(r2), 0.0});
            } else {
                // Suited (e.g. As Ks) => Pick Suits 0 and 0
                int c1_s = r1 * 4 + 0;
                int c2_s = r2 * 4 + 0;
                distinct_hands.push_back({c1_s, c2_s, get_rank(r1) + get_rank(r2) + "s", 0.0});

                // Offsuit (e.g. As Kh) => Pick Suits 0 and 1
                int c1_o = r1 * 4 + 0;
                int c2_o = r2 * 4 + 1;
                distinct_hands.push_back({c1_o, c2_o, get_rank(r1) + get_rank(r2) + "o", 0.0});
            }
        }
    }

    cout << "Starting Preflop Monte Carlo for 169 Distinct Hands...\n";
    cout << "10,000 sims per hand. Scheduled for P/E cores.\n\n";

    auto start_time = chrono::high_resolution_clock::now();

    // -------------------------------------------------------------------------
    // HYBRID CPU OPTIMIZATION: schedule(guided)
    // Feeds tasks dynamically so P-Cores finish faster and take over from E-Cores
    // -------------------------------------------------------------------------
    #pragma omp parallel for schedule(guided)
    for (size_t h = 0; h < distinct_hands.size(); ++h) {
        int hero_c1 = distinct_hands[h].card1;
        int hero_c2 = distinct_hands[h].card2;

        // Remaining deck of 50 cards
        int remaining_deck[50];
        int idx = 0;
        for (int i = 0; i < 52; ++i) {
            if (i != hero_c1 && i != hero_c2) {
                remaining_deck[idx++] = i;
            }
        }

        mt19937 rng(1337 + omp_get_thread_num() + h); // Thread-safe random engine
        int hero_wins = 0;
        int ties = 0;

        for (int s = 0; s < SIMULATIONS_PER_HAND; ++s) {
            int deck[50];
            copy(begin(remaining_deck), end(remaining_deck), begin(deck));

            // We only need 7 cards (2 for villain + 5 for the board)
            for (int k = 0; k < 7; ++k) {
                uniform_int_distribution<int> dist(k, 49);
                swap(deck[k], deck[dist(rng)]);
            }

            int v1 = deck[0], v2 = deck[1];
            int b1 = deck[2], b2 = deck[3], b3 = deck[4], b4 = deck[5], b5 = deck[6];

            int hero_score = evaluate_7_cards(hero_c1, hero_c2, b1, b2, b3, b4, b5);
            int villain_score = evaluate_7_cards(v1, v2, b1, b2, b3, b4, b5);

            if (hero_score > villain_score) hero_wins++;
            else if (hero_score == villain_score) ties++;
        }

        double equity = (hero_wins + (ties / 2.0)) / static_cast<double>(SIMULATIONS_PER_HAND);
        distinct_hands[h].equity = equity;
    }

    auto end_time = chrono::high_resolution_clock::now();
    chrono::duration<double> duration = end_time - start_time;

    // Output sample results (top tier hands to prove it works)
    cout << "--- Hand Equities (First 10) ---\n";
    for(int i = 0; i < 10; i++) {
        cout << setw(3) << distinct_hands[i].name << " -> " 
             << fixed << setprecision(2) << (distinct_hands[i].equity * 100) << "%\n";
    }

    cout << "\nTotal evaluations: " << (169 * SIMULATIONS_PER_HAND) << " (" 
         << (169 * SIMULATIONS_PER_HAND) / 1000000.0 << " Million)\n";
    cout << "Time elapsed: " << fixed << setprecision(5) << duration.count() << " seconds.\n";

    return 0;
}