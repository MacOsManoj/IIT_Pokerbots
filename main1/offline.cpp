/*
 * Offline CFR (Counterfactual Regret Minimization) Precomputation
 * for Sneak Peek Hold'em
 * 
 * This computes GTO strategies for all abstracted information sets.
 * Output: strategy map with action probability distributions.
 */

#include <iostream>
#include <fstream>
#include <iomanip>
#include <vector>
#include <array>
#include <map>
#include <unordered_map>
#include <string>
#include <random>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <chrono>
#include <mutex>
#include <atomic>
#include <thread>

#ifdef _OPENMP
#include <omp.h>
#else
// Fallback for non-OpenMP builds
inline int omp_get_thread_num() { return 0; }
inline int omp_get_num_threads() { return 1; }
#endif

// ============================================================================
// CONSTANTS
// ============================================================================

constexpr int NUM_CARDS = 52;
constexpr int NUM_SUITS = 4;
constexpr int NUM_RANKS = 13;
constexpr int STARTING_STACK = 5000;
constexpr int BIG_BLIND = 20;
constexpr int SMALL_BLIND = 10;

// CFR parameters
constexpr int NUM_ITERATIONS = 1000000;
constexpr int CHECKPOINT_INTERVAL = 10000;

// Action abstraction
constexpr int NUM_BET_SIZES = 4;  // fold, call/check, half-pot, pot
constexpr int NUM_BID_SIZES = 5;  // 0, 25%, 50%, 75%, 100% of pot

// ============================================================================
// CARD REPRESENTATION
// ============================================================================

// Card: 0-51, where card / 4 = rank (0=2, 12=A), card % 4 = suit
inline int make_card(int rank, int suit) { return rank * 4 + suit; }
inline int get_rank(int card) { return card / 4; }
inline int get_suit(int card) { return card % 4; }

const char* RANK_CHARS = "23456789TJQKA";
const char* SUIT_CHARS = "cdhs";

std::string card_to_string(int card) {
    std::string s;
    s += RANK_CHARS[get_rank(card)];
    s += SUIT_CHARS[get_suit(card)];
    return s;
}

int string_to_card(const std::string& s) {
    int rank = strchr(RANK_CHARS, s[0]) - RANK_CHARS;
    int suit = strchr(SUIT_CHARS, s[1]) - SUIT_CHARS;
    return make_card(rank, suit);
}

// ============================================================================
// HAND EVALUATION (7-card evaluator)
// ============================================================================

// Hand rankings
enum HandRank {
    HIGH_CARD = 0,
    ONE_PAIR = 1,
    TWO_PAIR = 2,
    THREE_OF_A_KIND = 3,
    STRAIGHT = 4,
    FLUSH = 5,
    FULL_HOUSE = 6,
    FOUR_OF_A_KIND = 7,
    STRAIGHT_FLUSH = 8
};

// Returns hand strength (higher = better). Uses standard 7-card evaluation.
int evaluate_hand(const std::vector<int>& cards) {
    // Count ranks and suits
    std::array<int, NUM_RANKS> rank_count = {0};
    std::array<int, NUM_SUITS> suit_count = {0};
    std::array<std::vector<int>, NUM_SUITS> cards_by_suit;
    
    for (int card : cards) {
        int rank = get_rank(card);
        int suit = get_suit(card);
        rank_count[rank]++;
        suit_count[suit]++;
        cards_by_suit[suit].push_back(rank);
    }
    
    // Check for flush
    int flush_suit = -1;
    for (int s = 0; s < NUM_SUITS; s++) {
        if (suit_count[s] >= 5) {
            flush_suit = s;
            break;
        }
    }
    
    // Check for straight (including wheel A-2-3-4-5)
    auto check_straight = [](const std::array<int, NUM_RANKS>& counts) -> int {
        // Check A-high straight down to 5-high wheel
        for (int high = 12; high >= 3; high--) {
            bool is_straight = true;
            for (int i = 0; i < 5; i++) {
                int rank = (high - i + 13) % 13;
                if (high == 3 && i == 4) rank = 12; // A for wheel
                if (counts[rank] == 0) {
                    is_straight = false;
                    break;
                }
            }
            if (is_straight) return high;
        }
        return -1;
    };
    
    int straight_high = check_straight(rank_count);
    
    // Check for straight flush
    if (flush_suit >= 0) {
        std::array<int, NUM_RANKS> flush_ranks = {0};
        for (int r : cards_by_suit[flush_suit]) {
            flush_ranks[r]++;
        }
        int sf_high = check_straight(flush_ranks);
        if (sf_high >= 0) {
            return STRAIGHT_FLUSH * 10000000 + sf_high;
        }
    }
    
    // Count pairs, trips, quads
    int quads = -1, trips = -1, pair1 = -1, pair2 = -1;
    for (int r = 12; r >= 0; r--) {
        if (rank_count[r] == 4) quads = r;
        else if (rank_count[r] == 3) { if (trips < 0) trips = r; }
        else if (rank_count[r] == 2) {
            if (pair1 < 0) pair1 = r;
            else if (pair2 < 0) pair2 = r;
        }
    }
    
    // Four of a kind
    if (quads >= 0) {
        int kicker = -1;
        for (int r = 12; r >= 0; r--) {
            if (r != quads && rank_count[r] > 0) { kicker = r; break; }
        }
        return FOUR_OF_A_KIND * 10000000 + quads * 100 + kicker;
    }
    
    // Full house
    if (trips >= 0 && pair1 >= 0) {
        return FULL_HOUSE * 10000000 + trips * 100 + pair1;
    }
    
    // Flush
    if (flush_suit >= 0) {
        std::vector<int> flush_cards = cards_by_suit[flush_suit];
        std::sort(flush_cards.rbegin(), flush_cards.rend());
        int score = FLUSH * 10000000;
        for (int i = 0; i < 5; i++) {
            score += flush_cards[i] * pow(13, 4 - i);
        }
        return score;
    }
    
    // Straight
    if (straight_high >= 0) {
        return STRAIGHT * 10000000 + straight_high;
    }
    
    // Three of a kind
    if (trips >= 0) {
        std::vector<int> kickers;
        for (int r = 12; r >= 0; r--) {
            if (r != trips && rank_count[r] > 0) {
                for (int i = 0; i < rank_count[r] && kickers.size() < 2; i++) {
                    kickers.push_back(r);
                }
            }
        }
        return THREE_OF_A_KIND * 10000000 + trips * 10000 + kickers[0] * 100 + kickers[1];
    }
    
    // Two pair
    if (pair1 >= 0 && pair2 >= 0) {
        int kicker = -1;
        for (int r = 12; r >= 0; r--) {
            if (r != pair1 && r != pair2 && rank_count[r] > 0) { kicker = r; break; }
        }
        return TWO_PAIR * 10000000 + pair1 * 10000 + pair2 * 100 + kicker;
    }
    
    // One pair
    if (pair1 >= 0) {
        std::vector<int> kickers;
        for (int r = 12; r >= 0; r--) {
            if (r != pair1 && rank_count[r] > 0) {
                for (int i = 0; i < rank_count[r] && kickers.size() < 3; i++) {
                    kickers.push_back(r);
                }
            }
        }
        return ONE_PAIR * 10000000 + pair1 * 100000 + kickers[0] * 1000 + kickers[1] * 10 + kickers[2];
    }
    
    // High card
    std::vector<int> high_cards;
    for (int r = 12; r >= 0; r--) {
        for (int i = 0; i < rank_count[r] && high_cards.size() < 5; i++) {
            high_cards.push_back(r);
        }
    }
    int score = HIGH_CARD * 10000000;
    for (int i = 0; i < 5; i++) {
        score += high_cards[i] * pow(13, 4 - i);
    }
    return score;
}

// ============================================================================
// CARD ABSTRACTION (Equity Bucketing)
// ============================================================================

// Number of buckets per street
constexpr int PREFLOP_BUCKETS = 169;  // All unique starting hands
constexpr int FLOP_BUCKETS = 6;
constexpr int TURN_BUCKETS = 6;
constexpr int RIVER_BUCKETS = 6;

// Calculate hand equity via Monte Carlo simulation
double calculate_equity(const std::vector<int>& hole_cards, 
                        const std::vector<int>& board,
                        int num_simulations = 1000) {
    std::vector<bool> used(NUM_CARDS, false);
    for (int c : hole_cards) used[c] = true;
    for (int c : board) used[c] = true;
    
    std::vector<int> deck;
    for (int i = 0; i < NUM_CARDS; i++) {
        if (!used[i]) deck.push_back(i);
    }
    
    std::random_device rd;
    std::mt19937 rng(rd());
    
    int wins = 0, ties = 0, total = 0;
    
    for (int sim = 0; sim < num_simulations; sim++) {
        std::shuffle(deck.begin(), deck.end(), rng);
        
        // Deal remaining board cards
        std::vector<int> full_board = board;
        int deck_idx = 0;
        while (full_board.size() < 5) {
            full_board.push_back(deck[deck_idx++]);
        }
        
        // Deal opponent hole cards
        std::vector<int> opp_hole = {deck[deck_idx], deck[deck_idx + 1]};
        
        // Evaluate both hands
        std::vector<int> my_hand = hole_cards;
        my_hand.insert(my_hand.end(), full_board.begin(), full_board.end());
        
        std::vector<int> opp_hand = opp_hole;
        opp_hand.insert(opp_hand.end(), full_board.begin(), full_board.end());
        
        int my_score = evaluate_hand(my_hand);
        int opp_score = evaluate_hand(opp_hand);
        
        if (my_score > opp_score) wins++;
        else if (my_score == opp_score) ties++;
        total++;
    }
    
    return (wins + 0.5 * ties) / total;
}

// Convert equity to bucket (0 to num_buckets-1)
int equity_to_bucket(double equity, int num_buckets) {
    int bucket = static_cast<int>(equity * num_buckets);
    return std::min(bucket, num_buckets - 1);
}

// ============================================================================
// INFORMATION SET ABSTRACTION
// ============================================================================

struct InfoSet {
    int street;           // 0=preflop, 1=auction, 2=flop, 3=turn, 4=river
    int equity_bucket;    // Hand strength bucket
    int pot_bucket;       // Pot size relative to starting stack
    int position;         // 0=SB/OOP, 1=BB/IP
    std::string action_history;  // Compressed action sequence
    bool has_info_advantage;     // Won auction (sees opp cards)
    
    std::string to_key() const {
        return std::to_string(street) + "_" +
               std::to_string(equity_bucket) + "_" +
               std::to_string(pot_bucket) + "_" +
               std::to_string(position) + "_" +
               action_history + "_" +
               (has_info_advantage ? "1" : "0");
    }
};

// ============================================================================
// CFR NODE
// ============================================================================

struct CFRNode {
    int num_actions;
    std::vector<double> regret_sum;
    std::vector<double> strategy_sum;
    
    CFRNode(int n_actions) : num_actions(n_actions) {
        regret_sum.resize(n_actions, 0.0);
        strategy_sum.resize(n_actions, 0.0);
    }
    
    // Get current strategy via regret matching
    std::vector<double> get_strategy() {
        std::vector<double> strategy(num_actions);
        double normalizing_sum = 0;
        
        for (int a = 0; a < num_actions; a++) {
            strategy[a] = std::max(regret_sum[a], 0.0);
            normalizing_sum += strategy[a];
        }
        
        for (int a = 0; a < num_actions; a++) {
            if (normalizing_sum > 0) {
                strategy[a] /= normalizing_sum;
            } else {
                strategy[a] = 1.0 / num_actions;
            }
        }
        
        return strategy;
    }
    
    // Get average strategy (the converged GTO strategy)
    std::vector<double> get_average_strategy() {
        std::vector<double> avg_strategy(num_actions);
        double normalizing_sum = 0;
        
        for (int a = 0; a < num_actions; a++) {
            normalizing_sum += strategy_sum[a];
        }
        
        for (int a = 0; a < num_actions; a++) {
            if (normalizing_sum > 0) {
                avg_strategy[a] = strategy_sum[a] / normalizing_sum;
            } else {
                avg_strategy[a] = 1.0 / num_actions;
            }
        }
        
        return avg_strategy;
    }
};

// ============================================================================
// GAME STATE
// ============================================================================

// Correct sequence: PREFLOP -> FLOP dealt -> AUCTION -> FLOP_BET -> TURN -> RIVER
enum Street { PREFLOP = 0, AUCTION = 1, FLOP_BET = 2, TURN = 3, RIVER = 4 };

struct GameState {
    Street street;
    int actions_this_round;        // Number of actions taken this betting round
    int first_actor;               // Who acts first (0=SB preflop, 1=BB postflop)
    std::array<int, 2> wagers;     // Current round wagers
    std::array<int, 2> chips;      // Remaining chips
    std::array<int, 2> bids;       // Auction bids (-1 = not yet bid)
    std::vector<int> board;        // Community cards (0 preflop, 3 after flop, 4 turn, 5 river)
    std::array<std::vector<int>, 2> hole_cards;  // Each player's hole cards
    std::array<bool, 2> sees_opp;  // Whether player can see opponent's cards
    std::array<int, 2> revealed_card; // Which card is revealed to opponent (-1 if none)
    std::string action_history;
    int folded_player;             // -1 if no fold, else 0 or 1
    bool hand_over;                // True when hand is complete (showdown ready)
    
    bool is_terminal() const {
        // Terminal if someone folded or hand is over (reached showdown)
        if (folded_player >= 0) return true;
        if (hand_over) return true;
        return false;
    }
    
    int active_player() const {
        // Preflop: SB(0) acts first. Postflop: BB(1) acts first.
        return (first_actor + actions_this_round) % 2;
    }
    
    int pot() const {
        return 2 * STARTING_STACK - chips[0] - chips[1];
    }
    
    // Get min raise per rules: Current Wager + Cost to Call + max(BB, Cost to Call)
    int get_min_raise() const {
        int active = active_player();
        int cost = wagers[1 - active] - wagers[active];
        int min_raise_to = wagers[active] + cost + std::max(BIG_BLIND, cost);
        return std::min(min_raise_to, wagers[active] + chips[active]);
    }
    
    // Get max raise per rules: min(Your Chips, Opponent's Chips)
    int get_max_raise() const {
        int active = active_player();
        int cost = wagers[1 - active] - wagers[active];
        // Max you can bet = min of both stacks (so opponent can call)
        return wagers[active] + std::min(chips[active], chips[1-active] + cost);
    }
};

// ============================================================================
// MCCFR TRAINER
// ============================================================================

class MCCFRTrainer {
private:
    std::unordered_map<std::string, CFRNode> node_map;
    std::mutex node_mutex;  // Protects node_map access
    std::atomic<int> completed_iterations{0};
    
public:
    MCCFRTrainer() {}
    
    // Get or create CFR node for information set (thread-safe)
    CFRNode& get_node(const std::string& info_set_key, int num_actions) {
        std::lock_guard<std::mutex> lock(node_mutex);
        auto it = node_map.find(info_set_key);
        if (it == node_map.end()) {
            it = node_map.emplace(info_set_key, CFRNode(num_actions)).first;
        }
        return it->second;
    }
    
    // Update node regrets (thread-safe)
    void update_regrets(const std::string& key, int action, double regret) {
        std::lock_guard<std::mutex> lock(node_mutex);
        auto it = node_map.find(key);
        if (it != node_map.end()) {
            it->second.regret_sum[action] += regret;
        }
    }
    
    // Update strategy sum (thread-safe)
    void update_strategy_sum(const std::string& key, const std::vector<double>& weighted_strategy) {
        std::lock_guard<std::mutex> lock(node_mutex);
        auto it = node_map.find(key);
        if (it != node_map.end()) {
            for (size_t a = 0; a < weighted_strategy.size(); a++) {
                it->second.strategy_sum[a] += weighted_strategy[a];
            }
        }
    }
    
    size_t get_node_count() {
        std::lock_guard<std::mutex> lock(node_mutex);
        return node_map.size();
    }
    
    // Get valid actions for current state
    // Actions: 0=fold, 1=check/call, 2=raise half pot, 3=raise pot, 4=all-in
    // Auction: 0-4 = bid percentage of pot (0%, 25%, 50%, 75%, 100%)
    std::vector<int> get_valid_actions(const GameState& state) {
        std::vector<int> actions;
        
        if (state.street == AUCTION) {
            // Bid sizes: 0, 25%, 50%, 75%, 100% of pot
            for (int i = 0; i < NUM_BID_SIZES; i++) {
                actions.push_back(i);
            }
        } else {
            int active = state.active_player();
            int cost = state.wagers[1 - active] - state.wagers[active];
            
            if (cost == 0) {
                // No bet to face - can check or bet
                actions.push_back(1);  // Check
                // Can only bet if both players have chips
                if (state.chips[active] > 0 && state.chips[1 - active] > 0) {
                    actions.push_back(2);  // Bet half pot
                    actions.push_back(3);  // Bet pot
                    if (state.chips[active] > state.pot()) {
                        actions.push_back(4);  // All-in
                    }
                }
            } else {
                // Facing a bet - can fold, call, or raise
                actions.push_back(0);  // Fold
                actions.push_back(1);  // Call
                // Can only raise if we'd have chips left after calling and opponent can call
                bool can_raise = (cost < state.chips[active]) && (state.chips[1 - active] > 0);
                if (can_raise) {
                    actions.push_back(2);  // Raise half pot
                    actions.push_back(3);  // Raise pot
                    if (state.chips[active] > cost + state.pot()) {
                        actions.push_back(4);  // All-in
                    }
                }
            }
        }
        
        return actions;
    }
    
    // External sampling MCCFR (with thread-local RNG)
    double cfr(GameState& state, int traverser, double p0, double p1, std::mt19937& rng, int depth = 0) {
        // Safety: limit recursion depth
        if (depth > 100) {
            return 0.0;
        }
        
        if (state.is_terminal()) {
            // Return payoff for traverser
            return calculate_terminal_payoff(state, traverser);
        }
        
        // Handle all-in: if both players are all-in, run to showdown
        if (state.chips[0] == 0 && state.chips[1] == 0 && state.street != AUCTION) {
            // Skip to showdown
            state.hand_over = true;
            return calculate_terminal_payoff(state, traverser);
        }
        
        int active = state.active_player();
        std::vector<int> actions = get_valid_actions(state);
        int num_actions = actions.size();
        
        // Safety: if no actions available, treat as terminal
        if (num_actions == 0) {
            return calculate_terminal_payoff(state, traverser);
        }
        
        // Build information set key
        InfoSet info_set;
        info_set.street = static_cast<int>(state.street);
        info_set.equity_bucket = get_equity_bucket(state, active);
        info_set.pot_bucket = std::min(state.pot() / (STARTING_STACK / 10), 10);
        info_set.position = active;
        info_set.action_history = state.action_history;
        info_set.has_info_advantage = state.sees_opp[active];
        
        std::string key = info_set.to_key();
        CFRNode& node = get_node(key, num_actions);
        
        std::vector<double> strategy = node.get_strategy();
        
        if (active == traverser) {
            // Traverser's turn: compute counterfactual values for all actions
            std::vector<double> action_values(num_actions, 0.0);
            double node_value = 0.0;
            
            for (int a = 0; a < num_actions; a++) {
                GameState next_state = apply_action(state, actions[a], rng);
                action_values[a] = cfr(next_state, traverser, p0 * strategy[a], p1, rng, depth + 1);
                node_value += strategy[a] * action_values[a];
            }
            
            // Update regrets (thread-safe)
            for (int a = 0; a < num_actions; a++) {
                double regret = action_values[a] - node_value;
                double weighted_regret = (active == 0 ? p1 : p0) * regret;
                update_regrets(key, a, weighted_regret);
            }
            
            return node_value;
        } else {
            // Opponent's turn: sample according to strategy
            double r = std::uniform_real_distribution<>(0, 1)(rng);
            double cumulative = 0.0;
            int sampled_action = num_actions - 1;
            
            for (int a = 0; a < num_actions; a++) {
                cumulative += strategy[a];
                if (r < cumulative) {
                    sampled_action = a;
                    break;
                }
            }
            
            // Update strategy sum for average strategy (thread-safe)
            std::vector<double> weighted_strategy(num_actions);
            for (int a = 0; a < num_actions; a++) {
                weighted_strategy[a] = (active == 0 ? p0 : p1) * strategy[a];
            }
            update_strategy_sum(key, weighted_strategy);
            
            GameState next_state = apply_action(state, actions[sampled_action], rng);
            return cfr(next_state, traverser, p0, p1 * strategy[sampled_action], rng, depth + 1);
        }
    }
    
    // Apply action to state - FULL implementation
    GameState apply_action(const GameState& state, int action, std::mt19937& rng) {
        GameState next = state;
        next.action_history += std::to_string(action);
        
        if (state.street == AUCTION) {
            // Auction bid
            int active = state.active_player();
            int pot = state.pot();
            // Bid amounts: 0%, 25%, 50%, 75%, 100% of pot
            int bid_amount = (action * pot) / 4;
            next.bids[active] = bid_amount;
            next.actions_this_round++;
            
            // Check if both players have bid
            if (next.bids[0] >= 0 && next.bids[1] >= 0) {
                // Resolve auction with second-price mechanism
                resolve_auction(next, rng);
                // Move to flop betting, BB acts first
                next.street = FLOP_BET;
                next.actions_this_round = 0;
                next.first_actor = 1;  // BB acts first postflop
                next.wagers = {0, 0};  // Reset wagers for new betting round
            }
            return next;
        }
        
        // Betting action
        int active = state.active_player();
        int cost = state.wagers[1 - active] - state.wagers[active];
        
        if (action == 0) {
            // Fold
            next.folded_player = active;
            return next;
        }
        
        if (action == 1) {
            // Check (cost=0) or Call (cost>0)
            if (cost > 0) {
                next.chips[active] -= cost;
                next.wagers[active] += cost;
            }
            next.actions_this_round++;
            
            // Check if betting round ends
            if (should_end_betting_round(state, next)) {
                advance_street(next);
            }
            return next;
        }
        
        // Raise/Bet actions (2=half pot, 3=pot, 4=all-in)
        int pot = state.pot();
        int raise_amount;
        if (action == 2) {
            raise_amount = std::max(pot / 2, state.get_min_raise() - state.wagers[active]);
        } else if (action == 3) {
            raise_amount = std::max(pot, state.get_min_raise() - state.wagers[active]);
        } else {
            // All-in
            raise_amount = state.chips[active];
        }
        
        // Clamp to valid range
        int min_raise = state.get_min_raise();
        int max_raise = state.get_max_raise();
        int target_wager = std::min(std::max(state.wagers[active] + raise_amount, min_raise), max_raise);
        int actual_add = target_wager - state.wagers[active];
        
        next.chips[active] -= actual_add;
        next.wagers[active] = target_wager;
        next.actions_this_round++;
        
        return next;
    }
    
    // Resolve second-price auction
    void resolve_auction(GameState& state, std::mt19937& rng) {
        int bid0 = state.bids[0];
        int bid1 = state.bids[1];
        
        if (bid0 > bid1) {
            // Player 0 wins, pays player 1's bid (second-price)
            state.chips[0] -= bid1;
            state.sees_opp[0] = true;
            // Reveal random card of opponent
            state.revealed_card[0] = rng() % 2;  // 0 or 1
        } else if (bid1 > bid0) {
            // Player 1 wins, pays player 0's bid (second-price)
            state.chips[1] -= bid0;
            state.sees_opp[1] = true;
            state.revealed_card[1] = rng() % 2;
        } else {
            // Tie: both pay their bids, both see one card
            state.chips[0] -= bid0;
            state.chips[1] -= bid1;
            state.sees_opp[0] = true;
            state.sees_opp[1] = true;
            state.revealed_card[0] = rng() % 2;
            state.revealed_card[1] = rng() % 2;
        }
    }
    
    // Check if betting round should end
    bool should_end_betting_round(const GameState& old_state, const GameState& new_state) {
        // Special case: preflop SB calls BB, BB gets option to act
        if (old_state.street == PREFLOP && old_state.actions_this_round == 0) {
            // SB just acted (called or raised)
            // If SB just called (wagers now equal at BB), BB gets option
            return false;
        }
        
        // Round ends when wagers are equal and at least 2 actions taken
        // (or when both check: cost was 0 and still 0)
        if (new_state.wagers[0] == new_state.wagers[1] && new_state.actions_this_round >= 2) {
            return true;
        }
        
        // Also ends if someone is all-in and other called
        if ((new_state.chips[0] == 0 || new_state.chips[1] == 0) && 
            new_state.wagers[0] == new_state.wagers[1]) {
            return true;
        }
        
        return false;
    }
    
    // Advance to next street
    void advance_street(GameState& state) {
        state.wagers = {0, 0};
        state.actions_this_round = 0;
        state.first_actor = 1;  // BB acts first postflop
        
        switch (state.street) {
            case PREFLOP:
                // Deal flop (3 cards) then go to auction
                state.street = AUCTION;
                state.first_actor = 0;  // SB bids first (simultaneous anyway)
                break;
            case FLOP_BET:
                // Deal turn card
                state.street = TURN;
                break;
            case TURN:
                // Deal river card
                state.street = RIVER;
                break;
            case RIVER:
                // Hand is complete - mark as terminal
                state.hand_over = true;
                break;
            default:
                break;
        }
    }
    
    // Calculate terminal payoff
    double calculate_terminal_payoff(const GameState& state, int player) {
        int pot = state.pot();
        
        // Handle fold
        if (state.folded_player >= 0) {
            // Player who didn't fold wins
            int winner = 1 - state.folded_player;
            // Return profit/loss from perspective of 'player'
            if (player == winner) {
                return (STARTING_STACK - state.chips[player]);  // They put in this much, win pot
            } else {
                return -(STARTING_STACK - state.chips[player]); // They lose what they put in
            }
        }
        
        // Showdown: evaluate hands
        std::vector<int> hand0 = state.hole_cards[0];
        hand0.insert(hand0.end(), state.board.begin(), state.board.end());
        
        std::vector<int> hand1 = state.hole_cards[1];
        hand1.insert(hand1.end(), state.board.begin(), state.board.end());
        
        int score0 = evaluate_hand(hand0);
        int score1 = evaluate_hand(hand1);
        
        // Calculate each player's investment
        int invested0 = STARTING_STACK - state.chips[0];
        int invested1 = STARTING_STACK - state.chips[1];
        
        if (score0 > score1) {
            // Player 0 wins
            return player == 0 ? invested1 : -invested1;
        } else if (score1 > score0) {
            // Player 1 wins
            return player == 1 ? invested0 : -invested0;
        }
        // Tie - split pot, return 0 profit
        return 0;
    }
    
    // Get equity bucket for current player
    int get_equity_bucket(const GameState& state, int player) {
        // Get visible board cards based on street
        std::vector<int> visible_board;
        if (state.street == PREFLOP) {
            // No board cards visible
        } else if (state.street == AUCTION || state.street == FLOP_BET) {
            // Flop visible (3 cards)
            visible_board = {state.board[0], state.board[1], state.board[2]};
        } else if (state.street == TURN) {
            // Turn visible (4 cards)
            visible_board = {state.board[0], state.board[1], state.board[2], state.board[3]};
        } else {
            // River visible (5 cards)
            visible_board = state.board;
        }
        
        double equity = calculate_equity(state.hole_cards[player], visible_board, 500);
        
        int num_buckets;
        switch (state.street) {
            case PREFLOP: num_buckets = PREFLOP_BUCKETS; break;
            case AUCTION: num_buckets = FLOP_BUCKETS; break;
            case FLOP_BET: num_buckets = FLOP_BUCKETS; break;
            case TURN: num_buckets = TURN_BUCKETS; break;
            case RIVER: num_buckets = RIVER_BUCKETS; break;
            default: num_buckets = 10;
        }
        
        return equity_to_bucket(equity, num_buckets);
    }
    
    // Deal random cards
    GameState deal_random_game(std::mt19937& rng) {
        GameState state;
        state.street = PREFLOP;
        state.actions_this_round = 0;
        state.first_actor = 0;  // SB acts first preflop
        state.wagers = {SMALL_BLIND, BIG_BLIND};
        state.chips = {STARTING_STACK - SMALL_BLIND, STARTING_STACK - BIG_BLIND};
        state.bids = {-1, -1};
        state.sees_opp = {false, false};
        state.revealed_card = {-1, -1};
        state.action_history = "";
        state.folded_player = -1;
        state.hand_over = false;
        
        // Shuffle deck
        std::vector<int> deck(NUM_CARDS);
        std::iota(deck.begin(), deck.end(), 0);
        std::shuffle(deck.begin(), deck.end(), rng);
        
        // Deal hole cards
        state.hole_cards[0] = {deck[0], deck[1]};
        state.hole_cards[1] = {deck[2], deck[3]};
        
        // Pre-deal full board (revealed as game progresses)
        // Board is always 5 cards; visibility depends on street
        state.board = {deck[4], deck[5], deck[6], deck[7], deck[8]};
        
        return state;
    }
    
    // Print progress bar
    void print_progress_bar(int current, int total, double elapsed_secs, size_t nodes, int bar_width = 50) {
        float progress = static_cast<float>(current) / total;
        int filled = static_cast<int>(progress * bar_width);
        
        // Calculate ETA
        double rate = current / elapsed_secs;
        double eta = (total - current) / rate;
        
        // Build progress bar
        std::cout << "\r[";
        for (int i = 0; i < bar_width; i++) {
            if (i < filled) std::cout << "█";
            else if (i == filled) std::cout << "▓";
            else std::cout << "░";
        }
        std::cout << "] ";
        
        // Percentage
        std::cout << std::fixed << std::setprecision(1) << (progress * 100.0) << "% ";
        
        // Stats
        std::cout << "| " << current << "/" << total << " ";
        std::cout << "| Nodes: " << nodes << " ";
        std::cout << "| " << std::setprecision(0) << rate << " it/s ";
        
        // ETA
        if (eta < 60) {
            std::cout << "| ETA: " << std::setprecision(0) << eta << "s";
        } else if (eta < 3600) {
            std::cout << "| ETA: " << std::setprecision(1) << (eta / 60) << "m";
        } else {
            std::cout << "| ETA: " << std::setprecision(1) << (eta / 3600) << "h";
        }
        
        std::cout << "   " << std::flush;
    }
    
    // Train for N iterations (parallelized with OpenMP)
    void train(int num_iterations) {
        int num_threads = std::thread::hardware_concurrency();
        if (num_threads == 0) num_threads = 4;  // Default fallback
        
        std::cout << "Starting MCCFR training for " << num_iterations << " iterations...\n";
        std::cout << "Using " << num_threads << " threads\n\n";
        
        auto start_time = std::chrono::high_resolution_clock::now();
        int update_interval = std::max(1, std::min(10, num_iterations / 100));  // Update at least every 10 iterations
        
        completed_iterations = 0;
        
        #pragma omp parallel num_threads(num_threads)
        {
            // Thread-local RNG with unique seed per thread
            std::mt19937 local_rng(std::random_device{}() + omp_get_thread_num());
            
            #pragma omp for schedule(dynamic, 100)
            for (int i = 0; i < num_iterations; i++) {
                // Deal new random game
                GameState state = deal_random_game(local_rng);
                
                // Run CFR from both player perspectives
                cfr(state, 0, 1.0, 1.0, local_rng);
                state = deal_random_game(local_rng);
                cfr(state, 1, 1.0, 1.0, local_rng);
                
                // Update progress (only from thread 0)
                int completed = ++completed_iterations;
                if (omp_get_thread_num() == 0 && completed % update_interval == 0) {
                    auto current_time = std::chrono::high_resolution_clock::now();
                    double elapsed = std::chrono::duration<double>(current_time - start_time).count();
                    print_progress_bar(completed, num_iterations, elapsed, get_node_count());
                }
            }
        }
        
        auto end_time = std::chrono::high_resolution_clock::now();
        double total_time = std::chrono::duration<double>(end_time - start_time).count();
        
        // Final progress update
        print_progress_bar(num_iterations, num_iterations, total_time, get_node_count());
        
        std::cout << "\n\nTraining complete!\n";
        std::cout << "Total nodes: " << get_node_count() << "\n";
        std::cout << "Total time: " << std::fixed << std::setprecision(1) << total_time << "s\n";
        std::cout << "Speed: " << std::setprecision(0) << (num_iterations / total_time) << " it/s\n";
    }
    
    // Save strategy as Python dict (for direct paste into mccfr.py)
    void save_strategy(const std::string& filename) {
        std::ofstream file(filename);
        
        if (!file.is_open()) {
            std::cerr << "Error: Could not open file " << filename << "\n";
            return;
        }
        
        std::cout << "Saving strategy to " << filename << "...\n";
        
        // Output as Python dict literal
        file << "# Auto-generated CFR strategy - paste this into mccfr.py\n";
        file << "STRATEGY = {\n";
        
        bool first = true;
        for (auto& [key, node] : node_map) {
            std::vector<double> strategy = node.get_average_strategy();
            
            // Skip uniform/near-uniform strategies to save space
            double avg = 1.0 / node.num_actions;
            bool is_uniform = true;
            for (int a = 0; a < node.num_actions; a++) {
                if (std::abs(strategy[a] - avg) > 0.05) {
                    is_uniform = false;
                    break;
                }
            }
            if (is_uniform) continue;
            
            if (!first) file << ",\n";
            first = false;
            
            file << "    \"" << key << "\": [";
            for (int a = 0; a < node.num_actions; a++) {
                file << std::fixed << std::setprecision(2) << strategy[a];
                if (a < node.num_actions - 1) file << ", ";
            }
            file << "]";
        }
        
        file << "\n}\n";
        
        file.close();
        std::cout << "Strategy saved successfully. Copy contents into mccfr.py\n";
    }
};

// ============================================================================
// MAIN
// ============================================================================

int main(int argc, char* argv[]) {
    int iterations = NUM_ITERATIONS;
    std::string output_file = "cfr_strategy.py";
    
    // Parse command line arguments
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "-i" && i + 1 < argc) {
            iterations = std::stoi(argv[++i]);
        } else if (arg == "-o" && i + 1 < argc) {
            output_file = argv[++i];
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "Usage: " << argv[0] << " [-i iterations] [-o output_file]\n";
            std::cout << "  -i: Number of CFR iterations (default: " << NUM_ITERATIONS << ")\n";
            std::cout << "  -o: Output strategy file (default: cfr_strategy.py)\n";
            return 0;
        }
    }
    
    std::cout << "=== MCCFR Offline Computation for Sneak Peek Hold'em ===\n";
    std::cout << "Iterations: " << iterations << "\n";
    std::cout << "Output: " << output_file << "\n\n";
    
    MCCFRTrainer trainer;
    trainer.train(iterations);
    trainer.save_strategy(output_file);
    
    return 0;
}
