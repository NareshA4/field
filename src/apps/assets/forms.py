import re
from django import forms

from apps.sites.models import Site, Zone
from apps.tenancy.models import Membership
from apps.ui.forms import BootstrapFormMixin

from . import attributes as attrs
from .models import Asset, AssetCategory, AssetComponent, AssetDocument

_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,39}")


class _ZoneChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.site.code} · {obj.name}"


class _OwnerChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.user.display_name


class AssetForm(BootstrapFormMixin, forms.Form):
    asset_tag = forms.CharField(max_length=40, label="Asset tag", help_text="Human-readable ID, unique in the organization.")
    name = forms.CharField(max_length=200)
    category = forms.ModelChoiceField(queryset=AssetCategory.objects.none())
    site = forms.ModelChoiceField(queryset=Site.objects.none())
    zone = _ZoneChoice(queryset=Zone.objects.none(), required=False, label="Location (building / zone)",
                       empty_label="(none)")
    manufacturer = forms.CharField(max_length=100, required=False)
    model = forms.CharField(max_length=100, required=False)
    serial_number = forms.CharField(max_length=100, required=False)
    purchase_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    commission_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                      label="Commissioning date")
    owner = _OwnerChoice(queryset=Membership.objects.none(), required=False, label="Owner / custodian",
                         empty_label="(none)")
    warranty_ref = forms.CharField(max_length=200, required=False, label="Warranty reference",
                                   help_text="Free-text reference; coverage tracking arrives with the warranty module.")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    reason = forms.CharField(max_length=500, required=False, label="Reason for location change",
                             help_text="Recorded in the location history when the site or location changes.")

    def __init__(self, *args, sites, org, creating=True, category=None, asset_pk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        self.asset_pk = asset_pk
        self.attribute_defs = list(category.attribute_definitions) if category else []
        values = (kwargs.get("initial") or {}).get("attributes") or {}
        for d in self.attribute_defs:  # the category's custom attributes become real, validated form fields
            self.fields[f"attr_{d['key']}"] = self._attribute_field(d, values.get(d["key"], ""))
        self.fields["category"].widget.attrs["data-category-reload"] = "1"
        site_qs = sites.filter(status="ACTIVE").order_by("code")
        self.fields["site"].queryset = site_qs
        self.fields["zone"].queryset = Zone.objects.for_organization(org).filter(
            site__in=site_qs, status="ACTIVE").select_related("site").order_by("site__code", "name")
        self.fields["category"].queryset = AssetCategory.objects.for_organization(org).filter(is_active=True)
        self.fields["owner"].queryset = Membership.objects.for_organization(org).filter(
            status=Membership.Status.ACTIVE).select_related("user").order_by("user__full_name")
        if creating:
            del self.fields["reason"]
        self.fields["zone"].widget.attrs["data-site-filter"] = "1"
        self.fields["site"].widget.attrs["data-site-select"] = "1"

    @staticmethod
    def _attribute_field(d, initial):
        required = bool(d.get("required"))
        label = f"{d['label']} *" if required else d["label"]
        common = {"required": required, "label": label, "initial": initial}
        if d["type"] == "number":
            return forms.DecimalField(decimal_places=6, max_digits=20, widget=forms.NumberInput(attrs={"class": "form-control"}), **common)
        if d["type"] == "date":
            return forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}), **common)
        if d["type"] == "choice":
            return forms.ChoiceField(choices=[("", "(choose)")] + [(c, c) for c in d["choices"]], widget=forms.Select(attrs={"class": "form-select"}), **common)
        return forms.CharField(max_length=attrs.MAX_TEXT, widget=forms.TextInput(attrs={"class": "form-control"}), **common)

    def clean_asset_tag(self):
        tag = (self.cleaned_data.get("asset_tag") or "").strip()
        if not tag:
            raise forms.ValidationError("Asset tag is required.")
        if not _TAG_RE.fullmatch(tag):
            raise forms.ValidationError("Asset tag must be 1-40 characters: letters, digits, '.', '_', '/' or '-'.")
        if self.org:
            qs = Asset.objects.for_organization(self.org).filter(asset_tag__iexact=tag)
            if self.asset_pk:
                qs = qs.exclude(pk=self.asset_pk)
            if qs.exists():
                raise forms.ValidationError("An asset with this tag already exists.")
        return tag

    def clean(self):
        data = super().clean()
        values = {}
        for d in self.attribute_defs:
            v = data.pop(f"attr_{d['key']}", None)
            values[d["key"]] = "" if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))
        data["attributes"] = values
        site, zone = data.get("site"), data.get("zone")
        if site and zone and zone.site_id != site.pk:
            self.add_error("zone", "This location does not belong to the selected site.")
        purchase, commission = data.get("purchase_date"), data.get("commission_date")
        if purchase and commission and commission < purchase:
            self.add_error("commission_date", "Commissioning date cannot be before the purchase date.")
        mfg = (data.get("manufacturer") or "").strip()
        serial = (data.get("serial_number") or "").strip()
        if serial and self.org:
            dup = Asset.objects.for_organization(self.org).filter(manufacturer__iexact=mfg, serial_number__iexact=serial)
            if self.asset_pk:
                dup = dup.exclude(pk=self.asset_pk)
            if dup.exists():
                self.add_error("serial_number", "An asset with this manufacturer and serial number already exists.")
        return data


class RelationshipFields(forms.Form):
    relationship_type = forms.ChoiceField(choices=AssetComponent.Relationship.choices, initial="COMPONENT")
    quantity = forms.IntegerField(min_value=1, initial=1)
    part_number = forms.CharField(max_length=100, required=False)
    notes = forms.CharField(max_length=300, required=False)


class NewChildForm(BootstrapFormMixin, RelationshipFields):
    """Extra fields shown on the create-asset form when registering a child of an existing asset."""


class ComponentAddForm(BootstrapFormMixin, RelationshipFields):
    child = forms.ModelChoiceField(queryset=None, label="Existing asset")

    def __init__(self, *args, candidates, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = candidates
        self.order_fields(["child", "relationship_type", "quantity", "part_number", "notes"])


class ComponentEditForm(BootstrapFormMixin, RelationshipFields):
    """Edits the relationship (type, quantity, part number, notes) of an existing parent-child link."""


class ComponentMoveForm(BootstrapFormMixin, forms.Form):
    parent = forms.ModelChoiceField(queryset=None, label="New parent")

    def __init__(self, *args, candidates, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = candidates


class ComponentReplaceForm(BootstrapFormMixin, forms.Form):
    new_child = forms.ModelChoiceField(queryset=None, label="Replacement Asset")
    reason = forms.CharField(max_length=300, required=False, label="Replacement Reason",
                             help_text="e.g. Scheduled part swap, worn bearing replaced during PM.")

    def __init__(self, *args, candidates, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_child"].queryset = candidates


class DocumentForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField()
    title = forms.CharField(max_length=150, required=False)
    doc_type = forms.ChoiceField(choices=AssetDocument.DocType.choices, initial="OTHER", label="Type")


class MeterForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=80, help_text="e.g. Run hours")
    unit = forms.CharField(max_length=20, help_text="e.g. h, km, cycles")


class ReadingForm(BootstrapFormMixin, forms.Form):
    value = forms.DecimalField(max_digits=16, decimal_places=3, min_value=0)
    read_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                  input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"],
                                  label="Reading time (blank = now)")
    notes = forms.CharField(max_length=300, required=False)


class CategoryForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=100)
    description = forms.CharField(max_length=300, required=False)
    is_active = forms.BooleanField(required=False, initial=True, label="Active")
    attribute_text = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Custom attributes",
        help_text="One per line: Label | type | required | choices. Types: text, number, date, choice. "
                  "Example: Voltage | number | required")

    def clean_attribute_text(self):
        from apps.core.exceptions import DomainError

        try:
            return attrs.parse_text(self.cleaned_data.get("attribute_text", ""))
        except DomainError as exc:
            raise forms.ValidationError(exc.message) from exc
