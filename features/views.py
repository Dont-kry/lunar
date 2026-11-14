
import csv
import io
import json
import math
import re
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone

from django.shortcuts import render
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Feature
from .serializers import FeatureSerializer


JPL_HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"

STEP_MINUTES = 30
MAX_DURATION_HOURS = 2160


def home(request):
    return render(request, "features/index.html")


class FeatureListCreateAPIView(generics.ListCreateAPIView):
    queryset = Feature.objects.all().order_by("-created_at")
    serializer_class = FeatureSerializer


class FeatureDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Feature.objects.all()
    serializer_class = FeatureSerializer


def parse_datetime(value, field_name="date and time"):
    if not value:
        raise ValueError(f"{field_name} is required.")

    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {field_name}.")

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def get_horizons_positions(
    target,
    latitude,
    longitude,
    start_time,
    end_time,
):
    """
    Retrieve apparent azimuth and elevation from JPL Horizons.

    Targets:
        10  = Sun
        399 = Earth

    Observer:
        User-defined location on the Moon.
    """

    params = {
        "format": "json",
        "COMMAND": f"'{target}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'",
        "CENTER": "'coord@301'",
        "COORD_TYPE": "'GEODETIC'",
        "SITE_COORD": (
            f"'{longitude:.6f},{latitude:.6f},0'"
        ),
        "START_TIME": (
            f"'{start_time.strftime('%Y-%m-%d %H:%M')}'"
        ),
        "STOP_TIME": (
            f"'{end_time.strftime('%Y-%m-%d %H:%M')}'"
        ),
        "STEP_SIZE": f"'{STEP_MINUTES}m'",
        "QUANTITIES": "'4'",
        "CSV_FORMAT": "'YES'",
        "TIME_TYPE": "'UT'",
        "TIME_DIGITS": "'MINUTES'",
    }

    url = (
        JPL_HORIZONS_URL
        + "?"
        + urllib.parse.urlencode(params)
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "LunarMissionBrowser/1.0"
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=60,
        ) as response:
            result = response.read().decode("utf-8")

    except Exception as error:
        raise RuntimeError(
            f"Could not contact JPL Horizons: {error}"
        )

    try:
        payload = json.loads(result)

    except json.JSONDecodeError:
        raise RuntimeError(
            "JPL returned an invalid response."
        )

    if payload.get("error"):
        raise RuntimeError(payload["error"])

    table = payload.get("result", "")

    if "$$SOE" not in table or "$$EOE" not in table:
        raise RuntimeError(
            "JPL did not return an ephemeris table."
        )

    table = (
        table.split("$$SOE", 1)[1]
        .split("$$EOE", 1)[0]
    )

    positions = {}

    date_formats = (
        "%Y-%b-%d %H:%M",
        "%Y-%b-%d %H:%M:%S",
        "%Y-%b-%d %H:%M:%S.%f",
    )

    for row in csv.reader(io.StringIO(table)):
        if not row:
            continue

        date_text = row[0].strip()

        if not re.match(
            r"^\d{4}-[A-Za-z]{3}-\d{2}",
            date_text,
        ):
            continue

        sample_time = None

        for date_format in date_formats:
            try:
                sample_time = datetime.strptime(
                    date_text,
                    date_format,
                ).replace(tzinfo=timezone.utc)

                break

            except ValueError:
                continue

        if sample_time is None:
            continue

        numeric_values = []

        for item in row[1:]:
            try:
                numeric_values.append(
                    float(item.strip())
                )

            except (ValueError, TypeError):
                continue

        if len(numeric_values) < 2:
            continue

        azimuth = numeric_values[-2]
        elevation = numeric_values[-1]

        positions[sample_time] = {
            "azimuth_deg": azimuth,
            "elevation_deg": elevation,
        }

    if not positions:
        raise RuntimeError(
            "Could not parse JPL position data. "
            "Check the Horizons response format."
        )

    return positions


def create_intervals(
    samples,
    field,
    interval_type,
    end_time,
):
    """
    Create visibility intervals from elevation samples.

    Visibility:
        Elevation greater than zero degrees.

    Interval boundaries:
        30-minute sampling resolution.
    """

    intervals = []
    active_start = None
    active_elevations = []

    for sample in samples:
        visible = (
            sample[field] is not None
            and sample[field] > 0
        )

        if visible and active_start is None:
            active_start = sample["time"]
            active_elevations = []

        if visible:
            active_elevations.append(sample[field])

        if not visible and active_start is not None:
            interval_end = sample["time"]

            intervals.append({
                "type": interval_type,
                "start": (
                    active_start.isoformat()
                    .replace("+00:00", "Z")
                ),
                "end": (
                    interval_end.isoformat()
                    .replace("+00:00", "Z")
                ),
                "metrics": {
                    "duration_hours": round(
                        (
                            interval_end - active_start
                        ).total_seconds() / 3600,
                        2,
                    ),
                    "maximum_elevation_deg": round(
                        max(active_elevations),
                        2,
                    ),
                    "mean_elevation_deg": round(
                        sum(active_elevations)
                        / len(active_elevations),
                        2,
                    ),
                },
            })

            active_start = None
            active_elevations = []

    if active_start is not None:
        intervals.append({
            "type": interval_type,
            "start": (
                active_start.isoformat()
                .replace("+00:00", "Z")
            ),
            "end": (
                end_time.isoformat()
                .replace("+00:00", "Z")
            ),
            "metrics": {
                "duration_hours": round(
                    (
                        end_time - active_start
                    ).total_seconds() / 3600,
                    2,
                ),
                "maximum_elevation_deg": round(
                    max(active_elevations),
                    2,
                ),
                "mean_elevation_deg": round(
                    sum(active_elevations)
                    / len(active_elevations),
                    2,
                ),
            },
        })

    return intervals


def total_interval_hours(intervals):
    return round(
        sum(
            item["metrics"]["duration_hours"]
            for item in intervals
        ),
        2,
    )


def iso_utc(value):
    return (
        value.isoformat()
        .replace("+00:00", "Z")
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def evaluate_mission(request):

    try:
        # -----------------------------------------
        # 1. Read location
        # -----------------------------------------

        try:
            latitude = float(
                request.data.get("latitude")
            )

            longitude = float(
                request.data.get("longitude")
            )

        except (TypeError, ValueError):
            raise ValueError(
                "Valid latitude and longitude are required."
            )

        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError(
                "Latitude and longitude must be valid numbers."
            )

        if not -90 <= latitude <= 90:
            raise ValueError(
                "Latitude must be between -90 and 90."
            )

        if not -180 <= longitude <= 360:
            raise ValueError(
                "Longitude must be between -180 and 360."
            )

        longitude = longitude % 360

        # -----------------------------------------
        # 2. Read mission start and stop times
        # -----------------------------------------

        start_time = parse_datetime(
            request.data.get("start_time"),
            "Mission start date and time",
        )

        end_time_value = (
            request.data.get("end_time")
            or request.data.get("stop_time")
        )

        if end_time_value:
            end_time = parse_datetime(
                end_time_value,
                "Mission stop date and time",
            )

            duration_hours = (
                end_time - start_time
            ).total_seconds() / 3600

        else:
            try:
                duration_hours = float(
                    request.data.get("duration_hours")
                )

            except (TypeError, ValueError):
                raise ValueError(
                    "Mission stop date and time or "
                    "duration_hours is required."
                )

            if not math.isfinite(duration_hours):
                raise ValueError(
                    "Mission duration must be a valid number."
                )

            end_time = start_time + timedelta(
                hours=duration_hours
            )

        if end_time <= start_time:
            raise ValueError(
                "Mission stop time must be after start time."
            )

        if not 0 < duration_hours <= MAX_DURATION_HOURS:
            raise ValueError(
                "Mission duration must be greater than 0 "
                "and no more than 2160 hours."
            )

        # -----------------------------------------
        # 3. Retrieve Sun positions from JPL
        # -----------------------------------------

        sun_data = get_horizons_positions(
            target=10,
            latitude=latitude,
            longitude=longitude,
            start_time=start_time,
            end_time=end_time,
        )

        # -----------------------------------------
        # 4. Retrieve Earth positions from JPL
        # -----------------------------------------

        earth_data = get_horizons_positions(
            target=399,
            latitude=latitude,
            longitude=longitude,
            start_time=start_time,
            end_time=end_time,
        )

        common_times = sorted(
            set(sun_data.keys())
            & set(earth_data.keys())
        )

        if not common_times:
            return Response(
                {
                    "detail": (
                        "No matching Sun and Earth samples "
                        "were returned by JPL."
                    )
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # -----------------------------------------
        # 5. Combine Sun and Earth samples
        # -----------------------------------------

        samples = []

        for sample_time in common_times:

            sun_position = sun_data[sample_time]
            earth_position = earth_data[sample_time]

            samples.append({
                "time": sample_time,

                "solar_azimuth_deg": round(
                    sun_position["azimuth_deg"],
                    3,
                ),

                "solar_elevation_deg": round(
                    sun_position["elevation_deg"],
                    3,
                ),

                "earth_azimuth_deg": round(
                    earth_position["azimuth_deg"],
                    3,
                ),

                "earth_elevation_deg": round(
                    earth_position["elevation_deg"],
                    3,
                ),
            })

        # -----------------------------------------
        # 6. Calculate sunlight intervals
        # -----------------------------------------

        sun_intervals = create_intervals(
            samples,
            "solar_elevation_deg",
            "sun",
            end_time,
        )

        # -----------------------------------------
        # 7. Calculate Earth visibility intervals
        # -----------------------------------------

        earth_intervals = create_intervals(
            samples,
            "earth_elevation_deg",
            "earth",
            end_time,
        )

        sunlight_hours = total_interval_hours(
            sun_intervals
        )

        communication_hours = total_interval_hours(
            earth_intervals
        )

        # -----------------------------------------
        # 8. Calculate solar metrics
        # -----------------------------------------

        solar_elevations = [
            item["solar_elevation_deg"]
            for item in samples
        ]

        solar_power_efficiency = (
            sum(
                max(
                    0,
                    math.sin(math.radians(value)),
                )
                for value in solar_elevations
            )
            / len(solar_elevations)
        ) * 100

        # -----------------------------------------
        # 9. Combine intervals
        # -----------------------------------------

        intervals = sorted(
            sun_intervals + earth_intervals,
            key=lambda item: item["start"],
        )

        sun_visible = sunlight_hours > 0
        earth_visible = communication_hours > 0

        # -----------------------------------------
        # 10. Feasibility assessment
        # -----------------------------------------

        reasons = []

        if not sun_visible:
            reasons.append(
                "No sampled sunlight availability "
                "during this period."
            )

        if not earth_visible:
            reasons.append(
                "No sampled direct Earth visibility "
                "during this period."
            )

        if sun_visible and earth_visible:
            rating = "Potentially feasible"

            summary = (
                "Sunlight and direct Earth visibility "
                "were both available during the selected "
                "period. Further engineering checks are required."
            )

        elif sun_visible or earth_visible:
            rating = "Limited availability"

            summary = (
                "Only one of the two required visibility "
                "conditions was available during the selected period."
            )

        else:
            rating = "Low availability"

            summary = (
                "No sampled sunlight or direct Earth visibility "
                "was found during the selected period."
            )

        # -----------------------------------------
        # 11. Prepare frontend samples
        # -----------------------------------------

        response_samples = []

        for item in samples:
            response_samples.append({
                "time": iso_utc(item["time"]),

                "solar_azimuth_deg": (
                    item["solar_azimuth_deg"]
                ),

                "solar_elevation_deg": (
                    item["solar_elevation_deg"]
                ),

                "earth_azimuth_deg": (
                    item["earth_azimuth_deg"]
                ),

                "earth_elevation_deg": (
                    item["earth_elevation_deg"]
                ),
            })

        first_sample = samples[0]
        last_sample = samples[-1]

        # -----------------------------------------
        # 12. Return API response
        # -----------------------------------------

        return Response({

            "start_time": iso_utc(start_time),

            "end_time": iso_utc(end_time),

            "location": {
                "latitude": latitude,
                "longitude": longitude,
            },

            "metrics": {

                "solar_elevation_deg": round(
                    max(solar_elevations),
                    2,
                ),

                "sunlight_hours": sunlight_hours,

                "solar_power_efficiency_percent": round(
                    solar_power_efficiency,
                    2,
                ),

                "sun_visible": sun_visible,

                "earth_visible": earth_visible,

                "communication_hours": communication_hours,
            },

            "positions_at_start": {

                "time": iso_utc(
                    first_sample["time"]
                ),

                "sun": {
                    "azimuth_deg": (
                        first_sample["solar_azimuth_deg"]
                    ),
                    "elevation_deg": (
                        first_sample["solar_elevation_deg"]
                    ),
                },

                "earth": {
                    "azimuth_deg": (
                        first_sample["earth_azimuth_deg"]
                    ),
                    "elevation_deg": (
                        first_sample["earth_elevation_deg"]
                    ),
                },
            },

            "positions_at_end": {

                "time": iso_utc(
                    last_sample["time"]
                ),

                "sun": {
                    "azimuth_deg": (
                        last_sample["solar_azimuth_deg"]
                    ),
                    "elevation_deg": (
                        last_sample["solar_elevation_deg"]
                    ),
                },

                "earth": {
                    "azimuth_deg": (
                        last_sample["earth_azimuth_deg"]
                    ),
                    "elevation_deg": (
                        last_sample["earth_elevation_deg"]
                    ),
                },
            },

            "intervals": intervals,

            "samples": response_samples,

            "feasibility": {
                "rating": rating,
                "summary": summary,
                "reasons": reasons,
            },

            "calculation_notes": [
                "Sun and Earth azimuth and elevation are retrieved from JPL Horizons.",
                "Positions are sampled every 30 minutes.",
                "Visibility intervals use geometric elevation greater than zero degrees.",
                "Interval boundaries have 30-minute sampling resolution.",
                "Solar power efficiency is a horizontal-panel geometric proxy.",
                "Terrain obstruction, local horizon profiles, panel orientation, and link-budget constraints are not included.",
            ],

            "data_source": {
                "name": "NASA/JPL Horizons System",
                "url": JPL_HORIZONS_URL,
                "observer": "User-defined lunar surface coordinates",
                "sampling_interval_minutes": STEP_MINUTES,
            },
        })

    except ValueError as error:
        return Response(
            {"detail": str(error)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    except RuntimeError as error:
        return Response(
            {"detail": str(error)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    except Exception as error:
        return Response(
            {
                "detail": (
                    f"Mission evaluation failed: {error}"
                )
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
