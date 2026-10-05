from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TenantOwnedModel


class Status(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


class Site(TenantOwnedModel):
    """M01: a physical site of the organization (plant, campus, depot, customer premises)."""

    Status = Status

    code = models.CharField(max_length=30)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state_region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True, help_text="ISO 3166-1 alpha-2")
    timezone = models.CharField(max_length=64, default="UTC")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    status_reason = models.CharField(max_length=300, blank=True)
    contact_name = models.CharField(max_length=150, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(Lower("code"), "organization", name="uniq_site_code_per_org")]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def is_active(self):
        return self.status == Status.ACTIVE


class Zone(TenantOwnedModel):
    """Building / zone / service area inside a site. ``parent`` builds a cycle-free tree within one site."""

    class ZoneType(models.TextChoices):
        BUILDING = "BUILDING", "Building"
        ZONE = "ZONE", "Zone / area"
        SERVICE_AREA = "SERVICE_AREA", "Service area"

    Status = Status

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="zones")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    zone_type = models.CharField(max_length=14, choices=ZoneType.choices, default=ZoneType.ZONE)
    code = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    status_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), "site", condition=models.Q(parent__isnull=True), name="uniq_zone_name_root"),
            models.UniqueConstraint(
                Lower("name"), "site", "parent", condition=models.Q(parent__isnull=False),
                name="uniq_zone_name_under_parent"),
            models.UniqueConstraint(
                Lower("code"), "site", condition=~models.Q(code=""), name="uniq_zone_code_per_site"),
        ]
        indexes = [models.Index(fields=["organization", "site"])]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == Status.ACTIVE


class OperatingCalendar(TenantOwnedModel):
    """Working days/hours of a site (times are site-local). Holidays live in ``CalendarHoliday``."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="calendars")
    name = models.CharField(max_length=100)
    working_days = models.JSONField(default=list, help_text="ISO weekday numbers, 1=Monday ... 7=Sunday")
    is_24x7 = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["site__code", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "site", name="uniq_calendar_name_per_site"),
            models.UniqueConstraint(
                fields=["site"], condition=models.Q(is_default=True), name="uniq_default_calendar_per_site"),
        ]

    def __str__(self):
        return f"{self.name} ({self.site.code})"


class CalendarHoliday(TenantOwnedModel):
    calendar = models.ForeignKey(OperatingCalendar, on_delete=models.CASCADE, related_name="holidays")
    date = models.DateField()
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["calendar", "date"], name="uniq_holiday_per_calendar_date")]

    def __str__(self):
        return f"{self.name} ({self.date})"


# Escalation levels are stored in a PostgreSQL smallint; anything above this is rejected with a validation error.
MAX_ESCALATION_ORDER = 32767


class SiteContact(TenantOwnedModel):
    """Contact hierarchy of a site: ``escalation_order`` 1 is contacted first."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="contacts")
    name = models.CharField(max_length=150)
    role_title = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    escalation_order = models.PositiveSmallIntegerField(default=1)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["site__code", "escalation_order"]
        constraints = [
            models.UniqueConstraint(fields=["site", "escalation_order"], name="uniq_contact_order_per_site"),
            models.CheckConstraint(condition=models.Q(escalation_order__gte=1), name="contact_order_gte_1"),
        ]

    def __str__(self):
        return f"{self.name} (L{self.escalation_order})"
