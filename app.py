import io
import json
from datetime import datetime, timedelta

import ee
import folium
import streamlit as st
from folium.plugins import Fullscreen, MeasureControl, MousePosition
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from streamlit_folium import st_folium

# -----------------------------------------------------------------------------
# PAGE CONFIG
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="PNG Live Processing Workspace",
    layout="wide",
    initial_sidebar_state="expanded",
)

SERVICE_ACCOUNT_EMAIL = "png-el-nin-dashboard-eb9933c44@trekky675.iam.gserviceaccount.com"
DEFAULT_PROJECT = "trekky675"

# -----------------------------------------------------------------------------
# PREMIUM UI STYLING AND DARK-MODE-SAFE LEGENDS
# -----------------------------------------------------------------------------
st.markdown(
    """
    <style>
      .stApp {
        background: radial-gradient(circle at top left, #eef8f3 0, #f5f8f7 38%, #f8fafc 100%);
        color: #111827;
      }
      div[data-testid="stMarkdownContainer"], div[data-testid="stText"], label, p, span {
        color: #111827 !important;
      }
      .hero {
        background: linear-gradient(135deg, #0f3d2e 0%, #17694f 62%, #d97706 180%);
        color: #ffffff !important;
        border-radius: 28px;
        padding: 30px 34px;
        margin-bottom: 18px;
        box-shadow: 0 22px 55px rgba(16,24,40,0.20);
        position: relative;
        overflow: hidden;
      }
      .hero * { color: #ffffff !important; }
      .hero:after {
        content: "";
        position: absolute;
        width: 360px;
        height: 360px;
        border-radius: 999px;
        right: -130px;
        top: -150px;
        background: rgba(255,255,255,0.10);
      }
      .hero h1 {
        font-size: 42px;
        line-height: 1.05;
        letter-spacing: -0.045em;
        margin: 8px 0 8px 0;
      }
      .eyebrow {
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-weight: 800;
        font-size: 12px;
        color: #d9f99d !important;
      }
      .hero-sub {
        max-width: 1020px;
        line-height: 1.55;
        font-size: 16px;
        color: #ecfdf5 !important;
      }
      .badge-row {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 16px;
      }
      .badge {
        border: 1px solid rgba(255,255,255,0.24);
        background: rgba(255,255,255,0.13);
        border-radius: 999px;
        padding: 8px 12px;
        font-weight: 800;
        font-size: 12px;
      }
      .premium-card {
        background: rgba(255,255,255,0.94);
        border: 1px solid rgba(15,61,46,0.10);
        border-radius: 22px;
        padding: 18px 20px;
        box-shadow: 0 10px 28px rgba(16,24,40,0.08);
      }
      .method-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 14px;
        margin: 14px 0 18px;
      }
      .method-card {
        background: #ffffff;
        border: 1px solid #d8e0df;
        border-radius: 18px;
        padding: 14px;
        box-shadow: 0 6px 18px rgba(16,24,40,0.06);
      }
      .method-card b { display: block; margin-bottom: 6px; color: #0f3d2e !important; }
      .small-note { font-size: 13px; color: #667085 !important; line-height: 1.45; }
      .soft-alert {
        background: #fff7ed;
        border-left: 5px solid #d97706;
        border-radius: 16px;
        padding: 13px 15px;
        margin: 12px 0;
      }
      .success-strip {
        background: #ecfdf5;
        border-left: 5px solid #15803d;
        border-radius: 16px;
        padding: 13px 15px;
        margin: 12px 0;
      }
      .legend-box {
        background: rgba(255, 255, 255, 0.96) !important;
        color: #111827 !important;
        border: 1px solid #d1d5db !important;
        border-radius: 12px !important;
        padding: 10px 12px !important;
        font-size: 12px !important;
        line-height: 1.45 !important;
        box-shadow: 0 8px 24px rgba(16,24,40,0.18) !important;
      }
      .legend-box * { color: #111827 !important; }
      iframe { border-radius: 18px !important; }
      @media(max-width: 1000px) { .method-grid { grid-template-columns: 1fr; } .hero h1 { font-size: 30px; } }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# EARTH ENGINE AUTHENTICATION
# -----------------------------------------------------------------------------
def initialise_earth_engine():
    """Initialise Earth Engine using service account secrets, without exposing technical errors to users."""
    try:
        if "EARTHENGINE_SERVICE_ACCOUNT_JSON" in st.secrets:
            raw_secret = st.secrets["EARTHENGINE_SERVICE_ACCOUNT_JSON"]
            service_account_info = json.loads(raw_secret) if isinstance(raw_secret, str) else dict(raw_secret)
        elif "EARTHENGINE_SERVICE_ACCOUNT" in st.secrets:
            service_account_info = dict(st.secrets["EARTHENGINE_SERVICE_ACCOUNT"])
            if "private_key" in service_account_info:
                service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")
        else:
            return False, "missing_secret"

        credentials = ee.ServiceAccountCredentials(
            service_account_info["client_email"],
            key_data=json.dumps(service_account_info),
        )
        ee.Initialize(credentials, project=service_account_info.get("project_id", DEFAULT_PROJECT))
        return True, service_account_info.get("client_email", SERVICE_ACCOUNT_EMAIL)
    except Exception:
        return False, "permission_or_secret_issue"


# -----------------------------------------------------------------------------
# EARTH ENGINE LAYER HELPERS
# -----------------------------------------------------------------------------
def png_geometry():
    return (
        ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        .filter(ee.Filter.eq("country_na", "Papua New Guinea"))
        .geometry()
    )


@st.cache_resource(show_spinner=False)
def build_drought_layer(start_date: str, end_date: str):
    boundary = png_geometry()
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days = max((end - start).days, 1)

    current_rain = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterDate(start_date, end_date)
        .sum()
        .clip(boundary)
    )
    baseline_rain = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filter(ee.Filter.calendarRange(start.month, end.month, "month"))
        .filterDate("2000-01-01", "2022-12-31")
        .mean()
        .multiply(days)
        .clip(boundary)
    )
    return current_rain.divide(baseline_rain).multiply(100).rename("rainfall_pct_normal")


@st.cache_resource(show_spinner=False)
def build_frost_layer(start_date: str, end_date: str):
    boundary = png_geometry()
    elevation = ee.Image("USGS/SRTMGL1_003").select("elevation").clip(boundary)
    highland_mask = elevation.gt(2200)
    night_lst = (
        ee.ImageCollection("MODIS/061/MOD11A1")
        .filterDate(start_date, end_date)
        .select("LST_Night_1km")
        .min()
        .clip(boundary)
    )
    return night_lst.multiply(0.02).subtract(273.15).rename("night_lst_celsius").updateMask(highland_mask)


def add_ee_layer(fmap, image, vis_params, name, opacity=0.85):
    map_id = ee.Image(image).getMapId(vis_params)
    folium.raster_layers.TileLayer(
        tiles=map_id["tile_fetcher"].url_format,
        attr="Google Earth Engine",
        name=name,
        overlay=True,
        control=True,
        opacity=opacity,
    ).add_to(fmap)


def add_legend(fmap, title, rows):
    legend_html = [
        "<div class='legend-box' style='position: fixed; bottom: 28px; left: 28px; z-index: 9999;'>",
        f"<b>{title}</b><br>",
    ]
    for colour, label in rows:
        legend_html.append(
            f"<span style='display:inline-block;width:14px;height:14px;background:{colour};"
            "border:1px solid #374151;margin-right:6px;vertical-align:middle;'></span>"
            f"{label}<br>"
        )
    legend_html.append("</div>")
    fmap.get_root().html.add_child(folium.Element("".join(legend_html)))


def make_pdf_report(layer_name, drought_period, frost_period, methodology_text):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=34,
        leftMargin=34,
        topMargin=30,
        bottomMargin=30,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCustom",
        parent=styles["Title"],
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#0f3d2e"),
        spaceAfter=12,
    )
    body_style = ParagraphStyle("BodyCustom", parent=styles["BodyText"], fontSize=10, leading=14)
    small_style = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=8, leading=11, textColor=colors.HexColor("#667085"))

    story = [
        Paragraph("PNG Live Processing Workspace: Map and Methodology Report", title_style),
        Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", small_style),
        Spacer(1, 10),
        Table(
            [
                ["Selected layer", layer_name],
                ["Drought data period", drought_period],
                ["Frost screening period", frost_period],
                ["Purpose", "Technical review, map inspection, export preparation, and field verification planning."],
            ],
            colWidths=[150, 570],
            style=[
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#ecfdf5")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0f3d2e")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#94a3b8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ],
        ),
        Spacer(1, 12),
        Paragraph("Methodology", styles["Heading2"]),
        Paragraph(methodology_text, body_style),
        Spacer(1, 10),
        Paragraph("Interpretation note", styles["Heading2"]),
        Paragraph(
            "These layers are screening outputs for technical review and field verification planning. They do not represent official impact declarations and should be cross-checked with provincial and district field reports.",
            body_style,
        ),
        Spacer(1, 12),
        Paragraph("Legend summary", styles["Heading2"]),
        Table(
            [
                ["Drought", "Below-normal rainfall values indicate rainfall deficit compared with the selected historical baseline."],
                ["Frost", "Low nighttime land surface temperature in highland zones indicates possible frost-prone conditions."],
                ["Opacity", "Opacity only affects display transparency; it does not change the underlying analysis values."],
            ],
            colWidths=[110, 610],
            style=[
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#fef3c7")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d1d5db")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ],
        ),
    ]
    doc.build(story)
    buffer.seek(0)
    return buffer


# -----------------------------------------------------------------------------
# HERO AND METHODOLOGY HEADER
# -----------------------------------------------------------------------------
st.markdown(
    """
    <div class="hero">
      <div class="eyebrow">FAO PNG climate-risk technical workspace</div>
      <h1>PNG Live Processing Workspace</h1>
      <div class="hero-sub">
        Separate Streamlit workspace for live Google Earth Engine layer review, rainfall and frost screening,
        map inspection, export preparation, and technical reporting. The public dashboard remains the briefing layer;
        EarthMap remains the broader FAO geospatial exploration platform.
      </div>
      <div class="badge-row">
        <span class="badge">GEE processing</span>
        <span class="badge">Drought + frost screen</span>
        <span class="badge">Methodology visible</span>
        <span class="badge">PDF report export</span>
        <span class="badge">Dark-mode safe legend</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

utc_today = datetime.utcnow().date()
safe_end = utc_today - timedelta(days=15)
start_90 = safe_end - timedelta(days=90)
start_7 = utc_today - timedelta(days=7)

drought_period = f"{start_90} to {safe_end}"
frost_period = f"{start_7} to {utc_today}"
methodology_text = (
    "Drought screening uses CHIRPS daily rainfall accumulated over a recent lag-safe window and compares it with a 2000-2022 same-month baseline. "
    "Frost screening uses MODIS Terra nighttime land surface temperature converted to Celsius and masked to highland areas above 2,200 m using SRTM elevation. "
    "The workspace is intended for technical review, map inspection, export preparation, and field verification planning."
)

st.markdown(
    f"""
    <div class="method-grid">
      <div class="method-card"><b>Drought data period</b><span class="small-note">CHIRPS rainfall window: {drought_period}</span></div>
      <div class="method-card"><b>Frost data period</b><span class="small-note">MODIS night LST window: {frost_period}</span></div>
      <div class="method-card"><b>Baseline</b><span class="small-note">Rainfall baseline: 2000-2022 same-month climatology.</span></div>
      <div class="method-card"><b>Use</b><span class="small-note">Screening and prioritisation only; verify with field information.</span></div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("Methodology and interpretation note", expanded=True):
    st.markdown(
        """
        **Drought layer:** CHIRPS daily rainfall is accumulated over the selected recent lag-safe period and expressed as a percentage of normal rainfall for the same months. Lower percentages indicate rainfall deficit.

        **Frost layer:** MODIS nighttime land surface temperature is converted to Celsius and masked to highland areas above 2,200 m using SRTM elevation. Lower values indicate areas that may require frost-related follow-up.

        **Important:** These are screening layers for technical review and field verification planning. They should be compared with crop condition reports, water availability, local weather observations, and provincial or district assessment information.
        """
    )

# -----------------------------------------------------------------------------
# AUTH STATUS
# -----------------------------------------------------------------------------
ee_ready, ee_message = initialise_earth_engine()
if ee_ready:
    st.markdown(
        f"<div class='success-strip'><b>Earth Engine status:</b> Connected using service account.</div>",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div class="soft-alert">
          <b>Workspace setup pending:</b> The live processing layers are not available yet because the Earth Engine service account still needs final permission setup in Google Cloud / Streamlit Secrets.
          This message is intentionally simplified for viewers. Technical authentication details are not displayed on the public page.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

try:
    png_test_count = (
        ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        .filter(ee.Filter.eq("country_na", "Papua New Guinea"))
        .size()
        .getInfo()
    )
except Exception:
    st.markdown(
        """
        <div class="soft-alert">
          <b>Workspace setup pending:</b> Earth Engine connected, but the test query could not run. Please check the service account project permissions and private asset access.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# -----------------------------------------------------------------------------
# SIDEBAR CONTROLS AND PDF EXPORT
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("Control panel")
    mode = st.radio(
        "Select active data layer",
        ["Drought: rainfall percentage of normal", "Frost: nighttime land surface temperature", "Both layers"],
    )
    opacity = st.slider("Data layer opacity", 0.10, 1.00, 0.85, 0.05)
    st.markdown("---")
    st.subheader("Map report export")
    pdf_buffer = make_pdf_report(mode, drought_period, frost_period, methodology_text)
    st.download_button(
        "Download PDF map/report",
        data=pdf_buffer,
        file_name=f"PNG_Live_Processing_Workspace_Report_{utc_today}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
    st.caption("The PDF includes the selected layer, data periods, methodology note, interpretation guidance, and legend summary.")

# -----------------------------------------------------------------------------
# MAP
# -----------------------------------------------------------------------------
st.subheader("Live GEE map review")
st.caption(
    f"Active dates: drought rainfall window {drought_period}; frost screening window {frost_period}."
)

rainfall_vis = {
    "min": 50,
    "max": 150,
    "palette": ["#8b0000", "#ff4500", "#ffcc00", "#ffffff", "#00ccff", "#00008b"],
}
frost_vis = {
    "min": -5,
    "max": 5,
    "palette": ["#0000ff", "#00ffff", "#ffffff", "#ffaa00", "#ff0000"],
}

m = folium.Map(location=[-6.3, 146.5], zoom_start=6, tiles="CartoDB positron", control_scale=True)
Fullscreen().add_to(m)
MeasureControl(primary_length_unit="kilometers").add_to(m)
MousePosition(position="bottomright", separator=" | ", prefix="Lat/Lon:").add_to(m)

if mode in ["Drought: rainfall percentage of normal", "Both layers"]:
    rain_img = build_drought_layer(str(start_90), str(safe_end))
    add_ee_layer(m, rain_img, rainfall_vis, "CHIRPS rainfall % of normal", opacity)
    add_legend(
        m,
        "Rainfall % of Normal",
        [
            ("#8b0000", "Below 70%: severe deficit"),
            ("#ff4500", "70-85%: moderate deficit"),
            ("#ffcc00", "85-95%: mild stress"),
            ("#ffffff", "95-105%: near normal"),
            ("#00ccff", "105-130%: wetter"),
            ("#00008b", "Above 130%: very wet"),
        ],
    )

if mode in ["Frost: nighttime land surface temperature", "Both layers"]:
    frost_img = build_frost_layer(str(start_7), str(utc_today))
    add_ee_layer(m, frost_img, frost_vis, "MODIS night LST highland frost screen", opacity)
    if mode == "Frost: nighttime land surface temperature":
        add_legend(
            m,
            "Night LST / Frost Screen",
            [
                ("#0000ff", "Below -2°C: severe frost signal"),
                ("#00ffff", "-2°C to 0°C: active frost line"),
                ("#ffffff", "0°C to 3°C: near-freezing"),
                ("#ffaa00", "3°C to 5°C: stable highland range"),
                ("#ff0000", "Above 5°C: warmer surface"),
            ],
        )

folium.LayerControl(collapsed=False).add_to(m)
st_folium(m, width=None, height=720)

st.markdown(
    """
    <div class="premium-card">
      <b>Operational note:</b> Use this workspace to review live layer behaviour, adjust display opacity, inspect spatial patterns, and export a technical map/report for discussion. Final response decisions should be supported by field verification and official assessment channels.
    </div>
    """,
    unsafe_allow_html=True,
)
