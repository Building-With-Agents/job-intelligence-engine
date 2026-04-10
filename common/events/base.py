"""Re-export EventEnvelope from its canonical location.

This module exists so that ``from common.events import EventEnvelope``
works alongside the original ``from common.event_envelope import EventEnvelope``.
"""

from common.event_envelope import EventEnvelope  # noqa: F401

__all__ = ["EventEnvelope"]
