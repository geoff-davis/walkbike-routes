"""Domain exceptions raised by the accessibility core."""


class AccessibilityError(Exception):
    """Base exception for invalid accessibility studies or operations."""


class SchemaValidationError(AccessibilityError, ValueError):
    """A canonical input table violates its declared contract."""
