"""Core domain: decision business logic. Pure logic, no network I/O.

Modules: decision (engine + provider chain) / experience / collect / memory /
weights / workflow / context / narrator. Domain modules never import each other
except the whitelisted provider-implementations -> decision.base.
"""
