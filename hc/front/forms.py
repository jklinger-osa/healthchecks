from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timezone
from datetime import timedelta as td
from typing import Any, ClassVar, cast
from zoneinfo import ZoneInfo

from django import forms

from hc.accounts.models import Project
from hc.api.models import Check, MaintenanceWindow
from hc.front.validators import (
    CronValidator,
    OnCalendarValidator,
    TimezoneValidator,
    WebhookValidator,
)
from hc.lib.tz import all_timezones


def _choices(csv: str) -> list[tuple[str, str]]:
    return [(v, v) for v in csv.split(",")]


class LaxURLField(forms.URLField):
    """Subclass of URLField which additionally accepts URLs without a tld.

    For example, unlike URLField, it accepts "http://home_server"

    """

    default_validators = [WebhookValidator()]


class NameTagsForm(forms.Form):
    name = forms.CharField(max_length=100, required=False)
    slug = forms.SlugField(max_length=100, required=False)
    tags = forms.CharField(max_length=500, required=False)
    desc = forms.CharField(max_length=10000, required=False)

    def clean_tags(self) -> str:
        result = []

        for part in self.cleaned_data["tags"].split(" "):
            part = part.strip()
            if part != "":
                result.append(part)

        return " ".join(result)


class AddCheckForm(NameTagsForm):
    kind = forms.ChoiceField(choices=_choices("simple,cron,oncalendar"))
    timeout = forms.IntegerField(min_value=60, max_value=31536000)
    schedule = forms.CharField(required=False, max_length=100)
    tz = forms.CharField(max_length=36, validators=[TimezoneValidator()])
    grace = forms.IntegerField(min_value=60, max_value=31536000)

    def clean_timeout(self) -> td:
        return td(seconds=self.cleaned_data["timeout"])

    def clean_grace(self) -> td:
        return td(seconds=self.cleaned_data["grace"])

    def clean_schedule(self) -> str:
        kind = self.cleaned_data.get("kind")
        if kind == "cron":
            cron_validator = CronValidator()
            cron_validator(self.cleaned_data["schedule"])
        elif kind == "oncalendar":
            oncalendar_validator = OnCalendarValidator()
            oncalendar_validator(self.cleaned_data["schedule"])
        else:
            # If kind is not cron or oncalendar, ignore the passed in value
            # and use "* * * * *" instead.
            return "* * * * *"

        assert isinstance(self.cleaned_data["schedule"], str)
        return self.cleaned_data["schedule"]


class FilteringRulesForm(forms.Form):
    filter_subject = forms.BooleanField(required=False)
    filter_body = forms.BooleanField(required=False)
    filter_http_body = forms.BooleanField(required=False)
    filter_default_fail = forms.BooleanField(required=False)
    start_kw = forms.CharField(required=False, max_length=200)
    success_kw = forms.CharField(required=False, max_length=200)
    failure_kw = forms.CharField(required=False, max_length=200)
    methods = forms.ChoiceField(required=False, choices=(("", "Any"), ("POST", "POST")))
    manual_resume = forms.BooleanField(required=False)


class TimeoutForm(forms.Form):
    timeout = forms.IntegerField(min_value=60, max_value=31536000)
    grace = forms.IntegerField(min_value=60, max_value=31536000)

    def clean_timeout(self) -> td:
        return td(seconds=self.cleaned_data["timeout"])

    def clean_grace(self) -> td:
        return td(seconds=self.cleaned_data["grace"])


class CronPreviewForm(forms.Form):
    schedule = forms.CharField(max_length=100, validators=[CronValidator()])
    tz = forms.CharField(max_length=36, validators=[TimezoneValidator()])


class CronForm(forms.Form):
    schedule = forms.CharField(max_length=100, validators=[CronValidator()])
    tz = forms.CharField(max_length=36, validators=[TimezoneValidator()])
    grace = forms.IntegerField(min_value=60, max_value=31536000)

    def clean_grace(self) -> td:
        return td(seconds=self.cleaned_data["grace"])


class OnCalendarForm(forms.Form):
    schedule = forms.CharField(max_length=100, validators=[OnCalendarValidator()])
    tz = forms.CharField(max_length=36, validators=[TimezoneValidator()])
    grace = forms.IntegerField(min_value=60, max_value=31536000)

    def clean_grace(self) -> td:
        return td(seconds=self.cleaned_data["grace"])


class AddUrlForm(forms.Form):
    error_css_class = "has-error"
    value = LaxURLField(max_length=1000, assume_scheme="https")


class PhoneNumberForm(forms.Form):
    error_css_class = "has-error"
    label = forms.CharField(max_length=100, required=False)
    phone = forms.CharField()

    def clean_phone(self) -> str:
        v = self.cleaned_data["phone"]
        assert isinstance(v, str)

        stripped = v.encode("ascii", "ignore").decode("ascii")
        stripped = stripped.replace(" ", "").replace("-", "")
        if not re.match(r"^\+\d{5,15}$", stripped):
            raise forms.ValidationError("Invalid phone number format.")

        return stripped

    def get_json(self) -> str:
        return json.dumps({"value": self.cleaned_data["phone"]})


class PhoneUpDownForm(PhoneNumberForm):
    up = forms.BooleanField(required=False, initial=True)
    down = forms.BooleanField(required=False, initial=True)

    def clean(self) -> None:
        super().clean()

        down = self.cleaned_data.get("down")
        up = self.cleaned_data.get("up")

        if not down and not up:
            self.add_error("down", "Please select at least one.")

    def get_json(self) -> str:
        return json.dumps(
            {
                "value": self.cleaned_data["phone"],
                "up": self.cleaned_data["up"],
                "down": self.cleaned_data["down"],
            }
        )


class ChannelNameForm(forms.Form):
    name = forms.CharField(max_length=100, required=False)


class SearchForm(forms.Form):
    q = forms.RegexField(regex=r"^[0-9a-zA-Z\s]{3,100}$")


class LogFiltersForm(forms.Form):
    # min_value is 2010-01-01, max_value is 2030-01-01
    u = forms.FloatField(min_value=1262296800, max_value=1893448800, required=False)
    end = forms.FloatField(min_value=1262296800, max_value=1893448800, required=False)
    success = forms.BooleanField(required=False)
    fail = forms.BooleanField(required=False)
    start = forms.BooleanField(required=False)
    log = forms.BooleanField(required=False)
    ign = forms.BooleanField(required=False)
    notification = forms.BooleanField(required=False)
    flip = forms.BooleanField(required=False)

    def clean_u(self) -> datetime | None:
        if self.cleaned_data["u"]:
            return datetime.fromtimestamp(self.cleaned_data["u"], tz=timezone.utc)
        return None

    def clean_end(self) -> datetime | None:
        if self.cleaned_data["end"]:
            return datetime.fromtimestamp(self.cleaned_data["end"], tz=timezone.utc)
        return None

    def kinds(self) -> tuple[str, ...]:
        kind_keys = ("success", "fail", "start", "log", "ign", "notification", "flip")
        return tuple(key for key in kind_keys if self.cleaned_data[key])


class TransferForm(forms.Form):
    project = forms.UUIDField()


class BadgeSettingsForm(forms.Form):
    target = forms.ChoiceField(choices=_choices("all,tag,check"))
    tag = forms.CharField(max_length=100, required=False)
    check = forms.UUIDField(required=False)
    fmt = forms.ChoiceField(choices=_choices("svg,json,shields"))
    states = forms.ChoiceField(choices=_choices("2,3"))


class MaintenanceCheckChoiceField(forms.ModelMultipleChoiceField[Check]):
    def label_from_instance(self, obj: Check) -> str:
        return obj.name_then_code()


class MaintenanceWindowForm(forms.ModelForm[MaintenanceWindow]):
    checks = MaintenanceCheckChoiceField(
        queryset=Check.objects.none(),
        label="Services",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    all_projects = forms.BooleanField(label="All projects", required=False)
    tz = forms.ChoiceField(
        label="Time zone", choices=[(zone, zone) for zone in all_timezones]
    )
    start_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    start_time = forms.TimeField(
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"})
    )
    end_time = forms.TimeField(
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"})
    )
    weekdays = forms.TypedMultipleChoiceField(
        coerce=int,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        choices=list(
            enumerate(
                (
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                )
            )
        ),
    )

    class Meta:
        model = MaintenanceWindow
        fields = (
            "name",
            "enabled",
            "kind",
            "tz",
            "start_date",
            "start_time",
            "end_date",
            "end_time",
            "weekdays",
            "all_projects",
            "all_checks",
            "checks",
        )
        labels: ClassVar[dict[str, str]] = {
            "all_checks": "All services in this project",
            "checks": "Services",
        }

    def __init__(
        self, *args: Any, project: Project, allow_global: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.project = project
        self.allow_global = allow_global
        if not self.instance.pk:
            self.instance.project = project
        self.initial["all_projects"] = self.instance.all_projects
        checks_field = cast(MaintenanceCheckChoiceField, self.fields["checks"])
        checks_field.queryset = project.check_set.order_by("name", "id")
        for name in (
            "name",
            "kind",
            "tz",
            "start_date",
            "end_date",
            "start_time",
            "end_time",
        ):
            self.fields[name].widget.attrs["class"] = "form-control"
        for field, value in (
            ("start_date", self.instance.starts_at),
            ("end_date", self.instance.ends_at),
        ):
            if value:
                self.initial[field] = value.astimezone(
                    ZoneInfo(self.instance.tz)
                ).date()

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("all_projects"):
            if not self.allow_global:
                self.add_error(
                    "all_projects",
                    "Only superusers can manage all-project maintenance.",
                )
            self.instance.project = None
            data["all_checks"] = True
            data["checks"] = self.project.check_set.none()
        else:
            self.instance.project = self.project
            if not data.get("all_checks") and not data.get("checks"):
                self.add_error(
                    "checks",
                    "Select at least one service or all services in this project.",
                )
        if data.get("kind") == "one_time":
            zone_name = data.get("tz")
            if zone_name:
                for date_field, time_field, attribute in (
                    ("start_date", "start_time", "starts_at"),
                    ("end_date", "end_time", "ends_at"),
                ):
                    if not data.get(date_field):
                        self.add_error(
                            date_field, "A date is required for a one-time window."
                        )
                    elif data.get(time_field):
                        value = datetime.combine(
                            data[date_field], data[time_field], ZoneInfo(zone_name)
                        )
                        if attribute == "ends_at":
                            value = value.replace(fold=1)
                        setattr(self.instance, attribute, value.astimezone(UTC))
        else:
            self.instance.starts_at = self.instance.ends_at = None
        return data
