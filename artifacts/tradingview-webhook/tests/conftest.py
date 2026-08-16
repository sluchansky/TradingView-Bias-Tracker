"""Pytest configuration for tradingview-webhook tests.

Inserts the tradingview-webhook source directory at the front of sys.path so
that test modules can `import gate_effectiveness` (and sibling modules) without
needing to be invoked from that directory.
"""
import os
import sys

# Ensure the parent directory of the tests/ folder (i.e. artifacts/tradingview-webhook/)
# is on sys.path so that `import gate_effectiveness` resolves correctly regardless
# of which directory pytest is launched from.
_src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
