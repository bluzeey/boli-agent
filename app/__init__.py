"""Boli procurement agent package.

Configures root logging before any app submodule is imported so that
module-level boot diagnostics appear in the container logs.
"""
import logging
import os

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
