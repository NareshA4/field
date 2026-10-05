"""M01 pages render for every role and tab (guards against permission codes that belong to modules not installed)."""
import pytest

pytestmark = pytest.mark.django_db

TABS = ["overview", "locations", "calendars", "contacts", "assets"]


@pytest.mark.parametrize("tab", TABS)
def test_owner_site_detail_tabs_render(client, owner_a, site_a1, tab):
    client.force_login(owner_a)
    assert client.get(f"/app/sites/{site_a1.pk}/?tab={tab}").status_code == 200


@pytest.mark.parametrize("tab", TABS)
def test_technician_site_detail_tabs_render(client, tech_a, site_a1, tab):
    client.force_login(tech_a)
    assert client.get(f"/app/sites/{site_a1.pk}/?tab={tab}").status_code == 200


@pytest.mark.parametrize("url", ["/app/sites/", "/app/sites/new/"])
def test_owner_site_pages_render(client, owner_a, url):
    client.force_login(owner_a)
    assert client.get(url).status_code == 200, url


def test_site_edit_and_zone_create_pages_render(client, owner_a, site_a1):
    client.force_login(owner_a)
    assert client.get(f"/app/sites/{site_a1.pk}/edit/").status_code == 200
    assert client.get(f"/app/sites/{site_a1.pk}/locations/new/").status_code == 200
    assert client.get(f"/app/sites/{site_a1.pk}/calendars/new/").status_code == 200
    assert client.get(f"/app/sites/{site_a1.pk}/contacts/new/").status_code == 200
