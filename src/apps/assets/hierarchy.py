"""M03 asset hierarchy: parent/child assemblies, components and replaceable parts.

Integrity rules (all enforced server-side, in one transaction, under a per-organization advisory lock so two
concurrent edits cannot jointly create a cycle):
  * parent and child belong to the SAME organization and the SAME site;
  * no self-parenting, no cycles (a node can never become its own ancestor);
  * a child has exactly one parent (OneToOne), so the structure is a forest;
  * depth is limited to ``MAX_DEPTH`` levels (guards runaway traversal);
  * RETIRED / DISPOSED assets cannot gain, lose or change relationships;
  * every change is audited; moving a node re-validates cycle and depth rules.
"""
from __future__ import annotations

from django.db import connection, transaction

from apps.audit import services as audit
from apps.core.exceptions import Conflict, ValidationFailed

from .models import Asset, AssetComponent
from .workflow import TERMINAL_STATES

MAX_DEPTH = 8
COMPONENT_FIELDS = ["parent_id", "child_id", "relationship_type", "quantity", "part_number", "notes"]


def _lock_org(org):
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [org.pk.int & 0x7FFFFFFFFFFFFFFF])


def ancestors(asset: Asset) -> list[Asset]:
    """Parent, grandparent, ... up to the root (nearest first). Bounded: a corrupt cycle raises."""
    chain: list[Asset] = []
    seen = {asset.pk}
    node = asset
    while True:
        link = AssetComponent.objects.filter(child=node).select_related("parent").first()
        if link is None:
            return chain
        node = link.parent
        if node.pk in seen or len(chain) > MAX_DEPTH + 1:
            raise ValidationFailed("Asset hierarchy contains a cycle.", code="hierarchy_cycle")
        seen.add(node.pk)
        chain.append(node)


def subtree_height(asset: Asset) -> int:
    """Levels below ``asset`` (0 = no children)."""
    level, height, seen = [asset.pk], 0, {asset.pk}
    while True:
        nxt = [c for c in AssetComponent.objects.filter(parent_id__in=level).values_list("child_id", flat=True)
               if c not in seen]
        if not nxt:
            return height
        height += 1
        seen.update(nxt)
        level = nxt
        if height > MAX_DEPTH:
            return height


def check_link(parent: Asset, child: Asset, *, moving: bool = False):
    """Raises a DomainError when ``child`` may not be placed under ``parent`` (also used for dry-run checks)."""
    if parent.pk == child.pk:
        raise ValidationFailed("An asset cannot be its own parent.", code="self_parent")
    if parent.organization_id != child.organization_id:
        raise ValidationFailed("Assets belong to different organizations.", code="cross_tenant")
    if parent.site_id != child.site_id:
        raise ValidationFailed("Parent and child must be at the same site.", code="site_mismatch")
    if parent.status in TERMINAL_STATES or child.status in TERMINAL_STATES:
        raise Conflict("Retired or disposed assets cannot take part in the hierarchy.", code="asset_terminal")
    if not moving and AssetComponent.objects.filter(child=child).exists():
        raise Conflict("This asset is already a component of another asset; move it instead.",
                       code="already_has_parent")
    above = ancestors(parent)
    if any(a.pk == child.pk for a in above):
        raise ValidationFailed("This would create a cycle: the child is an ancestor of the parent.",
                               code="hierarchy_cycle")
    levels = len(above) + 1 + 1 + subtree_height(child)  # ancestors + parent + child + below child
    if levels > MAX_DEPTH:
        raise ValidationFailed(f"The hierarchy is limited to {MAX_DEPTH} levels.", code="hierarchy_too_deep")


def _check_quantity(quantity):
    if not isinstance(quantity, int) or quantity < 1 or quantity > 10000:
        raise ValidationFailed("Quantity must be a whole number from 1 to 10000.", code="invalid_quantity")


def _lock_assets(*assets):
    return {a.pk: a for a in Asset.objects.select_for_update().filter(pk__in=[a.pk for a in assets]).order_by("pk")}


@transaction.atomic
def add_component(parent: Asset, child: Asset, *, relationship_type: str = "COMPONENT", quantity: int = 1,
                  part_number: str = "", notes: str = "", actor, request=None) -> AssetComponent:
    _lock_org(parent.organization)
    fresh = _lock_assets(parent, child)
    if len(fresh) != 2 and parent.pk != child.pk:
        raise ValidationFailed("Asset not found.", code="not_found")
    parent, child = fresh.get(parent.pk, parent), fresh.get(child.pk, child)
    if relationship_type not in AssetComponent.Relationship.values:
        raise ValidationFailed("Unknown relationship type.", code="invalid_relationship")
    _check_quantity(quantity)
    check_link(parent, child)
    link = AssetComponent(organization=parent.organization, parent=parent, child=child,
                          relationship_type=relationship_type, quantity=quantity,
                          part_number=(part_number or "").strip(), notes=(notes or "").strip())
    link.save()
    audit.record("asset.component_added", actor=actor, organization=parent.organization, target=parent,
                 after=audit.snapshot(link, COMPONENT_FIELDS), request=request)
    return link


@transaction.atomic
def move_component(link: AssetComponent, new_parent: Asset, *, actor, request=None) -> AssetComponent:
    """Re-parents ``link.child``; cycle/depth/site/organization rules are re-validated."""
    _lock_org(link.organization)
    link = AssetComponent.objects.select_for_update().select_related("parent", "child").get(pk=link.pk)
    fresh = _lock_assets(link.child, new_parent)
    child, new_parent = fresh.get(link.child_id, link.child), fresh.get(new_parent.pk, new_parent)
    if new_parent.pk == link.parent_id:
        raise Conflict("The component is already under that parent.", code="no_change")
    check_link(new_parent, child, moving=True)
    before = audit.snapshot(link, COMPONENT_FIELDS)
    link.parent = new_parent
    link.save()
    audit.record("asset.component_moved", actor=actor, organization=link.organization, target=child,
                 before=before, after=audit.snapshot(link, COMPONENT_FIELDS), request=request)
    return link


@transaction.atomic
def update_component(link: AssetComponent, *, actor, request=None, relationship_type=None, quantity=None,
                     part_number=None, notes=None) -> AssetComponent:
    if link.parent.status in TERMINAL_STATES or link.child.status in TERMINAL_STATES:
        raise Conflict("Retired or disposed assets cannot take part in the hierarchy.", code="asset_terminal")
    before = audit.snapshot(link, COMPONENT_FIELDS)
    if relationship_type is not None:
        if relationship_type not in AssetComponent.Relationship.values:
            raise ValidationFailed("Unknown relationship type.", code="invalid_relationship")
        link.relationship_type = relationship_type
    if quantity is not None:
        _check_quantity(quantity)
        link.quantity = quantity
    if part_number is not None:
        link.part_number = part_number.strip()
    if notes is not None:
        link.notes = notes.strip()
    link.save()
    after = audit.snapshot(link, COMPONENT_FIELDS)
    if before != after:
        audit.record("asset.component_updated", actor=actor, organization=link.organization, target=link.parent,
                     before=before, after=after, request=request)
    return link


@transaction.atomic
def remove_component(link: AssetComponent, *, actor, request=None):
    if link.parent.status in TERMINAL_STATES or link.child.status in TERMINAL_STATES:
        raise Conflict("Retired or disposed assets cannot take part in the hierarchy.", code="asset_terminal")
    snap, parent, child, org = audit.snapshot(link, COMPONENT_FIELDS), link.parent, link.child, link.organization
    link.delete()
    audit.record("asset.component_removed", actor=actor, organization=org, target=parent, before=snap,
                 metadata={"child": child.asset_tag}, request=request)


# --- read side ---------------------------------------------------------------------------------------


def root_of(asset: Asset) -> Asset:
    above = ancestors(asset)
    return above[-1] if above else asset


def build_tree(root: Asset) -> dict:
    """Nested subtree of ``root`` (one query per level, depth-bounded):
    {"asset", "link", "children": [...], "depth"}. All nodes share the root's site (enforced on write)."""
    node = {"asset": root, "link": None, "children": [], "depth": 0}
    level = {root.pk: node}
    for depth in range(1, MAX_DEPTH + 1):
        links = list(AssetComponent.objects.filter(parent_id__in=list(level)).select_related(
            "child", "child__category").order_by("child__asset_tag"))
        if not links:
            break
        nxt = {}
        for link in links:
            child_node = {"asset": link.child, "link": link, "children": [], "depth": depth}
            level[link.parent_id]["children"].append(child_node)
            nxt[link.child_id] = child_node
        level = nxt
    return node


def flatten(node: dict) -> list[dict]:
    out = [node]
    for c in node["children"]:
        out.extend(flatten(c))
    return out


def validate_tree(root: Asset) -> dict:
    """Integrity report for the subtree below ``root`` (the API 'validate' action)."""
    issues = []
    tree = build_tree(root)
    nodes = flatten(tree)
    for n in nodes:
        link = n["link"]
        if link is None:
            continue
        a, p = link.child, link.parent
        if a.organization_id != p.organization_id:
            issues.append({"code": "cross_tenant", "asset": a.asset_tag})
        if a.site_id != p.site_id:
            issues.append({"code": "site_mismatch", "asset": a.asset_tag})
    if len({n["asset"].pk for n in nodes}) != len(nodes):
        issues.append({"code": "hierarchy_cycle", "asset": root.asset_tag})
    if subtree_height(root) + len(ancestors(root)) + 1 > MAX_DEPTH:
        issues.append({"code": "hierarchy_too_deep", "asset": root.asset_tag})
    return {"valid": not issues, "nodes": len(nodes), "issues": issues}
