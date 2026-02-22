#!/usr/bin/env python3
"""
ASTREX v3.0 — Web Package
"""

try:
    from .api import app, run_server
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    app = None
    run_server = None

__all__ = ['app', 'run_server', 'FASTAPI_AVAILABLE']
