"""
Tests for dependencies.
"""

import importlib
import unittest


class TestDependencies(unittest.TestCase):
    """Verify presence of key project dependencies."""

    def test_core_dependencies(self):
        core_modules = [
            "speech_recognition",
        ]
        for mod in core_modules:
            with self.subTest(module=mod):
                try:
                    importlib.import_module(mod)
                except ImportError as e:
                    self.fail(f"Required module '{mod}' could not be imported: {e}")


if __name__ == "__main__":
    unittest.main()