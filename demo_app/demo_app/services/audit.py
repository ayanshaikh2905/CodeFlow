"""A small decorator to demonstrate runtime-only wrapped call edges."""

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def audit_log(function: Callable[P, R]) -> Callable[P, R]:
    """Wrap a service call with an audit marker."""

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        # A real application would persist an audit event here.
        return function(*args, **kwargs)

    return wrapper
