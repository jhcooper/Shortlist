"""Command-line wrapper. The code lives in shortlist/core/validate_resume.py; `python3 validate_resume.py ...` and `import validate_resume` work as before."""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root, so `shortlist` imports

if __name__ == "__main__":
    runpy.run_module("shortlist.core.validate_resume", run_name="__main__", alter_sys=True)
else:
    import importlib

    sys.modules[__name__] = importlib.import_module("shortlist.core.validate_resume")  # one module object: `validate_resume` is `shortlist.core.validate_resume`
