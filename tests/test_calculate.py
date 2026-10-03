"""
Unit tests for the math calculation module (amigo.utils.calculate).
"""

import unittest
from unittest.mock import patch
from amigo.utils.calculate import Calc


class TestCalculate(unittest.TestCase):
    """Test AST arithmetic evaluation and fallback handling in Calc()."""

    def test_addition(self):
        self.assertEqual(Calc("what is 5 + 7"), "12")

    def test_subtraction(self):
        self.assertEqual(Calc("calculate 20 minus 8"), "12")

    def test_multiplication_symbol(self):
        self.assertEqual(Calc("what is 4 * 6"), "24")

    def test_multiplication_x(self):
        self.assertEqual(Calc("what is 4 x 5"), "20")

    def test_multiplication_times(self):
        self.assertEqual(Calc("what is 9 times 3"), "27")

    def test_division(self):
        self.assertEqual(Calc("solve 100 divided by 4"), "25")

    def test_power(self):
        self.assertEqual(Calc("2 to the power of 8"), "256")
        self.assertEqual(Calc("3 ^ 3"), "27")

    def test_decimal_formatting(self):
        self.assertEqual(Calc("7 / 2"), "3.5")

    def test_speak_callback(self):
        spoken = []
        Calc("3 + 4", speak=lambda msg: spoken.append(msg))
        self.assertTrue(len(spoken) > 0)
        self.assertIn("7", spoken[0])

    @patch("amigo.utils.calculate._get_query_local_llm")
    def test_llm_fallback_for_word_problems(self, mock_get_llm):
        mock_llm = mock_get_llm.return_value
        mock_llm.return_value = "There are 15 apples."
        res = Calc("if Alice has 5 apples and gets 10 more, how many does she have?")
        self.assertEqual(res, "There are 15 apples.")


if __name__ == "__main__":
    unittest.main()
