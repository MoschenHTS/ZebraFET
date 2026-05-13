"""
conftest.py — Shared pytest configuration and fixtures for ZebraFET tests.

Adds the project root to sys.path so that project_manager, constants,
biostatistics, and the database package can be imported without Qt.
"""
import os
import sys

# Insert the project root (one level above this file) at the front of sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
