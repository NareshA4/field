"""Per-target read authorization for attachments. A module that attaches files to its own model registers a
checker ``(membership, target) -> bool`` so downloads honour that module's rules (for example site scope).
Targets without a checker fall back to the attachment's ``read_permission`` (organization-wide)."""
from collections.abc import Callable

_CHECKERS: dict[str, Callable] = {}


def register(model_label: str, checker: Callable) -> None:
    _CHECKERS[model_label.lower()] = checker


def checker_for(model_label: str) -> Callable | None:
    return _CHECKERS.get(model_label.lower())


def allowed(checker: Callable, membership, target, attachment) -> bool:
    """Runs a checker; checkers that declare an ``attachment`` parameter also receive the attachment (needed for
    rules such as "a portal client may read only the files they uploaded themselves")."""
    import inspect

    if "attachment" in inspect.signature(checker).parameters:
        return bool(checker(membership, target, attachment=attachment))
    return bool(checker(membership, target))
