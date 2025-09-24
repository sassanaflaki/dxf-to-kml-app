import io
import math
from typing import List, Tuple

import streamlit as st
import ezdxf
from ezdxf.entities import Line, LWPolyline, Polyline, Point, Circle, Arc, Spline
from pyproj import Transformer, CRS
import simplekml

# ------------------------
# Helpers
# ------------------------

def get_transformer(input_crs: CRS):
    """Return a transformer from input CRS to WGS84 (EPSG:4326)."""
    return Transformer.from_crs(input_crs, CRS.from_epsg(4326), always_xy=True)


def transform_xy_list(transformer: Transformer, xy: List[Tuple[float, float]]):
    out = []
    for x, y in xy:
        lon, lat = transformer.transform(x, y)
        out.append((lon, lat))
    return out


def is_closed_lwpoly(lw: LWPolyline) -> bool:
    if lw.closed:
        return True
    # If not flagged closed, check first/last equality
    pts = [(v[0], v[1]) for v in lw]
    return len(pts) > 2 and (abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6)


def arc_to_polyline(center, radius, start_angle, end_angle, segments=64):
    """Approximate an ARC into vertices in OCS plane (assumes WCS already). Angles in degrees."""
    if end_angle < start_angle:
        end_angle += 360.0
    step = (end_angle - start_angle) / max(1, segments)
    pts = []
    for i in range(segments + 1):
        ang = math.radians(start_angle + i * step)
