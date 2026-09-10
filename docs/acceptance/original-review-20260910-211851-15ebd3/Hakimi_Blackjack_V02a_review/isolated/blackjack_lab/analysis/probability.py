"""Finite-shoe probabilities, marginalizing a single unobserved dealer hole card.

Counts contain the hole card AND the undealt cards. The hole's conditional
support is fixed by a negative blackjack peek. No actual hole rank is input.
"""
from functools import lru_cache
from time import perf_counter

VALUES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
LABELS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "T")
DEALER_LABELS = ("blackjack", "17", "18", "19", "20", "21", "bust")


class CalculationStopped(Exception):
    pass


class InsufficientCards(ValueError):
    pass


def remove(counts, index):
    return counts[:index] + (counts[index] - 1,) + counts[index + 1:]


def total(hard, ace):
    return hard + 10 if ace and hard <= 11 else hard


class FiniteModel:
    def __init__(self, dealer_up, peek_negative, budget_seconds=5.0, cancelled=None):
        self.up = dealer_up
        self.excluded = 9 if peek_negative and dealer_up == 1 else (0 if peek_negative and dealer_up == 10 else -1)
        self.deadline = perf_counter() + budget_seconds
        self.cancelled = cancelled
        self.nodes = 0
        # Caches are request-local. They never mix rules, information, or hands.
        self.dealer_run = lru_cache(maxsize=250_000)(self._dealer_run)
        self.dealer_distribution = lru_cache(maxsize=50_000)(self._dealer_distribution)

    def check(self):
        self.nodes += 1
        if self.nodes % 256 == 1:
            if self.cancelled is not None and self.cancelled():
                raise CalculationStopped("CANCELLED")
            if perf_counter() >= self.deadline:
                raise CalculationStopped("TIMEOUT")

    def target_draw(self, counts):
        """P(next=r|visible, hole in H) = C_r (C_H-1[r in H]) / C_H/(N-1)."""
        size = sum(counts)
        mass = size - (counts[self.excluded] if self.excluded >= 0 else 0)
        if size < 2 or mass <= 0:
            raise InsufficientCards("没有满足底牌条件的后续抽牌")
        return tuple(n * (mass - (i != self.excluded)) / (mass * (size - 1))
                     for i, n in enumerate(counts))

    def _dealer_distribution(self, counts):
        self.check()
        mass = sum(counts) - (counts[self.excluded] if self.excluded >= 0 else 0)
        if mass <= 0:
            raise InsufficientCards("底牌检查与剩余组成矛盾")
        result = [0.0] * 7
        for i, count in enumerate(counts):
            if not count or i == self.excluded:
                continue
            value = i + 1
            probability = count / mass
            if (self.up, value) in ((1, 10), (10, 1)):
                result[0] += probability
            else:
                hard, ace = self.up + value, self.up == 1 or value == 1
                score = total(hard, ace)
                if score >= 17:
                    result[6 if score > 21 else score - 16] += probability
                    continue
                child = self.dealer_run(remove(counts, i), hard, ace)
                for j, p in enumerate(child):
                    result[j] += probability * p
        return tuple(result)

    def _dealer_run(self, counts, hard, ace):
        self.check()
        score = total(hard, ace)
        if score >= 17:
            result = [0.0] * 7
            result[6 if score > 21 else score - 16] = 1.0
            return tuple(result)
        size = sum(counts)
        if size == 0:
            raise InsufficientCards("庄家尚需补牌但合成小牌靴已耗尽")
        result = [0.0] * 7
        for i, count in enumerate(counts):
            if count:
                probability = count / size
                next_hard, next_ace = hard + i + 1, ace or i == 0
                score = total(next_hard, next_ace)
                # Terminal outcomes need no tuple allocation, cache key, or recursion.
                if score >= 17:
                    result[6 if score > 21 else score - 16] += probability
                    continue
                child = self.dealer_run(remove(counts, i), next_hard, next_ace)
                for j, p in enumerate(child):
                    result[j] += probability * p
        return tuple(result)
