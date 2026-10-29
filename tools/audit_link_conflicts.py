"""Run cross-module regressions with mocked external services."""
from pathlib import Path
import sys
import unittest
root = Path(__file__).resolve().parents[1]
suite = unittest.defaultTestLoader.discover(str(root/'fish/tests'), pattern='test_link_conflicts.py')
sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
