"""Two sequential hands, one finite shoe and dealer, total-net-EV policy."""
from .native_backend import solve_native


def solve_split_counts(counts, hands, dealer_up, peek_negative, *, active=0,
                       split_aces=False, force_active=False, budget_seconds=5.0,
                       allow_das=False, stakes=(1, 1), force_close=False):
    return solve_native(counts, hands, dealer_up, peek_negative, active=active,
                        split_aces=split_aces, force_active=force_active,
                        budget_seconds=budget_seconds, allow_das=allow_das,
                        stakes=stakes, force_close=force_close)
