import io
import math
import tempfile
from typing import List, Tuple

import streamlit as st
import ezdxf
from ezdxf import recover
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
        x = center[0] + radius * math.cos(ang)
        y = center[1] + radius * math.sin(ang)
        pts.append((x, y))
    return pts


def spline_to_polyline(spline: Spline, segments=100):
    try:
        pts = [spline.point(i / segments) for i in range(segments + 1)]
        return [(p[0], p[1]) for p in pts]
    except Exception:
        # Fallback to control points if NURBS evaluation not available
        return [(p[0], p[1]) for p in spline.control_points]


# ------------------------
# UI
# ------------------------

st.set_page_config(page_title="DXF ➜ KML (State Plane ftUS)", layout="wide")
st.title("DXF ➜ KML converter")
st.caption("Reads a DXF drawn in State Plane US survey feet, transforms to WGS84, and exports KML.")

with st.sidebar:
    st.header("Coordinate System")
    epsg_choice = st.selectbox(
        "Input EPSG (State Plane ftUS)",
        [
            "Maryland ftUS (EPSG:2248)",
            "Virginia North ftUS (EPSG:2283)",
            "Custom EPSG...",
        ],
    )
    if epsg_choice == "Maryland ftUS (EPSG:2248)":
        input_epsg = 2248
    elif epsg_choice == "Virginia North ftUS (EPSG:2283)":
        input_epsg = 2283
    else:
        input_epsg = st.number_input("Custom EPSG code", min_value=2000, max_value=900000, value=2248, step=1)

    elevation_mode = st.selectbox("KML altitude mode", ["clampToGround", "absolute", "relativeToGround"], index=0)
    include_layers = st.text_input("Only include layers (comma-separated, blank = all)", value="")
    approx_segments = st.slider("Curve approximation segments", min_value=12, max_value=200, value=64, step=4)

uploaded = st.file_uploader("Upload DXF", type=["dxf"]) 

if uploaded:
    # Read DXF from UploadedFile by writing to a temp file, then using ezdxf.recover
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=True) as tmp:
            tmp.write(uploaded.getbuffer())
            tmp.flush()
            doc, auditor = recover.readfile(tmp.name)
        if auditor.has_errors:
            st.warning(f"DXF recovered with {len(auditor.errors)} issue(s). Proceeding.")
        msp = doc.modelspace()
    except Exception as e:
        st.error(f"Failed to read DXF: {e}")
        st.stop()

    # Filter layers
    layer_whitelist = None
    if include_layers.strip():
        layer_whitelist = {l.strip() for l in include_layers.split(',') if l.strip()}

    # Set up transform
    try:
        input_crs = CRS.from_epsg(int(input_epsg))
        transformer = get_transformer(input_crs)
    except Exception as e:
        st.error(f"Invalid EPSG {input_epsg}: {e}")
        st.stop()

    kml = simplekml.Kml()
    kml_alt = {
        "clampToGround": simplekml.AltitudeMode.clamptoground,
        "absolute": simplekml.AltitudeMode.absolute,
        "relativeToGround": simplekml.AltitudeMode.relativetoground,
    }[elevation_mode]

    count = {"points": 0, "lines": 0, "polylines": 0, "polygons": 0}

    def layer_ok(ent_layer: str) -> bool:
        return (layer_whitelist is None) or (ent_layer in layer_whitelist)

    # Iterate entities
    for e in msp:
        try:
            if isinstance(e, Point) and layer_ok(e.dxf.layer):
                x, y, z = e.dxf.location.x, e.dxf.location.y, e.dxf.location.z if hasattr(e.dxf.location, 'z') else 0.0
                lon, lat = transformer.transform(x, y)
                p = kml.newpoint(name=f"POINT:{e.dxf.layer}", coords=[(lon, lat, z)])
                p.altitudemode = kml_alt
                count["points"] += 1

            elif isinstance(e, Line) and layer_ok(e.dxf.layer):
                p1 = (e.dxf.start.x, e.dxf.start.y)
                p2 = (e.dxf.end.x, e.dxf.end.y)
                coords = transform_xy_list(transformer, [p1, p2])
                ls = kml.newlinestring(name=f"LINE:{e.dxf.layer}", coords=[(lon, lat, 0.0) for lon, lat in coords])
                ls.altitudemode = kml_alt
                count["lines"] += 1

            elif isinstance(e, LWPolyline) and layer_ok(e.dxf.layer):
                pts = [(v[0], v[1]) for v in e]
                coords = transform_xy_list(transformer, pts)
                if is_closed_lwpoly(e) and len(coords) >= 3:
                    pg = kml.newpolygon(name=f"POLY:{e.dxf.layer}")
                    pg.outerboundaryis = [(lon, lat, 0.0) for lon, lat in coords]
                    pg.altitudemode = kml_alt
                    count["polygons"] += 1
                else:
                    ls = kml.newlinestring(name=f"LWPOLY:{e.dxf.layer}", coords=[(lon, lat, 0.0) for lon, lat in coords])
                    ls.altitudemode = kml_alt
                    count["polylines"] += 1

            elif isinstance(e, Polyline) and layer_ok(e.dxf.layer):
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                coords = transform_xy_list(transformer, pts)
                closed = e.is_closed
                if closed and len(coords) >= 3:
                    pg = kml.newpolygon(name=f"POLY:{e.dxf.layer}")
                    pg.outerboundaryis = [(lon, lat, 0.0) for lon, lat in coords]
                    pg.altitudemode = kml_alt
                    count["polygons"] += 1
                else:
                    ls = kml.newlinestring(name=f"POLYLINE:{e.dxf.layer}", coords=[(lon, lat, 0.0) for lon, lat in coords])
                    ls.altitudemode = kml_alt
                    count["polylines"] += 1

            elif isinstance(e, Circle) and layer_ok(e.dxf.layer):
                center = (e.dxf.center.x, e.dxf.center.y)
                pts = arc_to_polyline(center, e.dxf.radius, 0.0, 360.0, segments=approx_segments)
                coords = transform_xy_list(transformer, pts)
                ls = kml.newlinestring(name=f"CIRCLE:{e.dxf.layer}", coords=[(lon, lat, 0.0) for lon, lat in coords])
                ls.altitudemode = kml_alt
                count["polylines"] += 1

            elif isinstance(e, Arc) and layer_ok(e.dxf.layer):
                center = (e.dxf.center.x, e.dxf.center.y)
                pts = arc_to_polyline(center, e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle, segments=approx_segments)
                coords = transform_xy_list(transformer, pts)
                ls = kml.newlinestring(name=f"ARC:{e.dxf.layer}", coords=[(lon, lat, 0.0) for lon, lat in coords])
                ls.altitudemode = kml_alt
                count["polylines"] += 1

            elif isinstance(e, Spline) and layer_ok(e.dxf.layer):
                pts = spline_to_polyline(e, segments=approx_segments)
                coords = transform_xy_list(transformer, pts)
                ls = kml.newlinestring(name=f"SPLINE:{e.dxf.layer}", coords=[(lon, lat, 0.0) for lon, lat in coords])
                ls.altitudemode = kml_alt
                count["polylines"] += 1

        except Exception as ex:
            st.warning(f"Skipped {e.dxftype()} on layer {getattr(e.dxf, 'layer', '?')}: {ex}")

    # Write to memory and offer download
    xml = kml.kml()

    st.success(f"KML created | points: {count['points']}  lines: {count['lines']}  polylines: {count['polylines']}  polygons: {count['polygons']}")
    st.download_button(
        label="Download KML",
        data=xml.encode("utf-8"),
        file_name="export.kml",
        mime="application/vnd.google-earth.kml+xml",
    )

    with st.expander("Details"):
        st.json({"EPSG_in": int(input_epsg), "altitudeMode": elevation_mode, "layer_filter": sorted(list(layer_whitelist)) if layer_whitelist else "ALL"})

else:
    st.info("Upload a DXF to start.")
