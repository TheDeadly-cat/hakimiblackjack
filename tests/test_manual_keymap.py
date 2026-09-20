"""Keymap: 0=T, 1=A, one key one card, Enter navigates, no 100ms merge."""
import unittest

from blackjack_lab.core.cards import TEN_BUCKET
from blackjack_lab.ui.manual_keymap import (
    KIND_HOLE, KIND_JUMP, KIND_NEXT, KIND_PREV, KIND_RANK, KIND_STAND, KIND_UNDO,
    RepeatGuard, resolve,
)


class TestManualKeymap(unittest.TestCase):
    def test_digit_map(self):
        self.assertEqual(resolve("0").rank, TEN_BUCKET)
        self.assertEqual(resolve("KP_0").rank, TEN_BUCKET)
        self.assertEqual(resolve("1").rank, "A")
        self.assertEqual(resolve("KP_1").rank, "A")
        self.assertEqual(resolve("7").rank, "7")
        self.assertEqual(resolve("a").rank, "A")
        self.assertIsNone(resolve("End"))
        self.assertIsNone(resolve("Left"))

    def test_two_zeroes_are_two_commands(self):
        first = resolve("0")
        second = resolve("0")
        self.assertEqual(first.kind, KIND_RANK)
        self.assertEqual(second.kind, KIND_RANK)
        self.assertEqual(first.rank, TEN_BUCKET)

    def test_enter_is_navigation_not_rank_or_stand(self):
        self.assertEqual(resolve("Return").kind, KIND_NEXT)
        self.assertEqual(resolve("Tab").kind, KIND_NEXT)
        self.assertEqual(resolve("ISO_Left_Tab").kind, KIND_PREV)
        self.assertEqual(resolve("Return", state=0x0001).kind, KIND_PREV)
        self.assertNotEqual(resolve("minus").kind, KIND_NEXT)
        self.assertEqual(resolve("minus").kind, KIND_STAND)

    def test_control_z_is_undo(self):
        self.assertEqual(resolve("z", state=0x0004).kind, KIND_UNDO)
        self.assertEqual(resolve("0", state=0x0004).kind, KIND_JUMP)
        self.assertEqual(resolve("3", state=0x0004).seat_number, 3)
        self.assertEqual(resolve("KP_3", state=0x0004).seat_number, 3)
        self.assertIsNone(resolve("9", state=0x0004))

    def test_hole_and_undo(self):
        self.assertEqual(resolve("period").kind, KIND_HOLE)
        self.assertEqual(resolve("KP_Decimal").kind, KIND_HOLE)
        self.assertEqual(resolve("BackSpace").kind, KIND_UNDO)

    def test_long_press_is_one_command(self):
        guard = RepeatGuard()
        self.assertTrue(guard.accept_press("0"))
        self.assertFalse(guard.accept_press("0"))
        guard.release("0")
        self.assertTrue(guard.accept_press("0"))
        guard.accept_press("7")
        guard.clear()
        self.assertTrue(guard.accept_press("7"))


if __name__ == "__main__":
    unittest.main()
