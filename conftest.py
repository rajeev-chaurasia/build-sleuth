"""Put the repo root on sys.path so tests can import the top-level `evals` package.

Only `src/buildsleuth` is installed into the venv; `evals` is a repo-local
harness that is deliberately not shipped in the wheel.
"""

import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
