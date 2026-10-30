"""Run the repaired lifecycle regressions without touching a device or QQ session.

Historical defect results are retained in docs/qq-login-audit-reproductions-2026-10-30.json.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'fish'))
suite = unittest.defaultTestLoader.discover(str(ROOT/'fish/tests'), pattern='test_qq_control.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
