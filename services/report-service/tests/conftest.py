"""Pytest conftest setup for report-service.
"""
import sys
from app.core import config

sys.modules["app.config"] = config
