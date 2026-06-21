"""Model routing and capability registry for Hachi.

Exports the primary public surface: Capability, ModelDescriptor, ModelRegistry,
RoutingPolicy, ModelRouter, RouteConstraints, ResolvedModel, NoRouteError.
"""

from hachi.core.routing.capability import Capability
from hachi.core.routing.descriptor import BUNDLED_MODEL_DEFAULTS, ModelDescriptor, Pricing
from hachi.core.routing.policy import RoutingPolicy
from hachi.core.routing.registry import ModelRegistry
from hachi.core.routing.router import ModelRouter, NoRouteError, ResolvedModel, RouteConstraints

__all__ = [
    "Capability",
    "ModelDescriptor",
    "Pricing",
    "BUNDLED_MODEL_DEFAULTS",
    "ModelRegistry",
    "RoutingPolicy",
    "ModelRouter",
    "RouteConstraints",
    "ResolvedModel",
    "NoRouteError",
]
