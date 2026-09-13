# monitoring/workers/__init__.py
"""
Worker factory - loads worker functions on demand.
"""

from monitoring.config import get_worker

__all__ = ['get_worker']