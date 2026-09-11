"""Two sequential hands, one finite shoe and dealer, total-net-EV policy."""
from .native_backend import solve_native


def solve_split_counts(counts, hands, dealer_up, peek_negative, *, active=0,
                       split_aces=False, force_active=False, budget_seconds=5.0):
    return solve_native(counts, hands, dealer_up, peek_negative, active=active,
                        split_aces=split_aces, force_active=force_active,
                        budget_seconds=budget_seconds)
