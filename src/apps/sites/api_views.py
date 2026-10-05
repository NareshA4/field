from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.apiutils import ID_PARAM, paginate, query_param, require_permission
from apps.core.exceptions import NotFound
from apps.tenancy.api import TenantAPIMixin

from . import selectors, services
from .models import MAX_ESCALATION_ORDER, CalendarHoliday, OperatingCalendar, Site, SiteContact, Zone

# --- serializers ---------------------------------------------------------------------------------------


class SiteSerializer(serializers.ModelSerializer):
    zone_count = serializers.IntegerField(read_only=True, required=False)
    asset_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Site
        fields = ["id", "code", "name", "description", "address", "city", "state_region", "postal_code",
                  "country", "timezone", "status", "status_reason", "contact_name", "contact_email",
                  "contact_phone", "zone_count", "asset_count", "created_at", "updated_at"]
        read_only_fields = fields


class SiteWriteSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=30)
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    state_region = serializers.CharField(max_length=100, required=False, allow_blank=True)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=64, required=False)
    contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


class ZoneSerializer(serializers.ModelSerializer):
    site_code = serializers.CharField(source="site.code", read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True, default=None)

    class Meta:
        model = Zone
        fields = ["id", "site", "site_code", "parent", "parent_name", "zone_type", "code", "name", "description",
                  "status", "status_reason", "created_at", "updated_at"]
        read_only_fields = fields


class ZoneWriteSerializer(serializers.Serializer):
    site = serializers.UUIDField()
    parent = serializers.UUIDField(required=False, allow_null=True)
    zone_type = serializers.ChoiceField(choices=Zone.ZoneType.choices, required=False)
    code = serializers.CharField(max_length=30, required=False, allow_blank=True)
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)


class ZoneUpdateSerializer(ZoneWriteSerializer):
    site = None  # a location never changes site


class ZoneNodeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    code = serializers.CharField()
    zone_type = serializers.CharField()
    status = serializers.CharField()
    asset_count = serializers.IntegerField()
    children = serializers.ListField(child=serializers.DictField())


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = CalendarHoliday
        fields = ["id", "calendar", "date", "name"]
        read_only_fields = fields


class HolidayWriteSerializer(serializers.Serializer):
    calendar = serializers.UUIDField()
    date = serializers.DateField()
    name = serializers.CharField(max_length=120)


class CalendarSerializer(serializers.ModelSerializer):
    site_code = serializers.CharField(source="site.code", read_only=True)
    holidays = HolidaySerializer(many=True, read_only=True)

    class Meta:
        model = OperatingCalendar
        fields = ["id", "site", "site_code", "name", "working_days", "is_24x7", "start_time", "end_time",
                  "is_default", "notes", "holidays", "created_at", "updated_at"]
        read_only_fields = fields


class CalendarWriteSerializer(serializers.Serializer):
    site = serializers.UUIDField()
    name = serializers.CharField(max_length=100)
    working_days = serializers.ListField(child=serializers.IntegerField(min_value=1, max_value=7), required=False)
    is_24x7 = serializers.BooleanField(required=False)
    start_time = serializers.TimeField(required=False, allow_null=True)
    end_time = serializers.TimeField(required=False, allow_null=True)
    is_default = serializers.BooleanField(required=False)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class CalendarUpdateSerializer(CalendarWriteSerializer):
    site = None


class ContactSerializer(serializers.ModelSerializer):
    site_code = serializers.CharField(source="site.code", read_only=True)

    class Meta:
        model = SiteContact
        fields = ["id", "site", "site_code", "name", "role_title", "phone", "email", "escalation_order", "notes"]
        read_only_fields = fields


class ContactWriteSerializer(serializers.Serializer):
    site = serializers.UUIDField()
    name = serializers.CharField(max_length=150)
    role_title = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    escalation_order = serializers.IntegerField(min_value=1, max_value=MAX_ESCALATION_ORDER, required=False)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class ContactUpdateSerializer(ContactWriteSerializer):
    site = None


def _node(n) -> dict:
    z = n["zone"]
    return {"id": str(z.pk), "name": z.name, "code": z.code, "zone_type": z.zone_type, "status": z.status,
            "asset_count": n["asset_count"], "children": [_node(c) for c in n["children"]]}


def _validated(serializer_cls, request, partial=False):
    ser = serializer_cls(data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


# --- viewsets ------------------------------------------------------------------------------------------


@extend_schema(parameters=[ID_PARAM])
class SiteViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "site.view", "retrieve": "site.view", "create": "site.create", "partial_update": "site.update",
        "deactivate": "site.deactivate", "reactivate": "site.deactivate", "tree": "zone.view",
    }

    def _site(self, request, pk, code=None):
        site = selectors.get_site(request.membership, request.organization, pk)  # 404 outside scope/tenant
        if code:
            require_permission(request.membership, code, site)
        return site

    @extend_schema(parameters=[query_param("q", "code / name / city"), query_param("status", "ACTIVE|INACTIVE")],
                   responses=SiteSerializer(many=True))
    def list(self, request):
        qs = selectors.site_list(request.membership, request.organization, request.query_params)
        return paginate(request, qs, SiteSerializer)

    @extend_schema(responses=SiteSerializer)
    def retrieve(self, request, pk=None):
        return Response(SiteSerializer(self._site(request, pk)).data)

    @extend_schema(request=SiteWriteSerializer, responses={201: SiteSerializer})
    def create(self, request):
        d = _validated(SiteWriteSerializer, request)
        site = services.create_site(request.organization, actor=request.user, request=request, **d)
        return Response(SiteSerializer(site).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=SiteWriteSerializer, responses=SiteSerializer)
    def partial_update(self, request, pk=None):
        site = self._site(request, pk, "site.update")
        d = _validated(SiteWriteSerializer, request, partial=True)
        return Response(SiteSerializer(services.update_site(site, actor=request.user, request=request, **d)).data)

    @extend_schema(request=ReasonSerializer, responses=SiteSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        site = self._site(request, pk, "site.deactivate")
        d = _validated(ReasonSerializer, request)
        return Response(SiteSerializer(
            services.deactivate_site(site, reason=d["reason"], actor=request.user, request=request)).data)

    @extend_schema(request=None, responses=SiteSerializer)
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        site = self._site(request, pk, "site.deactivate")
        return Response(SiteSerializer(services.reactivate_site(site, actor=request.user, request=request)).data)

    @extend_schema(responses=ZoneNodeSerializer(many=True))
    @action(detail=True, methods=["get"])
    def tree(self, request, pk=None):
        """Nested building / zone / service-area tree of the site."""
        site = self._site(request, pk, "zone.view")
        return Response([_node(n) for n in selectors.zone_tree(site)])


@extend_schema(parameters=[ID_PARAM])
class ZoneViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {
        "list": "zone.view", "retrieve": "zone.view", "create": "zone.create", "partial_update": "zone.update",
        "deactivate": "zone.deactivate", "reactivate": "zone.deactivate",
    }

    def _zone(self, request, pk, code=None):
        zone = selectors.get_zone(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, zone.site)
        return zone

    def _parent(self, request, site, parent_id):
        if parent_id is None:
            return None
        parent = selectors.get_zone(request.membership, request.organization, parent_id)
        if parent.site_id != site.pk:
            raise NotFound("Location not found.")
        return parent

    @extend_schema(parameters=[query_param("site", "site id", OpenApiTypes.UUID),
                               query_param("parent", "parent location id, or 'root'"), query_param("q"),
                               query_param("zone_type"), query_param("status")],
                   responses=ZoneSerializer(many=True))
    def list(self, request):
        qs = selectors.filter_zones(selectors.zones_for(request.membership, request.organization),
                                    request.query_params)
        return paginate(request, qs.order_by("site__code", "name"), ZoneSerializer)

    @extend_schema(responses=ZoneSerializer)
    def retrieve(self, request, pk=None):
        return Response(ZoneSerializer(self._zone(request, pk)).data)

    @extend_schema(request=ZoneWriteSerializer, responses={201: ZoneSerializer})
    def create(self, request):
        d = _validated(ZoneWriteSerializer, request)
        site = selectors.get_site_for(request.membership, request.organization, d.pop("site"), "zone.create")
        parent = self._parent(request, site, d.pop("parent", None))
        zone = services.create_zone(site, parent=parent, actor=request.user, request=request, **d)
        return Response(ZoneSerializer(zone).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ZoneUpdateSerializer, responses=ZoneSerializer)
    def partial_update(self, request, pk=None):
        zone = self._zone(request, pk, "zone.update")
        d = _validated(ZoneUpdateSerializer, request, partial=True)
        kwargs = {}
        if "parent" in d:
            kwargs["parent"] = self._parent(request, zone.site, d.pop("parent"))
        zone = services.update_zone(zone, actor=request.user, request=request, **kwargs, **d)
        return Response(ZoneSerializer(zone).data)

    @extend_schema(request=ReasonSerializer, responses=ZoneSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        zone = self._zone(request, pk, "zone.deactivate")
        d = _validated(ReasonSerializer, request)
        return Response(ZoneSerializer(
            services.deactivate_zone(zone, reason=d["reason"], actor=request.user, request=request)).data)

    @extend_schema(request=None, responses=ZoneSerializer)
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        zone = self._zone(request, pk, "zone.deactivate")
        return Response(ZoneSerializer(services.reactivate_zone(zone, actor=request.user, request=request)).data)


@extend_schema(parameters=[ID_PARAM])
class CalendarViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "calendar.view", "retrieve": "calendar.view", "create": "calendar.create",
                      "partial_update": "calendar.update", "destroy": "calendar.delete"}

    def _cal(self, request, pk, code=None):
        cal = selectors.get_calendar(request.membership, request.organization, pk)
        if code:
            require_permission(request.membership, code, cal.site)
        return cal

    @extend_schema(parameters=[query_param("site", "site id", OpenApiTypes.UUID)],
                   responses=CalendarSerializer(many=True))
    def list(self, request):
        qs = selectors.calendars_for(request.membership, request.organization)
        if request.query_params.get("site"):
            import uuid

            try:
                qs = qs.filter(site_id=uuid.UUID(request.query_params["site"]))
            except ValueError:
                qs = qs.none()
        return paginate(request, qs, CalendarSerializer)

    @extend_schema(responses=CalendarSerializer)
    def retrieve(self, request, pk=None):
        return Response(CalendarSerializer(self._cal(request, pk)).data)

    @extend_schema(request=CalendarWriteSerializer, responses={201: CalendarSerializer})
    def create(self, request):
        d = _validated(CalendarWriteSerializer, request)
        site = selectors.get_site_for(request.membership, request.organization, d.pop("site"), "calendar.create")
        cal = services.create_calendar(site, actor=request.user, request=request, **d)
        return Response(CalendarSerializer(cal).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=CalendarUpdateSerializer, responses=CalendarSerializer)
    def partial_update(self, request, pk=None):
        cal = self._cal(request, pk, "calendar.update")
        d = _validated(CalendarUpdateSerializer, request, partial=True)
        cal = services.update_calendar(cal, actor=request.user, request=request, **d)
        return Response(CalendarSerializer(selectors.get_calendar(request.membership, request.organization,
                                                                  cal.pk)).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        cal = self._cal(request, pk, "calendar.delete")
        services.delete_calendar(cal, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(parameters=[ID_PARAM])
class HolidayViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "calendar.view", "create": "calendar.update", "destroy": "calendar.update"}

    @extend_schema(parameters=[query_param("calendar", "calendar id", OpenApiTypes.UUID)],
                   responses=HolidaySerializer(many=True))
    def list(self, request):
        import uuid

        qs = selectors.holidays_for(request.membership, request.organization)
        if request.query_params.get("calendar"):
            try:
                qs = qs.filter(calendar_id=uuid.UUID(request.query_params["calendar"]))
            except ValueError:
                qs = qs.none()
        return paginate(request, qs, HolidaySerializer)

    @extend_schema(request=HolidayWriteSerializer, responses={201: HolidaySerializer})
    def create(self, request):
        d = _validated(HolidayWriteSerializer, request)
        cal = selectors.get_calendar(request.membership, request.organization, d["calendar"])
        require_permission(request.membership, "calendar.update", cal.site)
        h = services.add_holiday(cal, date=d["date"], name=d["name"], actor=request.user, request=request)
        return Response(HolidaySerializer(h).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        h = selectors.scoped_get(selectors.holidays_for(request.membership, request.organization), pk, "Holiday")
        require_permission(request.membership, "calendar.update", h.calendar.site)
        services.remove_holiday(h, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(parameters=[ID_PARAM])
class ContactViewSet(TenantAPIMixin, viewsets.ViewSet):
    permission_map = {"list": "site.view", "retrieve": "site.view", "create": "site.contact.manage",
                      "partial_update": "site.contact.manage", "destroy": "site.contact.manage"}

    def _contact(self, request, pk, code=None):
        c = selectors.scoped_get(selectors.contacts_for(request.membership, request.organization), pk, "Contact")
        if code:
            require_permission(request.membership, code, c.site)
        return c

    @extend_schema(parameters=[query_param("site", "site id", OpenApiTypes.UUID)],
                   responses=ContactSerializer(many=True))
    def list(self, request):
        import uuid

        qs = selectors.contacts_for(request.membership, request.organization)
        if request.query_params.get("site"):
            try:
                qs = qs.filter(site_id=uuid.UUID(request.query_params["site"]))
            except ValueError:
                qs = qs.none()
        return paginate(request, qs, ContactSerializer)

    @extend_schema(responses=ContactSerializer)
    def retrieve(self, request, pk=None):
        return Response(ContactSerializer(self._contact(request, pk)).data)

    @extend_schema(request=ContactWriteSerializer, responses={201: ContactSerializer})
    def create(self, request):
        d = _validated(ContactWriteSerializer, request)
        site = selectors.get_site_for(request.membership, request.organization, d.pop("site"),
                                      "site.contact.manage")
        c = services.add_contact(site, actor=request.user, request=request, **d)
        return Response(ContactSerializer(c).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ContactUpdateSerializer, responses=ContactSerializer)
    def partial_update(self, request, pk=None):
        c = self._contact(request, pk, "site.contact.manage")
        d = _validated(ContactUpdateSerializer, request, partial=True)
        return Response(ContactSerializer(services.update_contact(c, actor=request.user, request=request, **d)).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        services.remove_contact(self._contact(request, pk, "site.contact.manage"), actor=request.user,
                                request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
