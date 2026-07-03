import io
import json
import os
from datetime import datetime, timedelta

import ee
import folium
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen, MeasureControl, MousePosition
from html2image import Html2Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table
from streamlit_folium import st_folium

st.set_page_config(page_title="PNG Live Processing Workspace", layout="wide", initial_sidebar_state="expanded")

DEFAULT_PROJECT = "trekky675"
PRINT_MAP_WIDTH = 1400
PRINT_MAP_HEIGHT = 900

st.markdown(
    """
    <style>
      .stApp {background: radial-gradient(circle at top left,#eef8f3 0,#f5f8f7 38%,#f8fafc 100%); color:#111827;}
      div[data-testid="stMarkdownContainer"], div[data-testid="stText"], label, p, span {color:#111827!important;}
      .hero {background:linear-gradient(135deg,#0f3d2e 0%,#17694f 62%,#d97706 180%); color:#fff!important; border-radius:28px; padding:30px 34px; margin-bottom:18px; box-shadow:0 22px 55px rgba(16,24,40,.20); position:relative; overflow:hidden;}
      .hero * {color:#fff!important;}
      .hero:after {content:""; position:absolute; width:360px; height:360px; border-radius:999px; right:-130px; top:-150px; background:rgba(255,255,255,.10);}
      .hero h1 {font-size:42px; line-height:1.05; letter-spacing:-.045em; margin:8px 0;}
      .eyebrow {text-transform:uppercase; letter-spacing:.14em; font-weight:800; font-size:12px; color:#d9f99d!important;}
      .hero-sub {max-width:1020px; line-height:1.55; font-size:16px; color:#ecfdf5!important;}
      .premium-card {background:rgba(255,255,255,.94); border:1px solid rgba(15,61,46,.10); border-radius:22px; padding:18px 20px; box-shadow:0 10px 28px rgba(16,24,40,.08);}
      .method-grid {display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:14px 0 18px;}
      .method-card {background:#fff; border:1px solid #d8e0df; border-radius:18px; padding:14px; box-shadow:0 6px 18px rgba(16,24,40,.06);}
      .method-card b {display:block; margin-bottom:6px; color:#0f3d2e!important;}
      .small-note {font-size:13px; color:#667085!important; line-height:1.45;}
      .soft-alert {background:#fff7ed; border-left:5px solid #d97706; border-radius:16px; padding:13px 15px; margin:12px 0;}
      .success-strip {background:#ecfdf5; border-left:5px solid #15803d; border-radius:16px; padding:13px 15px; margin:12px 0;}
      .legend-box {background:rgba(255,255,255,.96)!important; color:#111827!important; border:1px solid #d1d5db!important; border-radius:12px!important; padding:10px 12px!important; font-size:12px!important; line-height:1.45!important; box-shadow:0 8px 24px rgba(16,24,40,.18)!important;}
      .legend-box * {color:#111827!important;}
      iframe {border-radius:18px!important;}
      @media(max-width:1000px){.method-grid{grid-template-columns:1fr}.hero h1{font-size:30px}}
    </style>
    """,
    unsafe_allow_html=True,
)


def initialise_earth_engine():
    try:
        if "EARTHENGINE_SERVICE_ACCOUNT_JSON" in st.secrets:
            raw_secret = st.secrets["EARTHENGINE_SERVICE_ACCOUNT_JSON"]
            service_account_info = json.loads(raw_secret) if isinstance(raw_secret, str) else dict(raw_secret)
        elif "EARTHENGINE_SERVICE_ACCOUNT" in st.secrets:
            service_account_info = dict(st.secrets["EARTHENGINE_SERVICE_ACCOUNT"])
            if "private_key" in service_account_info:
                service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")
        else:
            return False
        credentials = ee.ServiceAccountCredentials(service_account_info["client_email"], key_data=json.dumps(service_account_info))
        ee.Initialize(credentials, project=service_account_info.get("project_id", DEFAULT_PROJECT))
        return True
    except Exception:
        return False


def png_geometry():
    return ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017").filter(ee.Filter.eq("country_na", "Papua New Guinea")).geometry()


def province_collection():
    return ee.FeatureCollection("FAO/GAUL/2015/level1").filter(ee.Filter.eq("ADM0_NAME", "Papua New Guinea"))


@st.cache_data(show_spinner=False)
def get_province_geojson():
    return province_collection().getInfo()


@st.cache_resource(show_spinner=False)
def build_drought_layer(start_date: str, end_date: str):
    boundary = png_geometry()
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days = max((end - start).days, 1)
    current_rain = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate(start_date, end_date).sum().clip(boundary)
    baseline_rain = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filter(ee.Filter.calendarRange(start.month, end.month, "month")).filterDate("2000-01-01", "2022-12-31").mean().multiply(days).clip(boundary)
    return current_rain.divide(baseline_rain).multiply(100).rename("rainfall_pct_normal")


@st.cache_resource(show_spinner=False)
def build_frost_layer(start_date: str, end_date: str):
    boundary = png_geometry()
    elevation = ee.Image("USGS/SRTMGL1_003").select("elevation").clip(boundary)
    highland_mask = elevation.gt(2200)
    night_lst = ee.ImageCollection("MODIS/061/MOD11A1").filterDate(start_date, end_date).select("LST_Night_1km").min().clip(boundary)
    return night_lst.multiply(0.02).subtract(273.15).rename("night_lst_celsius").updateMask(highland_mask)


def add_ee_layer(fmap, image, vis_params, name, opacity=0.85):
    map_id = ee.Image(image).getMapId(vis_params)
    folium.raster_layers.TileLayer(tiles=map_id["tile_fetcher"].url_format, attr="Google Earth Engine", name=name, overlay=True, control=True, opacity=opacity).add_to(fmap)


def add_legend(fmap, title, rows):
    legend_html = ["<div class='legend-box' style='position: fixed; bottom: 28px; left: 28px; z-index: 9999;'>", f"<b>{title}</b><br>"]
    for colour, label in rows:
        legend_html.append(f"<span style='display:inline-block;width:14px;height:14px;background:{colour};border:1px solid #374151;margin-right:6px;vertical-align:middle;'></span>{label}<br>")
    legend_html.append("</div>")
    fmap.get_root().html.add_child(folium.Element("".join(legend_html)))


def add_province_boundaries(fmap):
    try:
        folium.GeoJson(
            get_province_geojson(),
            name="Provincial boundaries",
            style_function=lambda feature: {"color": "#111827", "weight": 1.2, "fillOpacity": 0.0, "opacity": 0.95},
            highlight_function=lambda feature: {"color": "#facc15", "weight": 2.5, "fillOpacity": 0.04},
            tooltip=folium.GeoJsonTooltip(fields=["ADM1_NAME"], aliases=["Province"], sticky=True, labels=True, style="background:white;color:#111827;font-size:12px;padding:4px;"),
            control=True,
        ).add_to(fmap)
    except Exception:
        pass


def add_standard_basemaps(fmap, for_print=False):
    if for_print:
        folium.TileLayer("OpenStreetMap", name="OpenStreetMap", overlay=False, control=True, show=True).add_to(fmap)
        return
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", overlay=False, control=True, show=False).add_to(fmap)
    folium.TileLayer("CartoDB positron", name="CartoDB Light", overlay=False, control=True, show=True).add_to(fmap)
    folium.TileLayer("CartoDB dark_matter", name="CartoDB Dark", overlay=False, control=True, show=False).add_to(fmap)


def build_map(mode, rain_img, frost_img, rainfall_vis, frost_vis, opacity, for_print=False):
    fmap = folium.Map(location=[-6.3, 146.5], zoom_start=6, tiles=None, control_scale=True, width=f"{PRINT_MAP_WIDTH}px" if for_print else "100%", height=f"{PRINT_MAP_HEIGHT}px" if for_print else "100%")
    add_standard_basemaps(fmap, for_print=for_print)
    if not for_print:
        Fullscreen().add_to(fmap)
        MeasureControl(primary_length_unit="kilometers").add_to(fmap)
        MousePosition(position="bottomright", separator=" | ", prefix="Lat/Lon:").add_to(fmap)
    if mode in ["Drought: rainfall percentage of normal", "Both layers"]:
        add_ee_layer(fmap, rain_img, rainfall_vis, "CHIRPS rainfall % of normal", opacity)
        add_legend(fmap, "Rainfall % of Normal", [("#8b0000", "Below 70%: severe deficit"), ("#ff4500", "70-85%: moderate deficit"), ("#ffcc00", "85-95%: mild stress"), ("#ffffff", "95-105%: near normal"), ("#00ccff", "105-130%: wetter"), ("#00008b", "Above 130%: very wet")])
    if mode in ["Frost: nighttime land surface temperature", "Both layers"]:
        add_ee_layer(fmap, frost_img, frost_vis, "MODIS night LST highland frost screen", opacity)
        if mode == "Frost: nighttime land surface temperature":
            add_legend(fmap, "Night LST / Frost Screen", [("#0000ff", "Below -2°C: severe frost signal"), ("#00ffff", "-2°C to 0°C: active frost line"), ("#ffffff", "0°C to 3°C: near-freezing"), ("#ffaa00", "3°C to 5°C: stable highland range"), ("#ff0000", "Above 5°C: warmer surface")])
    add_province_boundaries(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap


def capture_map_png(fmap):
    html_path = os.path.abspath("temp_print_map.html")
    png_name = "map_snapshot_print.png"
    png_path = os.path.abspath(png_name)
    fmap.save(html_path)
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    force_size_css = f"""<style>html, body {{margin:0!important;padding:0!important;width:{PRINT_MAP_WIDTH}px!important;height:{PRINT_MAP_HEIGHT}px!important;overflow:hidden!important;background:white!important;}} .folium-map, .leaflet-container {{width:{PRINT_MAP_WIDTH}px!important;height:{PRINT_MAP_HEIGHT}px!important;min-height:{PRINT_MAP_HEIGHT}px!important;}}</style>"""
    html = html.replace("</head>", force_size_css + "\n</head>") if "</head>" in html else force_size_css + html
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    hti = Html2Image(output_path=os.getcwd())
    if os.path.exists("/usr/bin/chromium-browser"):
        hti.browser_executable = "/usr/bin/chromium-browser"
    elif os.path.exists("/usr/bin/chromium"):
        hti.browser_executable = "/usr/bin/chromium"
    hti.screenshot(url="file:///" + html_path.replace(os.sep, "/"), save_as=png_name, size=(PRINT_MAP_WIDTH, PRINT_MAP_HEIGHT))
    return png_path


def make_export_image(mode, rain_img, frost_img):
    if mode == "Drought: rainfall percentage of normal":
        return rain_img.rename("rainfall_pct_normal"), "PNG_CHIRPS_rainfall_pct_normal", "GEO_TIFF"
    if mode == "Frost: nighttime land surface temperature":
        return frost_img.rename("night_lst_celsius"), "PNG_MODIS_night_LST_frost_screen", "GEO_TIFF"
    combined = rain_img.rename("rainfall_pct_normal").addBands(frost_img.rename("night_lst_celsius"))
    return combined, "PNG_combined_rainfall_frost_layers", "ZIPPED_GEO_TIFF"


def get_geotiff_url(export_image, export_name, export_format, export_scale):
    return export_image.getDownloadURL({"name": export_name, "scale": export_scale, "region": png_geometry(), "filePerBand": False, "format": export_format})


def classify_drought(value):
    if pd.isna(value):
        return "No data"
    if value < 70:
        return "Severe deficit"
    if value < 85:
        return "Moderate deficit"
    if value < 95:
        return "Mild stress"
    if value <= 105:
        return "Near normal"
    return "Wetter than normal"


def classify_frost(value):
    if pd.isna(value):
        return "No highland signal"
    if value <= -2:
        return "Severe frost signal"
    if value <= 0:
        return "Active frost line"
    if value <= 3:
        return "Near-freezing"
    return "Warmer / lower frost signal"


@st.cache_data(show_spinner=False)
def provincial_summary(drought_start, drought_end, frost_start, frost_end):
    rain = build_drought_layer(drought_start, drought_end)
    frost = build_frost_layer(frost_start, frost_end)
    combined = rain.addBands(frost)
    fc = combined.reduceRegions(collection=province_collection(), reducer=ee.Reducer.mean(), scale=5000, tileScale=4).getInfo()
    rows = []
    for feature in fc.get("features", []):
        props = feature.get("properties", {})
        rain_val = props.get("rainfall_pct_normal")
        frost_val = props.get("night_lst_celsius")
        rows.append({
            "Province": props.get("ADM1_NAME", "Unknown"),
            "Rainfall % normal": round(rain_val, 1) if rain_val is not None else None,
            "Drought interpretation": classify_drought(rain_val),
            "Mean night LST °C": round(frost_val, 1) if frost_val is not None else None,
            "Frost interpretation": classify_frost(frost_val),
        })
    return pd.DataFrame(rows).sort_values("Province")


def make_table_summary(df):
    if df.empty:
        return "No provincial summary values were returned for the selected period."
    severe_or_moderate = int(df["Drought interpretation"].isin(["Severe deficit", "Moderate deficit"]).sum())
    frost_watch = int(df["Frost interpretation"].isin(["Severe frost signal", "Active frost line", "Near-freezing"]).sum())
    driest = df.dropna(subset=["Rainfall % normal"]).sort_values("Rainfall % normal").head(3)["Province"].tolist()
    coldest = df.dropna(subset=["Mean night LST °C"]).sort_values("Mean night LST °C").head(3)["Province"].tolist()
    return f"For the selected period, {severe_or_moderate} provinces fall in moderate or severe rainfall deficit classes, while {frost_watch} provinces show a highland cold-temperature signal. Lowest rainfall percentage of normal: {', '.join(driest) if driest else 'no data'}. Coldest highland nighttime LST signal: {', '.join(coldest) if coldest else 'no data'}."


def make_pdf_report(layer_name, drought_period, frost_period, methodology_text, map_image_path=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=28, leftMargin=28, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=20, leading=24, textColor=colors.HexColor("#0f3d2e"), spaceAfter=8)
    body_style = ParagraphStyle("BodyCustom", parent=styles["BodyText"], fontSize=8.8, leading=11)
    small_style = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=7.6, leading=10, textColor=colors.HexColor("#667085"))
    story = [Paragraph("PNG Live Processing Workspace: Map and Methodology Report", title_style), Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", small_style), Spacer(1, 6)]
    story.append(Table([["Selected layer", layer_name], ["Drought data period", drought_period], ["Frost screening period", frost_period], ["Purpose", "Technical review, map inspection, export preparation, and field verification planning."]], colWidths=[145, 575], style=[("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#ecfdf5")), ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0f3d2e")), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#94a3b8")), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Map output", styles["Heading2"]))
    if map_image_path and os.path.exists(map_image_path):
        story.append(Image(map_image_path, width=540, height=347))
    else:
        story.append(Paragraph("Map image could not be embedded during this export. Use the live map or GeoTIFF export for spatial review.", body_style))
    story.extend([Spacer(1, 6), Paragraph("Methodology", styles["Heading2"]), Paragraph(methodology_text, body_style), Spacer(1, 6), Paragraph("Interpretation note", styles["Heading2"]), Paragraph("These layers are screening outputs for technical review and field verification planning. They do not represent official impact declarations and should be cross-checked with provincial and district field reports.", body_style)])
    doc.build(story)
    buffer.seek(0)
    return buffer


st.markdown("""
<div class="hero">
  <div class="eyebrow">FAO PNG climate-risk technical workspace</div>
  <h1>PNG Live Processing Workspace</h1>
  <div class="hero-sub">Separate Streamlit workspace for live Google Earth Engine layer review, rainfall and frost screening, map inspection, export preparation, and technical reporting. The public dashboard remains the briefing layer; EarthMap remains the broader FAO geospatial exploration platform.</div>
</div>
""", unsafe_allow_html=True)

ee_ready = initialise_earth_engine()
if not ee_ready:
    st.markdown("""<div class="soft-alert"><b>Workspace setup pending:</b> The live processing layers are not available yet because the Earth Engine service account still needs final permission setup in Google Cloud / Streamlit Secrets. This message is intentionally simplified for viewers. Technical authentication details are not displayed on the public page.</div>""", unsafe_allow_html=True)
    st.stop()

try:
    ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017").filter(ee.Filter.eq("country_na", "Papua New Guinea")).size().getInfo()
except Exception:
    st.markdown("""<div class="soft-alert"><b>Workspace setup pending:</b> Earth Engine connected, but the test query could not run. Please check the service account project permissions and private asset access.</div>""", unsafe_allow_html=True)
    st.stop()

utc_today = datetime.utcnow().date()
date_options = [utc_today - timedelta(days=d) for d in range(90, -1, -5)]

with st.sidebar:
    st.header("Control panel")
    mode = st.radio("Select active data layer", ["Drought: rainfall percentage of normal", "Frost: nighttime land surface temperature", "Both layers"])
    selected_today = st.select_slider(
        "Analysis date",
        options=date_options,
        value=utc_today,
        format_func=lambda d: f"{d.strftime('%d %b %Y')} (latest)" if d == utc_today else f"{d.strftime('%d %b %Y')} ({(utc_today - d).days} days back)",
        help="Select the anchor date for the analysis. The drought window uses the 90 days ending 15 days before this date; the frost window uses the 7 days ending on this date.",
    )
    opacity = st.slider("Data layer opacity", 0.10, 1.00, 0.85, 0.05)
    export_scale = st.selectbox("GeoTIFF export scale", [1000, 2500, 5000, 10000], index=2, help="Smaller values give higher-resolution exports but larger files.")

safe_end = selected_today - timedelta(days=15)
start_90 = safe_end - timedelta(days=90)
start_7 = selected_today - timedelta(days=7)
drought_period = f"{start_90} to {safe_end}"
frost_period = f"{start_7} to {selected_today}"
methodology_text = "Drought screening uses CHIRPS daily rainfall accumulated over a selected 90-day lag-safe window and compares it with a 2000-2022 same-month baseline. Frost screening uses MODIS Terra nighttime land surface temperature for the selected 7-day window, converted to Celsius and masked to highland areas above 2,200 m using SRTM elevation."

st.markdown(f"""<div class="method-grid"><div class="method-card"><b>Selected analysis date</b><span class="small-note">{selected_today.strftime('%d %b %Y')} ({(utc_today - selected_today).days} days back).</span></div><div class="method-card"><b>Drought data period</b><span class="small-note">CHIRPS rainfall window: {drought_period}</span></div><div class="method-card"><b>Frost data period</b><span class="small-note">MODIS night LST window: {frost_period}</span></div><div class="method-card"><b>Provincial summary</b><span class="small-note">Mean rainfall and frost indicators by province for this selected date.</span></div></div>""", unsafe_allow_html=True)

with st.expander("Methodology and interpretation note", expanded=True):
    st.markdown("""**Date selector:** Choose an analysis date from the last three months. The workspace then rebuilds the drought and frost windows around that selected date.

**Drought layer:** CHIRPS daily rainfall is accumulated over the selected lag-safe 90-day period and expressed as a percentage of normal rainfall for the same months. Lower percentages indicate rainfall deficit.

**Frost layer:** MODIS nighttime land surface temperature is converted to Celsius and masked to highland areas above 2,200 m using SRTM elevation. Lower values indicate areas that may require frost-related follow-up.

**Provincial summary:** The table summarises the currently selected map data by province using provincial polygons. Values are screening averages and should be verified with field observations.""")

st.markdown("<div class='success-strip'><b>Earth Engine status:</b> Connected using service account.</div>", unsafe_allow_html=True)

rainfall_vis = {"min": 50, "max": 150, "palette": ["#8b0000", "#ff4500", "#ffcc00", "#ffffff", "#00ccff", "#00008b"]}
frost_vis = {"min": -5, "max": 5, "palette": ["#0000ff", "#00ffff", "#ffffff", "#ffaa00", "#ff0000"]}
rain_img = build_drought_layer(str(start_90), str(safe_end))
frost_img = build_frost_layer(str(start_7), str(selected_today))
print_map_path = None
geotiff_url = None

try:
    print_map = build_map(mode, rain_img, frost_img, rainfall_vis, frost_vis, opacity, for_print=True)
    print_map.fit_bounds([[-12.0, 141.0], [-2.0, 156.0]], padding=(5, 5))
    print_map_path = capture_map_png(print_map)
except Exception:
    print_map_path = None

try:
    export_img, export_name, export_format = make_export_image(mode, rain_img, frost_img)
    geotiff_url = get_geotiff_url(export_img, export_name, export_format, export_scale)
except Exception:
    geotiff_url = None

with st.sidebar:
    st.markdown("---")
    st.subheader("Exports")
    pdf_buffer = make_pdf_report(mode, drought_period, frost_period, methodology_text, print_map_path)
    st.download_button("Download PDF map/report", data=pdf_buffer, file_name=f"PNG_Live_Processing_Workspace_Report_{selected_today}.pdf", mime="application/pdf", use_container_width=True)
    if geotiff_url:
        st.link_button("Download GeoTIFF", geotiff_url, use_container_width=True)
        st.caption("GeoTIFF links are generated by Earth Engine and may expire after a short period. Refresh the app to regenerate.")
    else:
        st.caption("GeoTIFF export link is not available yet. Check Earth Engine permissions or try a coarser export scale.")

st.subheader("Live GEE map review")
st.caption(f"Active dates: drought rainfall window {drought_period}; frost screening window {frost_period}.")
m = build_map(mode, rain_img, frost_img, rainfall_vis, frost_vis, opacity, for_print=False)
st_folium(m, width=None, height=720)

st.subheader("Provincial drought and frost summary")
st.markdown(f"""<div class="premium-card"><b>About this table:</b> The table summarises the selected map date, using provincial polygons to calculate average rainfall percentage of normal and mean highland nighttime land surface temperature. It is intended for screening and prioritisation, not as a confirmed impact assessment. Selected analysis date: <b>{selected_today.strftime('%d %b %Y')}</b>.</div>""", unsafe_allow_html=True)
try:
    summary_df = provincial_summary(str(start_90), str(safe_end), str(start_7), str(selected_today))
    st.info(make_table_summary(summary_df))
    st.dataframe(summary_df, use_container_width=True, hide_index=True)
    st.download_button("Download provincial summary CSV", data=summary_df.to_csv(index=False).encode("utf-8"), file_name=f"PNG_provincial_drought_frost_summary_{selected_today}.csv", mime="text/csv")
except Exception:
    st.info("Provincial summary is not available yet. Try refreshing the app or using a coarser export scale after Earth Engine finishes processing.")

st.markdown("""<div class="premium-card"><b>Operational note:</b> Use the date selector to review earlier drought/frost screening windows, switch data layers, inspect provincial boundaries, export GeoTIFF data, and download a provincial summary table. Final response decisions should be supported by field verification and official assessment channels.</div>""", unsafe_allow_html=True)
