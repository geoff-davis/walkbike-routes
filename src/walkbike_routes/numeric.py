"""Finite numeric input contracts shared by profiles and study configuration."""

from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from numbers import Real
from typing import Any


def require_finite(
    value: float,
    name: str,
    *,
    minimum: float = 0,
    positive: bool = False,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require a finite value in the specified nonnegative domain."""
    if not math.isfinite(value) or (value <= minimum if positive else value < minimum):
        domain = (
            ("positive" if minimum == 0 else f"greater than {minimum:g}")
            if positive
            else f"at least {minimum:g}"
        )
        raise error_type(f"{name} must be finite and {domain}")


def require_finite_settings(value: Any, name: str = "config") -> None:
    """Reject nonfinite numeric settings recursively, retaining optional values."""
    if isinstance(value, Real):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    elif is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            require_finite_settings(getattr(value, field.name), f"{name}.{field.name}")
    elif isinstance(value, dict):
        for key, item in value.items():
            require_finite_settings(item, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            require_finite_settings(item, f"{name}[{index}]")
