import streamlit as st
import ee
import folium
from folium.plugins import MeasureControl, Fullscreen, MousePosition
from streamlit_folium import st_folium
from datetime import datetime, timedelta
import io
import os
from html2image import Html2Image

# Imports for automated cartographic PDF construction
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# -------------------------------------------------------------
# STREAMLIT PAGE SETUP
# -------------------------------------------------------------
st.set_page_config(layout="wide", page_title="PNG Advanced Hazard Workspace")
st.title("🇵🇬 PNG Advanced Climate Hazard Workspace")
st.markdown(
    "A public-tier monitoring system featuring real-time analytical tools, "
    "vector extractions, GeoTIFF downloads, and cartographic PDF exports."
)

# -------------------------------------------------------------
# EARTH ENGINE INITIALIZATION WITH STREAMLIT SECRETS FALLBACK
# -------------------------------------------------------------
try:
    # Streamlit Cloud option: use saved OAuth user credentials from app secrets.
    if "EARTHENGINE_CREDENTIALS" in st.secrets:
        ee_creds = st.secrets["EARTHENGINE_CREDENTIALS"]
        from google.oauth2.credentials import Credentials

        credentials = Credentials(
            token=None,
            refresh_token=ee_creds["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=ee_creds["client_id"],
            client_secret=ee_creds["client_secret"],
        )
        ee.Initialize(credentials=credentials)
    else:
        # Local desktop option after running: earthengine authenticate
        ee.Initialize()
except Exception as e:
    st.error(f"Earth Engine failed to initialize: {str(e)}")
    st.info(
        "If running on Streamlit Cloud, verify that the [EARTHENGINE_CREDENTIALS] "
        "block is saved correctly in your App Secrets panel."
    )

# -------------------------------------------------------------
# 1. CORE SPATIAL DATA SETUPS
# -------------------------------------------------------------
png_boundary = (
    ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
    .filter(ee.Filter.eq("country_na", "Papua New Guinea"))
    .geometry()
)

dem = ee.Image("USGS/SRTMGL1_003").clip(png_boundary)
elevation = dem.select("elevation")
highland_mask = elevation.gt(2200)

# -------------------------------------------------------------
# 2. ANALYSIS TIMELINES
# -------------------------------------------------------------
today = datetime.today()
safe_end_date = today - timedelta(days=15)  # lag-safe CHIRPS window
three_months_ago = safe_end_date - timedelta(days=90)
one_week_ago = today - timedelta(days=7)

# -------------------------------------------------------------
# 3. GEOSPATIAL PROCESSING FUNCTIONS
# -------------------------------------------------------------
def get_drought_layer():
    """Return 90-day CHIRPS rainfall as percentage of historical normal."""
    current_rain = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterDate(three_months_ago.strftime("%Y-%m-%d"), safe_end_date.strftime("%Y-%m-%d"))
        .sum()
        .clip(png_boundary)
    )

    # Historical mean daily rainfall for the same months, scaled to 90 days.
    baseline_rain = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filter(ee.Filter.calendarRange(three_months_ago.month, safe_end_date.month, "month"))
        .filterDate("2000-01-01", "2022-12-31")
        .mean()
        .multiply(90)
        .clip(png_boundary)
    )

    return current_rain.divide(baseline_rain).multiply(100).rename("rainfall_anomaly_pct")


def get_frost_layer():
    """Return 7-day minimum MODIS nighttime LST in Celsius, masked to >2200m."""
    collection = (
        ee.ImageCollection("MODIS/061/MOD11A1")
        .filterDate(one_week_ago.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
        .select("LST_Night_1km")
    )

    modis_lst = collection.min().clip(png_boundary)
    lst_celsius = modis_lst.multiply(0.02).subtract(273.15).rename("night_lst_celsius")
    return lst_celsius.updateMask(highland_mask)


def add_ee_layer(
    folium_map,
    ee_image_object,
    vis_params,
    name,
    opacity_val=1.0,
    control=True,
):
    """Add an Earth Engine image as a tiled layer to a Folium map."""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    folium.raster_layers.TileLayer(
        tiles=map_id_dict["tile_fetcher"].url_format,
        attr="Google Earth Engine",
        name=name,
        overlay=True,
        control=control,
        opacity=opacity_val,
    ).add_to(folium_map)


# Cache calculations to stabilize rendering.
if "drought_img" not in st.session_state:
    st.session_state.drought_img = get_drought_layer()
if "frost_img" not in st.session_state:
    st.session_state.frost_img = get_frost_layer()

# -------------------------------------------------------------
# 4. IMPROVED CARTOGRAPHIC PDF PRINT REPORT ENGINE
# -------------------------------------------------------------
def get_legend_rows(hazard_mode):
    """Return legend rows for drought or frost PDF layout."""
    if hazard_mode == "Drought (Rainfall Anomaly)":
        return [
            ["", "Below 70%", "Severe drought / high crop failure risk", colors.HexColor("#8b0000")],
            ["", "70% - 85%", "Moderate moisture deficit", colors.HexColor("#ff4500")],
            ["", "85% - 95%", "Mild water stress", colors.HexColor("#ffcc00")],
            ["", "95% - 105%", "Near normal rainfall", colors.white],
            ["", "105% - 130%", "Moderately wetter than normal", colors.HexColor("#00ccff")],
            ["", "Above 130%", "Very wet conditions", colors.HexColor("#00008b")],
        ]

    return [
        ["", "Below -2°C", "Severe highland frost risk", colors.HexColor("#0000ff")],
        ["", "-2°C to 0°C", "Active frost line detected", colors.HexColor("#00ffff")],
        ["", "0°C to 3°C", "Near-freezing thermal risk", colors.white],
        ["", "3°C to 5°C", "Stable highland thermal range", colors.HexColor("#ffaa00")],
        ["", "Above 5°C", "Low frost risk / warmer surface", colors.HexColor("#ff0000")],
    ]


def build_legend_table(hazard_mode):
    """Create a compact cartographic legend table for the PDF."""
    styles = getSampleStyleSheet()

    heading = (
        "Rainfall Anomaly Legend"
        if hazard_mode == "Drought (Rainfall Anomaly)"
        else "Night LST / Frost Legend"
    )

    rows = [[Paragraph(f"<b>{heading}</b>", styles["Normal"]), "", ""]]

    for _, threshold, meaning, swatch_color in get_legend_rows(hazard_mode):
        rows.append([
            "",
            Paragraph(f"<b>{threshold}</b>", styles["Normal"]),
            Paragraph(meaning, styles["Normal"]),
        ])

    table = Table(rows, colWidths=[22, 68, 168])

    style = [
        ("SPAN", (0, 0), (2, 0)),
        ("BACKGROUND", (0, 0), (2, 0), colors.HexColor("#e2e8f0")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#94a3b8")),
        ("INNERGRID", (0, 1), (-1, -1), 0.3, colors.HexColor("#cbd5e0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]

    for idx, row in enumerate(get_legend_rows(hazard_mode), start=1):
        swatch_color = row[3]
        style.append(("BACKGROUND", (0, idx), (0, idx), swatch_color))
        style.append(("BOX", (0, idx), (0, idx), 0.5, colors.black))

    table.setStyle(TableStyle(style))
    return table


def build_print_map(hazard_mode, opacity_val=0.90):
    """
    Build a separate print-only Folium map.
    This avoids capturing interactive controls in the PDF.
    """
    print_map = folium.Map(
        location=[-6.3, 146.5],
        zoom_start=6,
        tiles=None,
        control_scale=True,
        zoom_control=False,
        width="100%",
        height="100%",
    )

    # Cleaner print basemap than satellite hybrid.
    folium.TileLayer(
        tiles="https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
        attr="Google Terrain",
        name="Google Terrain",
        overlay=False,
        control=False,
    ).add_to(print_map)

    if hazard_mode == "Drought (Rainfall Anomaly)":
        drought_vis = {
            "min": 50,
            "max": 150,
            "palette": ["#8b0000", "#ff4500", "#ffcc00", "#ffffff", "#00ccff", "#00008b"],
        }
        add_ee_layer(
            print_map,
            st.session_state.drought_img,
            drought_vis,
            "Rainfall Anomaly",
            opacity_val=opacity_val,
            control=False,
        )
    else:
        frost_vis = {
            "min": -5,
            "max": 5,
            "palette": ["#0000ff", "#00ffff", "#ffffff", "#ffaa00", "#ff0000"],
        }
        add_ee_layer(
            print_map,
            st.session_state.frost_img,
            frost_vis,
            "Night Surface Temperature",
            opacity_val=opacity_val,
            control=False,
        )

    # Add PNG boundary outline.
    try:
        boundary_geojson = png_boundary.getInfo()
        folium.GeoJson(
            boundary_geojson,
            name="PNG Boundary",
            style_function=lambda feature: {
                "color": "#111827",
                "weight": 1.2,
                "fillOpacity": 0,
            },
        ).add_to(print_map)
    except Exception:
        pass

    # Force the map to PNG extent.
    print_map.fit_bounds([[-12.0, 141.0], [-2.0, 156.0]])
    return print_map


def capture_print_map(print_map):
    """Save clean print map to PNG using html2image."""
    html_path = os.path.abspath("temp_print_map.html")
    png_name = "map_snapshot_print.png"
    png_path = os.path.abspath(png_name)

    print_map.save(html_path)

    hti = Html2Image(output_path=os.getcwd())

    # Streamlit Cloud / Linux Chromium paths.
    if os.path.exists("/usr/bin/chromium-browser"):
        hti.browser_executable = "/usr/bin/chromium-browser"
    elif os.path.exists("/usr/bin/chromium"):
        hti.browser_executable = "/usr/bin/chromium"

    # Keep this fairly large so the PDF map stays sharp.
    url = "file:///" + html_path.replace(os.sep, "/")
    hti.screenshot(url=url, save_as=png_name, size=(1400, 850))

    return png_path


def generate_pdf_report(hazard_mode, current_coordinates=None):
    """
    Generate a clean one-page landscape PDF map report.
    The print map is separate from the live Streamlit/Folium dashboard map.
    """
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        rightMargin=24,
        leftMargin=24,
        topMargin=22,
        bottomMargin=20,
    )

    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "MapTitle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=21,
        textColor=colors.HexColor("#0f2742"),
        spaceAfter=2,
    )

    subtitle_style = ParagraphStyle(
        "MapSubtitle",
        parent=styles["Normal"],
        fontSize=8.5,
        textColor=colors.HexColor("#475569"),
        leading=10,
    )

    section_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading3"],
        fontSize=10,
        textColor=colors.HexColor("#111827"),
        spaceBefore=4,
        spaceAfter=3,
    )

    note_style = ParagraphStyle(
        "NoteText",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#334155"),
    )

    if hazard_mode == "Drought (Rainfall Anomaly)":
        map_title = "Papua New Guinea 90-Day Rainfall Anomaly Map"
        analysis_window = f"{three_months_ago.strftime('%d %b %Y')} to {safe_end_date.strftime('%d %b %Y')}"
        method_text = (
            "The drought layer sums CHIRPS daily rainfall over a lag-safe 90-day window "
            "and compares it with a 2000-2022 historical baseline for the same seasonal period. "
            "Values below 100% indicate below-normal rainfall."
        )
    else:
        map_title = "Papua New Guinea Highland Frost Risk Map"
        analysis_window = f"{one_week_ago.strftime('%d %b %Y')} to {today.strftime('%d %b %Y')}"
        method_text = (
            "The frost layer uses the rolling 7-day minimum MODIS nighttime land surface temperature, "
            "converted from Kelvin to Celsius, and masked to terrain above 2,200 meters using SRTM elevation."
        )

    # Header
    story.append(Paragraph(map_title, title_style))
    story.append(
        Paragraph(
            f"<b>Generated:</b> {today.strftime('%Y-%m-%d %H:%M')} | "
            f"<b>Analysis Window:</b> {analysis_window} | "
            f"<b>Projection:</b> WGS 84 / EPSG:4326",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 8))

    # Map image generated from a clean print-only map.
    try:
        print_map = build_print_map(hazard_mode, opacity_val=0.90)
        map_image_path = capture_print_map(print_map)
        map_img = Image(map_image_path, width=500, height=305)
    except Exception as e:
        map_img = Paragraph(f"<i>Map image could not be rendered: {str(e)}</i>", note_style)

    # Right-side cartographic panel.
    north_arrow = Paragraph(
        "<para alignment='center'><font size='24'>▲</font><br/><b>NORTH</b></para>",
        styles["Normal"],
    )

    scale_note = Paragraph(
        "<b>Scale:</b> dynamic web-map scale. For planning and screening use only.",
        note_style,
    )

    credits = Paragraph(
        "<b>Data Credits:</b><br/>"
        "Rainfall: UCSB CHIRPS Daily<br/>"
        "Temperature: NASA MODIS MOD11A1 v061<br/>"
        "Elevation: USGS SRTM GL1 30m<br/>"
        "Processing: Google Earth Engine<br/>"
        "Boundary: USDOS LSIB SIMPLE 2017",
        note_style,
    )

    developer = Paragraph(
        "<b>System Developer:</b><br/>trekky675<br/>rudoq.007@gmail.com",
        note_style,
    )

    right_panel = [
        [build_legend_table(hazard_mode)],
        [Spacer(1, 8)],
        [north_arrow],
        [Spacer(1, 8)],
        [scale_note],
        [Spacer(1, 8)],
        [credits],
        [Spacer(1, 8)],
        [developer],
    ]

    right_table = Table(right_panel, colWidths=[270])
    right_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#94a3b8")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    main_layout = Table([[map_img, right_table]], colWidths=[510, 275])
    main_layout.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (0, 0), 0.8, colors.HexColor("#334155")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    story.append(main_layout)
    story.append(Spacer(1, 8))

    # Methodology and optional clicked point.
    story.append(Paragraph("Methodology Brief", section_style))
    story.append(Paragraph(method_text, note_style))

    if current_coordinates:
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                f"<b>Selected Inquiry Point:</b> "
                f"Latitude {current_coordinates[0]:.4f}, Longitude {current_coordinates[1]:.4f}",
                note_style,
            )
        )

    story.append(Spacer(1, 5))
    story.append(
        Paragraph(
            "<b>Use Limitation:</b> This map is intended for early warning, screening, coordination, "
            "and planning support. It should be validated with local field observations before operational decisions.",
            note_style,
        )
    )

    doc.build(story)
    buffer.seek(0)

    # Clean temporary files.
    for temp_file in ["temp_print_map.html", "map_snapshot_print.png"]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

    return buffer

# -------------------------------------------------------------
# 5. SIDEBAR SETTINGS & DOWNLOAD PIPELINES
# -------------------------------------------------------------
st.sidebar.header("Control Panel")
hazard_type = st.sidebar.radio(
    "Select Active Data Layer",
    ["Drought (Rainfall Anomaly)", "Frost Risk Tracking"],
)

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Layer Visibility Settings")
layer_opacity = st.sidebar.slider(
    "Data Layer Opacity",
    min_value=0.0,
    max_value=1.0,
    value=0.85,
    step=0.05,
)

st.sidebar.markdown("---")
st.sidebar.subheader("📥 Export Active Raster Subsets")

if hazard_type == "Drought (Rainfall Anomaly)":
    try:
        drought_url = st.session_state.drought_img.getDownloadUrl(
            {
                "scale": 5000,
                "crs": "EPSG:4326",
                "region": png_boundary,
                "format": "GEO_TIFF",
            }
        )
        st.sidebar.text_input("🔗 GeoTIFF Download Link", drought_url)
    except Exception:
        st.sidebar.warning("Data link calculation pending...")
else:
    try:
        frost_url = st.session_state.frost_img.getDownloadUrl(
            {
                "scale": 1000,
                "crs": "EPSG:4326",
                "region": png_boundary,
                "format": "GEO_TIFF",
            }
        )
        st.sidebar.text_input("🔗 GeoTIFF Download Link", frost_url)
    except Exception:
        st.sidebar.warning("Data link calculation pending...")

# -------------------------------------------------------------
# 6. BUILD INTERACTIVE MAP CANVAS
# -------------------------------------------------------------
m = folium.Map(
    location=[-6.3, 146.5],
    zoom_start=6,
    control_scale=True,
    zoom_control=True,
)

folium.TileLayer(
    tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    attr="Google Maps",
    name="Google Satellite Hybrid",
    overlay=False,
    control=True,
).add_to(m)

folium.TileLayer(
    tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attr="OpenStreetMap Contributors",
    name="OpenStreetMap (Standard)",
    overlay=False,
    control=True,
).add_to(m)

folium.TileLayer(
    tiles="https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
    attr="Google Maps",
    name="Google Terrain Base",
    overlay=False,
    control=True,
).add_to(m)

m.add_child(
    MeasureControl(
        position="topleft",
        primary_length_unit="kilometers",
        primary_area_unit="hectares",
    )
)
Fullscreen(position="topleft").add_to(m)

if hazard_type == "Drought (Rainfall Anomaly)":
    st.subheader("3-Month Cumulative Rainfall Anomaly (%) Workspace")

    st.info(
        f"📅 **Data Window:** {three_months_ago.strftime('%b %d, %Y')} to "
        f"{safe_end_date.strftime('%b %d, %Y')} "
        "*(Adjusted for 15-day CHIRPS satellite publication lag)*\n\n"
        "🔬 **Methodology:** Sums daily satellite infrared rainfall estimates across PNG "
        "for the past 90 days, then compares it as a percentage against a 22-year "
        "historical mean baseline (2000-2022) for the exact same calendar months."
    )

    drought_vis = {
        "min": 50,
        "max": 150,
        "palette": ["#8b0000", "#ff4500", "#ffcc00", "#ffffff", "#00ccff", "#00008b"],
    }
    add_ee_layer(
        m,
        st.session_state.drought_img,
        drought_vis,
        "Rainfall Anomaly Layer",
        opacity_val=layer_opacity,
    )

    legend_css = """
    <div style="position: absolute; bottom: 30px; left: 30px; width: 205px;
        background-color: white; border: 2px solid #cbd5e0; z-index: 1000;
        font-size: 11px; padding: 8px; border-radius: 4px; font-family: sans-serif;
        opacity: 0.95; line-height: 1.35;">
        <b>Rainfall Anomaly (%)</b><br>
        <i style="background:#8b0000; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Severe Drought (&lt;70%)<br>
        <i style="background:#ff4500; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Moderate Dry (70-85%)<br>
        <i style="background:#ffcc00; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Mild Deficit (85-95%)<br>
        <i style="background:#ffffff; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px; border:1px solid #ccc;"></i> Normal (95-105%)<br>
        <i style="background:#00ccff; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Moderately Wet<br>
        <i style="background:#00008b; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Very Wet (&gt;130%)<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_css))

else:
    st.subheader("Highland Frost Hazard Core Workspace (Active Detection)")

    st.info(
        f"📅 **Data Window:** {one_week_ago.strftime('%b %d, %Y')} to "
        f"{today.strftime('%b %d, %Y')} "
        "*(Near-real-time rolling 7-day minimum composite)*\n\n"
        "🔬 **Methodology:** Extracts the absolute lowest nighttime land surface temperatures "
        "observed by NASA MODIS satellites over the past week. To highlight high-altitude "
        "food crop exposure, SRTM elevation is used to mask out terrain beneath 2,200 meters."
    )

    frost_vis = {
        "min": -5,
        "max": 5,
        "palette": ["#0000ff", "#00ffff", "#ffffff", "#ffaa00", "#ff0000"],
    }
    add_ee_layer(
        m,
        st.session_state.frost_img,
        frost_vis,
        "Night Surface Temperature (°C)",
        opacity_val=layer_opacity,
    )

    legend_css = """
    <div style="position: absolute; bottom: 30px; left: 30px; width: 215px;
        background-color: white; border: 2px solid #cbd5e0; z-index: 1000;
        font-size: 11px; padding: 8px; border-radius: 4px; font-family: sans-serif;
        opacity: 0.95; line-height: 1.35;">
        <b>Night Surface Temp (°C)</b><br>
        <small>Highland terrain &gt;2200m</small><br>
        <i style="background:#0000ff; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Severe Frost (&lt; -2°C)<br>
        <i style="background:#00ffff; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Active Frost (-2 to 0°C)<br>
        <i style="background:#ffffff; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px; border:1px solid #ccc;"></i> Near Freezing (0-3°C)<br>
        <i style="background:#ffaa00; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Stable Range (3-5°C)<br>
        <i style="background:#ff0000; width:12px; height:10px; float:left; margin-right:6px; margin-top:3px;"></i> Warm Baseline (&gt;5°C)<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_css))

MousePosition(position="bottomright", separator=" | ", prefix="Coords: ").add_to(m)
folium.LayerControl(position="topright", collapsed=False).add_to(m)

# Render map interface.
output = st_folium(m, width=1100, height=600)

# -------------------------------------------------------------
# 7. EXPORT INTERFACE LAYER LOGIC
# -------------------------------------------------------------
clicked_coords = None
if output and output.get("last_clicked"):
    clicked_coords = (output["last_clicked"]["lat"], output["last_clicked"]["lng"])

    st.markdown("---")
    st.subheader("🔍 Selected Coordinate Inquiry Report")
    col1, col2 = st.columns(2)
    col1.metric("Target Latitude", f"{clicked_coords[0]:.4f}° N/S")
    col2.metric("Target Longitude", f"{clicked_coords[1]:.4f}° E/W")

    with st.spinner("Querying exact remote sensing pixel value at pinpoint..."):
        point_geom = ee.Geometry.Point([clicked_coords[1], clicked_coords[0]])

        if hazard_type == "Drought (Rainfall Anomaly)":
            pixel_val = (
                st.session_state.drought_img.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=point_geom,
                    scale=5000,
                )
                .get("rainfall_anomaly_pct")
                .getInfo()
            )
            if pixel_val is not None:
                st.info(
                    f"📊 **Rainfall Status:** This point received **{pixel_val:.1f}%** "
                    "of its historical normal rainfall profile over the past 90 days."
                )
            else:
                st.warning("Selected point falls outside current clipped terrestrial dataset parameters.")
        else:
            pixel_val = (
                st.session_state.frost_img.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=point_geom,
                    scale=1000,
                )
                .get("night_lst_celsius")
                .getInfo()
            )
            if pixel_val is not None:
                status = "❄️ CRITICAL FROST DETECTED" if pixel_val <= 0 else "☀️ Normal Temperature Range"
                st.info(
                    f"🌡️ **Surface Temperature Profile:** Observed minimum temperature at this site "
                    f"sits at **{pixel_val:.2f}°C** ({status})."
                )
            else:
                st.warning("Selected location sits outside the active 2,200-meter frost altitude mask.")

# Add PDF generation trigger directly beneath the active analytics reporting panel.
st.sidebar.markdown("---")
st.sidebar.subheader("📄 Map Layout & Reporting")
if st.sidebar.button("Generate Layout Report (PDF)"):
    with st.spinner("Compiling clean cartographic print layout..."):
        pdf_data = generate_pdf_report(hazard_type, clicked_coords)
        st.sidebar.download_button(
            label="💾 Download PDF Map Report",
            data=pdf_data,
            file_name=f"PNG_Climate_Report_{today.strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
        )

# -------------------------------------------------------------
# 8. APPLICATION FOOTER & ATTRIBUTIONS
# -------------------------------------------------------------
st.markdown("---")
footer_col1, footer_col2 = st.columns([3, 1])

with footer_col1:
    st.caption(
        "📊 **Data Credits & Attributions:**\n"
        "* **Precipitation Metrics:** Sourced via University of California Santa Barbara (UCSB) CHIRPS Daily v2.0 Image Infrastructure.\n"
        "* **Thermal Land Surface Profiles:** Extracted via NASA MODIS (MOD11A1 v061) Daily Nighttime 1km Grids.\n"
        "* **Topographical Baseline Modeling:** Constrained via USGS Shuttle Radar Topography Mission (SRTM GL1 30m) Elevation Datasets.\n"
        "* **Administrative Boundary:** USDOS LSIB SIMPLE 2017 country boundary for Papua New Guinea."
    )

with footer_col2:
    st.markdown(
        "<div style='text-align: right; padding-top: 10px; font-size: 13px; "
        "font-family: sans-serif; color: #718096;'>"
        "Developed by: <a href='mailto:rudoq.007@gmail.com' "
        "style='color: #3182ce; font-weight: bold; text-decoration: none;'>trekky675</a>"
        "</div>",
        unsafe_allow_html=True,
    )
