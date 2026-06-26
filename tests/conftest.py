"""
tests/conftest.py

Adds the growout/ directory to sys.path so that `core.*` modules are
importable without installing the package.  No Supabase, no Streamlit.
"""
import sys
import os

# growout/ is the parent of this tests/ folder
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
