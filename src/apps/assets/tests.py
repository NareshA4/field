import uuid
from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assets import hierarchy, selectors, services
from apps.assets.models import (
    Asset,
    AssetCategory,
    AssetComponent,
    AssetMeter,
    AssetStatus,
    AssetStatusHistory,
)
from apps.core.exceptions import Conflict, ValidationFailed
from apps.rbac.models import Permission, Role
from apps.sites.models import Site, Zone
from apps.tenancy.models import Membership, Organization


class AssetHierarchyAndLifecycleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="manager@fieldops.local",
            password="Password123!",
            full_name="Alice Manager"
        )
        self.org = Organization.objects.create(
            name="Apex Engineering Corp",
            code="APEX-CORP",
            slug="apex-engineering-corp"
        )
        self.membership = Membership.objects.create(
            organization=self.org,
            user=self.user,
            status=Membership.Status.ACTIVE
        )
        self.site = Site.objects.create(
            organization=self.org,
            name="Colombo Mega Plant",
            code="SITE-CMB-01",
            city="Colombo",
            timezone="Asia/Colombo"
        )
        self.zone_a = Zone.objects.create(
            organization=self.org,
            site=self.site,
            name="Zone A - Power & HVAC Wing",
            code="ZN-A-PWR"
        )
        self.cat_hvac = AssetCategory.objects.create(
            organization=self.org,
            name="HVAC & Climate Control"
        )
        self.cat_pump = AssetCategory.objects.create(
            organization=self.org,
            name="Pumps & Compressors"
        )

    def test_create_asset_hierarchy_tree(self):
        """Test creating 3-tier hierarchy: Root Chiller Plant -> Compressor Unit -> Impeller Part."""
        root_plant = services.create_asset(
            self.org,
            asset_tag="AST-CHL-001",
            name="Central Chiller Plant A",
            category=self.cat_hvac,
            site=self.site,
            zone=self.zone_a,
            actor=self.user
        )

        compressor = services.create_asset(
            self.org,
            asset_tag="AST-CMP-001",
            name="Screw Compressor Unit 1",
            category=self.cat_pump,
            site=self.site,
            zone=self.zone_a,
            actor=self.user
        )

        bearing = services.create_asset(
            self.org,
            asset_tag="AST-BRG-001",
            name="Magnetic Bearing Module",
            category=self.cat_pump,
            site=self.site,
            zone=self.zone_a,
            actor=self.user
        )

        # Link Root -> Compressor (ASSEMBLY)
        link1 = hierarchy.add_component(
            parent=root_plant,
            child=compressor,
            relationship_type="ASSEMBLY",
            quantity=1,
            actor=self.user
        )
        self.assertEqual(link1.parent, root_plant)
        self.assertEqual(link1.child, compressor)

        # Link Compressor -> Bearing (REPLACEABLE_PART)
        link2 = hierarchy.add_component(
            parent=compressor,
            child=bearing,
            relationship_type="REPLACEABLE_PART",
            quantity=2,
            part_number="SKF-6205-2RS",
            actor=self.user
        )
        self.assertEqual(link2.parent, compressor)
        self.assertEqual(link2.child, bearing)

        # Build tree from root
        tree = hierarchy.build_tree(root_plant)
        self.assertEqual(tree["asset"].asset_tag, "AST-CHL-001")
        self.assertEqual(len(tree["children"]), 1)
        self.assertEqual(tree["children"][0]["asset"].asset_tag, "AST-CMP-001")
        self.assertEqual(len(tree["children"][0]["children"]), 1)
        self.assertEqual(tree["children"][0]["children"][0]["asset"].asset_tag, "AST-BRG-001")
        self.assertEqual(tree["children"][0]["children"][0]["link"].part_number, "SKF-6205-2RS")

        # Ancestors of bearing
        ancs = hierarchy.ancestors(bearing)
        self.assertEqual([a.asset_tag for a in ancs], ["AST-CMP-001", "AST-CHL-001"])

    def test_cycle_prevention(self):
        """Test that linking an ancestor as a child raises ValidationFailed."""
        asset_a = services.create_asset(
            self.org,
            asset_tag="AST-A",
            name="Asset A",
            category=self.cat_hvac,
            site=self.site,
            actor=self.user
        )
        asset_b = services.create_asset(
            self.org,
            asset_tag="AST-B",
            name="Asset B",
            category=self.cat_hvac,
            site=self.site,
            actor=self.user
        )

        # Link A -> B
        hierarchy.add_component(parent=asset_a, child=asset_b, actor=self.user)

        # Attempting B -> A must fail with hierarchy_cycle
        with self.assertRaises(ValidationFailed):
            hierarchy.add_component(parent=asset_b, child=asset_a, actor=self.user)

        # Attempting A -> A must fail with self_parent
        with self.assertRaises(ValidationFailed):
            hierarchy.add_component(parent=asset_a, child=asset_a, actor=self.user)

    def test_move_component_subtree(self):
        """Test reparenting a component under a new parent."""
        root1 = services.create_asset(
            self.org, asset_tag="ROOT-1", name="Root 1",
            category=self.cat_hvac, site=self.site, actor=self.user
        )
        root2 = services.create_asset(
            self.org, asset_tag="ROOT-2", name="Root 2",
            category=self.cat_hvac, site=self.site, actor=self.user
        )
        child = services.create_asset(
            self.org, asset_tag="CHILD-1", name="Child 1",
            category=self.cat_pump, site=self.site, actor=self.user
        )

        link = hierarchy.add_component(parent=root1, child=child, actor=self.user)
        self.assertEqual(link.parent, root1)

        # Move child to root2
        moved_link = hierarchy.move_component(link, root2, actor=self.user)
        self.assertEqual(moved_link.parent, root2)
        self.assertEqual(hierarchy.ancestors(child)[0], root2)

    def test_remove_component(self):
        """Test detaching a component from its parent."""
        root = services.create_asset(
            self.org, asset_tag="ROOT-X", name="Root X",
            category=self.cat_hvac, site=self.site, actor=self.user
        )
        child = services.create_asset(
            self.org, asset_tag="CHILD-X", name="Child X",
            category=self.cat_pump, site=self.site, actor=self.user
        )
        link = hierarchy.add_component(parent=root, child=child, actor=self.user)
        self.assertTrue(AssetComponent.objects.filter(pk=link.pk).exists())

        hierarchy.remove_component(link, actor=self.user)
        self.assertFalse(AssetComponent.objects.filter(pk=link.pk).exists())
        self.assertEqual(hierarchy.ancestors(child), [])

    def test_meters_and_telemetry(self):
        """Test creating telemetry meters and logging readings."""
        asset = services.create_asset(
            self.org, asset_tag="GEN-001", name="Diesel Generator",
            category=self.cat_hvac, site=self.site, actor=self.user
        )
        meter = services.create_meter(asset, name="Running Hours", unit="h", actor=self.user)
        self.assertEqual(meter.name, "Running Hours")
        self.assertEqual(meter.unit, "h")

        reading = services.record_reading(meter, value=Decimal("1250.5"), notes="Routine reading", actor=self.user)
        self.assertEqual(reading.value, Decimal("1250.5"))
        self.assertEqual(reading.meter, meter)

    def test_status_workflow_transitions(self):
        """Test asset status changes and history recording."""
        asset = services.create_asset(
            self.org, asset_tag="CHL-002", name="Chiller 2",
            category=self.cat_hvac, site=self.site, actor=self.user
        )
        self.assertEqual(asset.status, AssetStatus.ACTIVE)

        # Transition to UNDER_MAINTENANCE
        services.change_status(
            asset, action="start_maintenance", reason="Overhaul", actor=self.user
        )
        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.UNDER_MAINTENANCE)

        history = AssetStatusHistory.objects.filter(asset=asset).order_by("created_at", "id")
        self.assertEqual(history.count(), 2)
        self.assertEqual(history[0].action, "register")
        self.assertEqual(history[0].to_status, AssetStatus.ACTIVE)
        self.assertEqual(history[1].from_status, AssetStatus.ACTIVE)
        self.assertEqual(history[1].to_status, AssetStatus.UNDER_MAINTENANCE)
