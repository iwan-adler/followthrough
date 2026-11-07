"""Vercel entrypoint: exposes the FastAPI app as a serverless function.

vercel.json routes every request here; the app serves both /api/* and the static UI.
"""
import sys
from pathlib import Path

# Make the project root importable so `app` resolves as a package (its modules use relative imports).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402,F401
