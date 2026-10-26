"""Parse the --bbox CLI argument into an EPSG:4326 (xmin, ymin, xmax, ymax) tuple."""

import json


def _validate_bbox_values(xmin, ymin, xmax, ymax):
    """Validate numeric bbox coordinate ranges and minimum size."""
    if xmin >= xmax:
        raise ValueError("xmin must be less than xmax")
    if ymin >= ymax:
        raise ValueError("ymin must be less than ymax")

    if xmin < -180 or xmax > 180:
        raise ValueError("Longitude values must be between -180 and 180")
    if ymin < -90 or ymax > 90:
        raise ValueError("Latitude values must be between -90 and 90")

    if (xmax - xmin) < 0.001 or (ymax - ymin) < 0.001:
        raise ValueError("Bbox is too small. Minimum size is 0.001 degrees in both dimensions")


def _parse_bbox_from_string(bbox_str):
    """
    Parse bbox from the legacy string format.

    Supports:
        - 'xmin,ymin,xmax,ymax'
        - 'xmin ymin xmax ymax'
    """
    if not bbox_str:
        return None

    bbox_str = bbox_str.strip()
    if bbox_str.lower() in ("null", "none") or bbox_str == "[]":
        return None

    if "," in bbox_str:
        parts = bbox_str.split(",")
    else:
        parts = bbox_str.split()

    if len(parts) != 4:
        raise ValueError("Bbox must have exactly 4 values: xmin, ymin, xmax, ymax")

    try:
        xmin, ymin, xmax, ymax = map(float, parts)
    except ValueError:
        raise ValueError("All bbox values must be valid numbers")

    _validate_bbox_values(xmin, ymin, xmax, ymax)
    return xmin, ymin, xmax, ymax


def _parse_bbox_from_geojson_feature(feature_obj):
    """
    Parse bbox from a GeoJSON Feature Polygon object.

    Expects a dict with:
        type: "Feature"
        geometry.type: "Polygon"
        geometry.coordinates: [[[x, y], ...]]
    """
    if not isinstance(feature_obj, dict):
        raise ValueError("GeoJSON bbox feature must be a JSON object")

    if feature_obj.get("type") != "Feature":
        raise ValueError('GeoJSON bbox feature must have type "Feature"')

    geometry = feature_obj.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("GeoJSON bbox feature must have a geometry object")

    if geometry.get("type") != "Polygon":
        raise ValueError('GeoJSON bbox geometry.type must be "Polygon"')

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("GeoJSON bbox Polygon must have non-empty coordinates")

    ring = coordinates[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("GeoJSON bbox Polygon must have at least 4 coordinate pairs")

    xs = []
    ys = []
    for coord in ring:
        if not isinstance(coord, (list, tuple)) or len(coord) < 2:
            raise ValueError("GeoJSON bbox coordinates must be [x, y] pairs")
        x, y = coord[0], coord[1]
        try:
            x = float(x)
            y = float(y)
        except (TypeError, ValueError):
            raise ValueError("GeoJSON bbox coordinates must be numeric")
        xs.append(x)
        ys.append(y)

    xmin = min(xs)
    xmax = max(xs)
    ymin = min(ys)
    ymax = max(ys)

    _validate_bbox_values(xmin, ymin, xmax, ymax)
    return xmin, ymin, xmax, ymax


def parse_bbox(bbox_input):
    """
    Parse bbox from either a legacy string or a GeoJSON Feature Polygon.

    Args:
        bbox_input: String in format 'xmin,ymin,xmax,ymax' / 'xmin ymin xmax ymax',
                    or a JSON-encoded GeoJSON Feature Polygon.

    Returns:
        tuple: (xmin, ymin, xmax, ymax) as floats, or None if no bbox / null-like

    Raises:
        ValueError: If bbox format is invalid or coordinates are invalid.
    """
    if bbox_input is None:
        return None

    if isinstance(bbox_input, (dict, list)):
        return _parse_bbox_from_geojson_feature(bbox_input)

    bbox_str = str(bbox_input).strip()
    if not bbox_str:
        return None

    if bbox_str.lower() in ("null", "none") or bbox_str == "[]":
        return None

    # Try to detect JSON first for GeoJSON Feature support
    if bbox_str.startswith("{") or bbox_str.startswith("["):
        try:
            json_obj = json.loads(bbox_str)
        except json.JSONDecodeError:
            # Fall back to legacy string parsing if JSON is invalid
            return _parse_bbox_from_string(bbox_str)

        # If it's a dict with Feature/Polygon shape, parse as GeoJSON
        if isinstance(json_obj, dict) and json_obj.get("type") == "Feature":
            return _parse_bbox_from_geojson_feature(json_obj)

        # Otherwise, fall back to legacy behavior (may raise ValueError)
        return _parse_bbox_from_string(bbox_str)

    # Legacy string format
    return _parse_bbox_from_string(bbox_str)
