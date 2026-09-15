"""from clew.contracts import Adapter, Extractor, Trigger"""

from .adapter import Adapter
from .extractor import Extractor
from .registry import discover
from .trigger import REMOVE, TRACE, Mode, Trigger

__all__ = ["Adapter", "Extractor", "Trigger", "Mode", "TRACE", "REMOVE", "discover"]
