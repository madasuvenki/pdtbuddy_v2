"""Application composition helpers for PDTBuddy.

This package owns Flask application wiring that is shared by the entry point and
future application-factory based deployments.  Feature handlers remain in their
existing modules while they are migrated incrementally.
"""

from .blueprints import register_feature_blueprints

__all__ = ["register_feature_blueprints"]