"""Root conftest for reception-service tests.

Adds the service root directory to sys.path so that `from app.xxx import ...`
works for all test modules regardless of where pytest is invoked from.
"""
import sys
from pathlib import Path

# Insert the service root (services/reception-service/) at the front of sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))
