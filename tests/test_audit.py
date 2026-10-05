import pytest
from django.db import InternalError, connection, transaction
from django.db.utils import IntegrityError

from apps.audit import services as audit
from apps.audit.models import AuditLog, ImmutableError

pytestmark = pytest.mark.django_db


def _entry(org):
    return audit.record("test.event", organization=org, target_repr="x", after={"k": 1})


def test_model_update_blocked(org_a):
    e = _entry(org_a)
    e.action = "tampered"
    with pytest.raises(ImmutableError):
        e.save()


def test_model_and_queryset_delete_update_blocked(org_a):
    e = _entry(org_a)
    with pytest.raises(ImmutableError):
        e.delete()
    with pytest.raises(ImmutableError):
        AuditLog.objects.filter(pk=e.pk).delete()
    with pytest.raises(ImmutableError):
        AuditLog.objects.filter(pk=e.pk).update(action="x")


def test_database_trigger_blocks_raw_sql(org_a):
    e = _entry(org_a)
    with pytest.raises((InternalError, IntegrityError)), transaction.atomic(), connection.cursor() as cur:
        cur.execute("UPDATE audit_auditlog SET action = 'tampered' WHERE id = %s", [e.pk])
    with pytest.raises((InternalError, IntegrityError)), transaction.atomic(), connection.cursor() as cur:
        cur.execute("DELETE FROM audit_auditlog WHERE id = %s", [e.pk])
    assert AuditLog.objects.get(pk=e.pk).action == "test.event"


def test_critical_actions_are_audited(as_user, owner_a, org_a):
    c = as_user(owner_a, org_a)
    c.post("/api/v1/members/", {"email": "new@alpha.test", "full_name": "New Person",
                                 "role_ids": [str(__import__("apps.rbac.models", fromlist=["Role"]).Role.objects
                                                  .unscoped().get(organization=org_a, system_key="technician").pk)]},
           format="json")
    c.patch("/api/v1/organization/", {"name": "Alpha Industries Ltd"}, format="json")
    actions = set(AuditLog.objects.filter(organization=org_a).values_list("action", flat=True))
    assert {"organization.created", "user.invited", "organization.updated", "membership.roles_changed"} <= actions
    upd = AuditLog.objects.filter(organization=org_a, action="organization.updated").first()
    assert upd.before["name"] == "Alpha Industries" and upd.after["name"] == "Alpha Industries Ltd"
    assert upd.actor_email == owner_a.email


def test_login_logout_audited(client, owner_a, org_a):
    from tests.conftest import PASSWORD
    r = client.post("/accounts/login/", {"username": owner_a.email, "password": PASSWORD})
    assert r.status_code == 302
    client.post("/accounts/logout/")
    actions = list(AuditLog.objects.filter(organization=org_a, actor=owner_a).values_list("action", flat=True))
    assert "auth.login" in actions and "auth.logout" in actions
