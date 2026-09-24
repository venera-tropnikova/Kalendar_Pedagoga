"""Stage D shadow adapter. No production import or generation integration."""
from .adapter import adapt, from_worker_json, inventory, to_worker_json
from .compatibility import legacy_content, legacy_plan
from .models import AdaptationError, CompatibilityError, ExplicitWorkload

__all__ = ["adapt", "from_worker_json", "inventory", "to_worker_json",
           "legacy_content", "legacy_plan", "AdaptationError", "CompatibilityError", "ExplicitWorkload"]
