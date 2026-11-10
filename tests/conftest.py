"""Shared pytest setup. Tests run on the fictional example config unless CONFIG_DIR points elsewhere."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "config.example")

# Must happen before any test module imports shortlist.core.prefilter, which reads config at import (task 2.3 changes that).
os.environ.setdefault("CONFIG_DIR", EXAMPLE)
