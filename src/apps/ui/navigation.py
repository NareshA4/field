"""Navigation registry. Each app declares its sidebar entries in ``<app>/navigation.py`` via ``register``.
Only IMPLEMENTED modules register entries, so the shell never shows dead links."""
from dataclasses import dataclass

_ITEMS: list["NavItem"] = []


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    permission: str | None  # None = any active member
    icon: str
    section: str
    order: int = 100
    match_prefix: str = ""  # URL prefix that marks the item active


def register(item: NavItem) -> None:
    if not any(i.url_name == item.url_name for i in _ITEMS):
        _ITEMS.append(item)


def items() -> list[NavItem]:
    return sorted(_ITEMS, key=lambda i: (i.section, i.order, i.label))
