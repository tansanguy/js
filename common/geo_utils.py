"""Geographic helpers for project preparation steps."""

from __future__ import annotations

import math
from typing import Any


EARTH_RADIUS_M = 6_371_008.8


def validate_lat_lon(lat: float, lon: float, label: str) -> tuple[float, float]:
    if not isinstance(lat, int | float):
        raise ValueError(f"ERROR: latitude for {label} must be numeric")
    if not isinstance(lon, int | float):
        raise ValueError(f"ERROR: longitude for {label} must be numeric")

    lat_value = float(lat)
    lon_value = float(lon)
    if lat_value < -90.0 or lat_value > 90.0:
        raise ValueError(f"ERROR: invalid latitude for {label}: {lat_value}")
    if lon_value < -180.0 or lon_value > 180.0:
        raise ValueError(f"ERROR: invalid longitude for {label}: {lon_value}")
    return lat_value, lon_value


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    x = math.sin(delta_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def midpoint_latlon(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    phi1 = math.radians(lat1)
    lambda1 = math.radians(lon1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    bx = math.cos(phi2) * math.cos(delta_lambda)
    by = math.cos(phi2) * math.sin(delta_lambda)
    phi3 = math.atan2(
        math.sin(phi1) + math.sin(phi2),
        math.sqrt((math.cos(phi1) + bx) ** 2 + by**2),
    )
    lambda3 = lambda1 + math.atan2(by, math.cos(phi1) + bx)
    return math.degrees(phi3), ((math.degrees(lambda3) + 540.0) % 360.0) - 180.0


def latlon_to_local_xy_m(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    x_m = math.radians(lon - origin_lon) * EARTH_RADIUS_M * math.cos(math.radians(origin_lat))
    y_m = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x_m, y_m


def local_xy_m_to_latlon(x_m: float, y_m: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    lat = origin_lat + math.degrees(y_m / EARTH_RADIUS_M)
    lon = origin_lon + math.degrees(x_m / (EARTH_RADIUS_M * math.cos(math.radians(origin_lat))))
    return lat, lon


def oriented_ellipse_polygon(
    center_lat: float,
    center_lon: float,
    semi_major_axis_m: float,
    semi_minor_axis_m: float,
    bearing_deg: float,
    num_points: int,
) -> list[tuple[float, float]]:
    bearing_rad = math.radians(bearing_deg)
    points: list[tuple[float, float]] = []

    for index in range(num_points):
        theta = 2.0 * math.pi * index / num_points
        x_major = semi_major_axis_m * math.cos(theta)
        y_minor = semi_minor_axis_m * math.sin(theta)

        east_m = x_major * math.sin(bearing_rad) + y_minor * math.sin(bearing_rad + math.pi / 2.0)
        north_m = x_major * math.cos(bearing_rad) + y_minor * math.cos(bearing_rad + math.pi / 2.0)
        points.append(local_xy_m_to_latlon(east_m, north_m, center_lat, center_lon))

    points.append(points[0])
    return points


def bbox_from_points(points: list[tuple[float, float]]) -> dict[str, float]:
    lats = [lat for lat, _lon in points]
    lons = [lon for _lat, lon in points]
    return {
        "min_lon": min(lons),
        "min_lat": min(lats),
        "max_lon": max(lons),
        "max_lat": max(lats),
    }


def expand_bbox_m(bbox: dict[str, float], buffer_m: float) -> dict[str, float]:
    center_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2.0
    lat_delta = math.degrees(buffer_m / EARTH_RADIUS_M)
    lon_delta = math.degrees(buffer_m / (EARTH_RADIUS_M * math.cos(math.radians(center_lat))))
    return {
        "min_lon": bbox["min_lon"] - lon_delta,
        "min_lat": bbox["min_lat"] - lat_delta,
        "max_lon": bbox["max_lon"] + lon_delta,
        "max_lat": bbox["max_lat"] + lat_delta,
    }


def geojson_feature(
    geometry_type: str,
    coordinates: Any,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {
            "type": geometry_type,
            "coordinates": coordinates,
        },
        "properties": properties,
    }
