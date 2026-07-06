# services/research/tests/test_strategy.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from strategy import validate_strategy_code, compute_code_hash, load_strategy, execute_strategy

VALID_CODE = """
def generate_signal(snapshot):
    rsi = snapshot["rsi"]
    if rsi < 35:
        return {"decision": "buy", "confidence": 0.78, "reasoning": "RSI oversold."}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "No signal."}
"""

SNAPSHOT = {
    "price": 150.0, "rsi": 30.0, "sma20": 148.0, "sma50": 145.0,
    "sma20_prev": 147.5, "sma20_prev2": 147.0, "volume": 2_000_000, "volume_avg": 1_000_000,
}


class TestValidateStrategyCode(unittest.TestCase):
    def test_valid_code_passes(self):
        validate_strategy_code(VALID_CODE)  # Should not raise

    def test_blocks_import_statement(self):
        code = "import os\ndef generate_signal(s): return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("Import", str(ctx.exception))

    def test_blocks_from_import(self):
        code = "from os import path\ndef generate_signal(s): return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("Import", str(ctx.exception))

    def test_blocks_dunder_import(self):
        code = "def generate_signal(s):\n    __import__('os')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("__import__", str(ctx.exception))

    def test_blocks_open_call(self):
        code = "def generate_signal(s):\n    open('/etc/passwd')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("open", str(ctx.exception))

    def test_blocks_exec_call(self):
        code = "def generate_signal(s):\n    exec('pass')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("exec", str(ctx.exception))

    def test_blocks_eval_call(self):
        code = "def generate_signal(s):\n    eval('1+1')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("eval", str(ctx.exception))

    def test_blocks_os_name_reference(self):
        code = "def generate_signal(s):\n    x = os\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("os", str(ctx.exception))

    def test_rejects_syntax_error(self):
        code = "def generate_signal(s):\n    return {"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("Syntax", str(ctx.exception))


class TestComputeCodeHash(unittest.TestCase):
    def test_returns_64_char_hex_string(self):
        h = compute_code_hash(VALID_CODE)
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_same_code_same_hash(self):
        self.assertEqual(compute_code_hash(VALID_CODE), compute_code_hash(VALID_CODE))

    def test_different_code_different_hash(self):
        self.assertNotEqual(compute_code_hash(VALID_CODE), compute_code_hash(VALID_CODE + " "))


class TestLoadStrategy(unittest.TestCase):
    def test_returns_callable(self):
        fn = load_strategy(VALID_CODE)
        self.assertTrue(callable(fn))

    def test_raises_if_no_generate_signal(self):
        code = "def other_function(): pass"
        with self.assertRaises(ValueError) as ctx:
            load_strategy(code)
        self.assertIn("generate_signal", str(ctx.exception))


class TestExecuteStrategy(unittest.TestCase):
    def test_returns_correct_decision(self):
        fn = load_strategy(VALID_CODE)
        result = execute_strategy(fn, SNAPSHOT)
        self.assertEqual(result["decision"], "buy")
        self.assertAlmostEqual(result["confidence"], 0.78)
        self.assertIsInstance(result["reasoning"], str)

    def test_enforces_timeout(self):
        slow_code = """
def generate_signal(snapshot):
    while True:
        pass
"""
        fn = load_strategy(slow_code)
        result = execute_strategy(fn, SNAPSHOT)
        # Timeout returns hold
        self.assertEqual(result["decision"], "hold")
        self.assertIn("timed out", result["reasoning"].lower())

    def test_handles_exception_in_strategy(self):
        bad_code = """
def generate_signal(snapshot):
    raise RuntimeError("oops")
"""
        fn = load_strategy(bad_code)
        result = execute_strategy(fn, SNAPSHOT)
        self.assertEqual(result["decision"], "hold")
        self.assertIn("exception", result["reasoning"].lower())

    def test_raises_on_invalid_schema(self):
        code = """
def generate_signal(snapshot):
    return {"wrong_key": "value"}
"""
        fn = load_strategy(code)
        with self.assertRaises(ValueError) as ctx:
            execute_strategy(fn, SNAPSHOT)
        self.assertIn("missing keys", str(ctx.exception))

    def test_raises_on_invalid_decision(self):
        code = """
def generate_signal(snapshot):
    return {"decision": "INVALID", "confidence": 0.5, "reasoning": "test"}
"""
        fn = load_strategy(code)
        with self.assertRaises(ValueError) as ctx:
            execute_strategy(fn, SNAPSHOT)
        self.assertIn("Invalid decision", str(ctx.exception))

    def test_raises_on_confidence_out_of_range(self):
        code = """
def generate_signal(snapshot):
    return {"decision": "buy", "confidence": 1.5, "reasoning": "test"}
"""
        fn = load_strategy(code)
        with self.assertRaises(ValueError) as ctx:
            execute_strategy(fn, SNAPSHOT)
        self.assertIn("Confidence", str(ctx.exception))

    def test_short_decision_accepted(self):
        code = """
def generate_signal(snapshot):
    return {"decision": "short", "confidence": 0.75, "reasoning": "bearish signal"}
"""
        fn = load_strategy(code)
        result = execute_strategy(fn, SNAPSHOT)
        self.assertEqual(result["decision"], "short")

    def test_cover_decision_accepted(self):
        code = """
def generate_signal(snapshot):
    return {"decision": "cover", "confidence": 0.75, "reasoning": "covering short"}
"""
        fn = load_strategy(code)
        result = execute_strategy(fn, SNAPSHOT)
        self.assertEqual(result["decision"], "cover")
