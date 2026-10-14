```````````````````import re
import zoneinfo

from django import forms

from apps.ui.forms import BootstrapFormMixin

from .geo_data import (
    CITIES_BY_STATE,
    CITY_PINCODES,
    CITY_TO_STATE,
    COUNTRIES,
    COUNTRY_CODES,
    COUNTRY_DEFAULT_TIMEZONES,
    COUNTRY_PHONE_PREFIXES,
    INDIA_STATE_PIN_PREFIXES,
    STATES_BY_COUNTRY,
    TIMEZONES,
    validate_india_pincode,
)
from .models import MAX_ESCALATION_ORDER, Zone

WEEKDAYS = [(1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"), (5, "Friday"), (6, "Saturday"),
            (7, "Sunday")]

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,29}$")
_PHONE_RE = re.compile(r"^[0-9+\s\-()]{7,32}$")


class SiteForm(BootstrapFormMixin, forms.Form):
    code = forms.CharField(
        max_length=30,
        min_length=1,
        help_text="Short unique identifier, e.g. PLANT-01 (1-30 chars: uppercase letters, numbers, '.', '_', '-').",
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. PLANT-01",
            "style": "text-transform: uppercase;",
            "required": "required",
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,29}$",
            "title": "1-30 characters: start with letter/digit, uppercase letters, digits, '.', '_' or '-'"
        })
    )
    name = forms.CharField(
        max_length=150,
        min_length=2,
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. Main Plant & Headquarters",
            "required": "required",
            "minlength": "2"
        })
    )
    description = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Brief description of the facility", "maxlength": "500"})
    )
    address = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Street address, plot number, industrial area", "maxlength": "500"})
    )
    country = forms.ChoiceField(
        choices=[("", "Select Country...")] + COUNTRIES,
        required=True,
        help_text="Primary country of the facility",
        widget=forms.Select(attrs={
            "id": "id_country",
            "class": "form-select",
            "required": "required"
        })
    )
    state_region = forms.CharField(
        max_length=100,
        required=True,
        label="State / region",
        widget=forms.Select(attrs={
            "id": "id_state_region",
            "class": "form-select",
            "required": "required"
        })
    )
    city = forms.CharField(
        max_length=100,
        required=True,
        label="City",
        widget=forms.Select(attrs={
            "id": "id_city",
            "class": "form-select",
            "required": "required"
        })
    )
    postal_code = forms.CharField(
        max_length=20,
        required=True,
        label="Postal / PIN code",
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. 560001",
            "id": "id_postal_code",
            "required": "required",
            "autocomplete": "postal-code"
        })
    )
    timezone = forms.ChoiceField(
        choices=[("", "Select Timezone...")] + TIMEZONES,
        required=True,
        help_text="Operating timezone for shifts and calendars",
        widget=forms.Select(attrs={
            "id": "id_timezone",
            "class": "form-select",
            "required": "required"
        })
    )
    contact_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Rajesh Kumar"})
    )
    contact_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"placeholder": "contact@example.com"})
    )
    contact_phone = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. +91 98765 43210", "id": "id_contact_phone", "type": "tel"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        data = self.data or self.initial
        country = "IN"
        if data:
            c = (data.get("country") or "").strip().upper()
            if c:
                country = c
                if c not in [code for code, _ in self.fields["country"].choices]:
                    self.fields["country"].choices = list(self.fields["country"].choices) + [(c, c)]
            tz = (data.get("timezone") or "").strip()
            if tz and tz not in [code for code, _ in self.fields["timezone"].choices]:
                self.fields["timezone"].choices = list(self.fields["timezone"].choices) + [(tz, tz)]

        # Dynamically build State choices
        states = STATES_BY_COUNTRY.get(country, [])
        state_choices = [("", "Select State / Region...")] + [(s, s) for s in states]
        curr_state = (data.get("state_region") or "").strip() if data else ""
        if curr_state and curr_state not in [s for s, _ in state_choices]:
            state_choices.append((curr_state, curr_state))
        self.fields["state_region"].widget.choices = state_choices

        # Dynamically build City choices
        city_choices = [("", "Select City...")]
        if curr_state and curr_state in CITIES_BY_STATE:
            city_choices += [(c, c) for c in CITIES_BY_STATE[curr_state]]
        elif country == "IN":
            # Provide sample major cities if no state selected yet
            top_cities = ["Bengaluru", "Mumbai", "New Delhi", "Chennai", "Hyderabad", "Kolkata", "Pune", "Ahmedabad"]
            city_choices += [(c, c) for c in top_cities]
        curr_city = (data.get("city") or "").strip() if data else ""
        if curr_city and curr_city not in [c for c, _ in city_choices]:
            city_choices.append((curr_city, curr_city))
        self.fields["city"].widget.choices = city_choices

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if not code:
            raise forms.ValidationError("Site code is required.")
        if len(code) > 30:
            raise forms.ValidationError("Site code must not exceed 30 characters.")
        if not _CODE_RE.fullmatch(code):
            raise forms.ValidationError("Site code must start with a letter/digit and contain only uppercase letters, numbers, '.', '_' or '-'.")
        return code

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Site name is required.")
        if len(name) < 2:
            raise forms.ValidationError("Site name must be at least 2 characters long.")
        if len(name) > 150:
            raise forms.ValidationError("Site name must not exceed 150 characters.")
        if name.isdigit():
            raise forms.ValidationError("Site name cannot consist solely of digits.")
        return name

    def clean_country(self):
        country = (self.cleaned_data.get("country") or "").strip().upper()
        if not country:
            raise forms.ValidationError("Country is required. Please select a country.")
        if not re.fullmatch(r"^[A-Z]{2}$", country):
            raise forms.ValidationError("Country must be a valid 2-letter ISO code.")
        return country

    def clean_timezone(self):
        tz = (self.cleaned_data.get("timezone") or "").strip()
        if not tz:
            raise forms.ValidationError("Timezone is required. Please select a timezone.")
        try:
            zoneinfo.ZoneInfo(tz)
        except Exception:
            raise forms.ValidationError(f"Unknown timezone '{tz}'. Please select a valid timezone.")
        return tz

    def clean_state_region(self):
        state = (self.cleaned_data.get("state_region") or "").strip()
        country = (self.cleaned_data.get("country") or "").strip().upper()
        if not state:
            raise forms.ValidationError("State / region is required. Please select a state.")
        if len(state) > 100:
            raise forms.ValidationError("State / region must not exceed 100 characters.")
        if country == "IN":
            if state not in INDIA_STATE_PIN_PREFIXES and state not in STATES_BY_COUNTRY.get("IN", []):
                raise forms.ValidationError(f"'{state}' is not a recognized Indian State or Union Territory.")
        return state

    def clean_city(self):
        city = (self.cleaned_data.get("city") or "").strip()
        if not city:
            raise forms.ValidationError("City is required. Please select or enter a city.")
        if len(city) < 2:
            raise forms.ValidationError("City name must be at least 2 characters.")
        if len(city) > 100:
            raise forms.ValidationError("City must not exceed 100 characters.")
        return city

    def clean_postal_code(self):
        postal = (self.cleaned_data.get("postal_code") or "").strip()
        country = (self.cleaned_data.get("country") or "").strip().upper()
        if not postal:
            raise forms.ValidationError("Postal / PIN code is required.")
        if len(postal) > 20:
            raise forms.ValidationError("Postal code must not exceed 20 characters.")
        if country == "IN":
            if not re.fullmatch(r"^[1-9][0-9]{5}$", postal):
                raise forms.ValidationError("Invalid Indian PIN code (must be exactly 6 digits, e.g. 560001).")
        elif country == "US":
            if not re.fullmatch(r"^\d{5}(-\d{4})?$", postal):
                raise forms.ValidationError("Invalid US ZIP code (must be 5 digits or 5+4, e.g. 90210).")
        else:
            if not re.fullmatch(r"^[A-Za-z0-9\s\-]{2,20}$", postal):
                raise forms.ValidationError("Postal code contains invalid characters.")
        return postal

    def clean(self):
        cleaned_data = super().clean()
        country = (cleaned_data.get("country") or "").strip().upper()
        state = (cleaned_data.get("state_region") or "").strip()
        city = (cleaned_data.get("city") or "").strip()
        postal = (cleaned_data.get("postal_code") or "").strip()

        if country == "IN":
            if not state:
                self.add_error("state_region", "State / region is required for Indian sites.")
            if not city:
                self.add_error("city", "City is required for Indian sites.")
            if not postal:
                self.add_error("postal_code", "PIN code is required for Indian sites.")

            # Validate PIN code against state postal circle
            if state and postal and re.fullmatch(r"^[1-9][0-9]{5}$", postal):
                is_valid, err_msg = validate_india_pincode(state, postal)
                if not is_valid:
                    self.add_error("postal_code", err_msg)

            # Check if city belongs to the selected state
            if city and state:
                expected_state = CITY_TO_STATE.get(city)
                if expected_state:
                    norm_expected = "Delhi" if "Delhi" in expected_state else expected_state
                    norm_state = "Delhi" if "Delhi" in state else state
                    if norm_expected != norm_state:
                        self.add_error("city", f"'{city}' is located in {expected_state}, but '{state}' was selected as State.")

        return cleaned_data

    def clean_contact_phone(self):
        phone = (self.cleaned_data.get("contact_phone") or "").strip()
        if phone:
            digits = [c for c in phone if c.isdigit()]
            if len(digits) < 7:
                raise forms.ValidationError("Phone number must contain at least 7 digits.")
            if len(digits) > 15:
                raise forms.ValidationError("Phone number cannot exceed 15 digits (ITU-T E.164 standard).")
            if not _PHONE_RE.fullmatch(phone):
                raise forms.ValidationError("Enter a valid phone number (e.g. +91 98765 43210).")
        return phone

    def clean_contact_email(self):
        email = (self.cleaned_data.get("contact_email") or "").strip().lower()
        return email

    def clean_contact_name(self):
        c_name = (self.cleaned_data.get("contact_name") or "").strip()
        if c_name and len(c_name) < 2:
            raise forms.ValidationError("Contact name must be at least 2 characters.")
        return c_name

    def clean_address(self):
        address = (self.cleaned_data.get("address") or "").strip()
        if address and len(address) > 500:
            raise forms.ValidationError("Address must not exceed 500 characters.")
        return address

    def clean_description(self):
        desc = (self.cleaned_data.get("description") or "").strip()
        if desc and len(desc) > 500:
            raise forms.ValidationError("Description must not exceed 500 characters.")
        return desc


class ZoneForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=120)
    zone_type = forms.ChoiceField(choices=Zone.ZoneType.choices, label="Type")
    code = forms.CharField(max_length=30, required=False)
    parent = forms.ModelChoiceField(queryset=Zone.objects.none(), required=False,
                                    empty_label="(top level of the site)")
    description = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, site, zone=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Zone.objects.filter(site=site, status="ACTIVE").order_by("name")
        if zone is not None:
            exclude = {zone.pk}
            frontier = [zone.pk]
            while frontier:  # a location cannot be moved below itself or its descendants
                frontier = list(Zone.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
                exclude.update(frontier)
            qs = qs.exclude(pk__in=exclude)
        self.fields["parent"].queryset = qs


class CalendarForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=100)
    is_24x7 = forms.BooleanField(required=False, label="Operates 24x7")
    working_days = forms.MultipleChoiceField(choices=WEEKDAYS, required=False, widget=forms.CheckboxSelectMultiple)
    start_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    end_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    is_default = forms.BooleanField(required=False, label="Default calendar of the site")
    notes = forms.CharField(max_length=300, required=False)

    def clean_working_days(self):
        return [int(d) for d in self.cleaned_data["working_days"]]


class HolidayForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    name = forms.CharField(max_length=120)


class ContactForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=150)
    role_title = forms.CharField(max_length=100, required=False, label="Role")
    phone = forms.CharField(max_length=32, required=False)
    email = forms.EmailField(required=False)
    escalation_order = forms.IntegerField(min_value=1, max_value=MAX_ESCALATION_ORDER, required=False,
                                          help_text="1 = contacted first. Leave empty to append at the end.")
    notes = forms.CharField(max_length=300, required=False)
