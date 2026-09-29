"""Weights domain: four-layer resolution + modulation growth channels (v4 §5.5)."""
from decide_agent.core.weights.modulation import (
    WeightModulationStore,
    attribute_feedback,
)
from decide_agent.core.weights.resolver import resolve

__all__ = ["WeightModulationStore", "attribute_feedback", "resolve"]
