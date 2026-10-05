"""Backend state-machine foundation. Every FieldOps workflow (service request, work order, PM, part
request, asset status) declares its transitions here and changes state ONLY through ``transition()`` so
that invalid moves are rejected in the domain layer, never in JavaScript.

A transition is: (from_state, action) -> to_state, optionally guarded by a callable. The caller (service
layer) is responsible for permission checks, wrapping in ``transaction.atomic`` and writing the audit
record; ``StateMachine.apply`` performs the validated state change and returns (previous, new).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .exceptions import InvalidTransition


@dataclass(frozen=True)
class Transition:
    action: str
    sources: tuple[str, ...]
    target: str
    guard: Callable | None = None
    label: str = ""


@dataclass
class StateMachine:
    name: str
    states: tuple[str, ...]
    transitions: list[Transition] = field(default_factory=list)

    def __post_init__(self):
        for t in self.transitions:
            unknown = {*t.sources, t.target} - set(self.states)
            if unknown:
                raise ValueError(f"{self.name}: transition {t.action} references unknown states {unknown}")

    def available(self, current: str) -> list[Transition]:
        return [t for t in self.transitions if current in t.sources]

    def get(self, current: str, action: str) -> Transition:
        for t in self.transitions:
            if t.action == action and current in t.sources:
                return t
        raise InvalidTransition(
            f"{self.name}: action '{action}' is not allowed from state '{current}'.",
            code="invalid_transition",
            details={"state": current, "action": action},
        )

    def apply(self, obj, action: str, *, state_attr: str = "status", **context) -> tuple[str, str]:
        previous = getattr(obj, state_attr)
        transition = self.get(previous, action)
        if transition.guard is not None:
            transition.guard(obj, **context)  # guard raises DomainError subclasses on failure
        setattr(obj, state_attr, transition.target)
        return previous, transition.target

    def choices(self) -> Iterable[tuple[str, str]]:
        return [(s, s.replace("_", " ").title()) for s in self.states]
