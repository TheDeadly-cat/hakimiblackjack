"""T5 DAS identity and investment vocabulary. Not a production rule template.

Old b1 `research-s17-us-peek-two-sequential-v1` stays no-DAS. This module is the
proposed next-template contract for independent reference tests only.
"""

DAS_PROFILE = "research-s17-us-peek-two-sequential-das-v1"
DAS_INPUT_SCHEMA = "hakimi-split-analysis-input-v1"
DAS_RESULT_SCHEMA = "hakimi-analysis-result-v2"
DAS_ENGINE = "v0.2b2-finite-two-hand-das-ref-1"
DAS_STRATEGY = "sequential-two-hand-total-net-das-v1"
DAS_ORDER = "sequential_complete_first"
DAS_SUPPORT_SCOPE = (
    "S17/3:2/US-peek/zero-burn/single-player/two-sequential/DAS-non-ace/no-resplit"
)

# Per-hand stake after split is 1 until that hand doubles once.
MIN_POST_SPLIT_INVESTMENT = 2
MAX_POST_SPLIT_INVESTMENT = 4  # both non-ace hands double
NET_TOTAL_RANGE = (-4, 4)


def can_das(split_aces, ranks, already_doubled=False):
    """Non-ace split hand, exactly two cards, not already doubled, not 21/bust."""
    if split_aces or already_doubled or ranks is None:
        return False
    if len(ranks) != 2:
        return False
    total = sum(11 if c == 1 else c for c in ranks)
    aces = ranks.count(1)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total < 21


def nets_for_stake(stake):
    return frozenset((-stake, 0, stake))


def investment_view(stakes, action, remaining_das_hands):
    """Separate money already down, this action, and still-optional DAS.

    stakes: current committed units per hand (1 or 2).
    remaining_das_hands: upper bound of DAS units still available after this
    action. stand/hit/double exclude this hand; deal may still include it.
    """
    additional = 1 if action == "double" else 0
    return {
        "current_investment": sum(stakes),
        "additional_investment": additional,
        "possible_future_additional": remaining_das_hands,
        "max_final_investment": sum(stakes) + additional + remaining_das_hands,
    }
