"""Scheduled-item jobs, run by the django-q2 cluster (qcluster)."""
from scheduled.services import ProductionService


def produce_all() -> int:
    """Hourly: what's due lands on each user's list soon after their midnight (opening the board does it too)."""
    return ProductionService.produce_all()
