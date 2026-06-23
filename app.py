import streamlit as st
import ee
import folium
from folium.plugins import MeasureControl, Fullscreen
from streamlit_folium import st_folium
from datetime import datetime, timedelta
import io
import os
from html2image import Html2Image

# Imports for automated cartographic PDF construction
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Set up the Streamlit page layout
st.set_page_config(layout="wide", page_title="PNG Advanced Hazard Workspace")
st.title("🇵🇬 PNG Advanced Climate Hazard Workspace")
st.markdown("A public-tier monitoring system featuring real-time analytical tools, vector extractions, and inquiry inspection overlays.")

# -------------------------------------------------------------
# EARTH ENGINE INITIALIZATION WITH SECRETS FALLBACK
# -------------------------------------------------------------
try:
    # Check if running on Streamlit Cloud with saved user-credential secrets
    if "EARTHENGINE_CREDENTIALS" in st.secrets:
        ee_creds = st.secrets["EARTHENGINE_CREDENTIALS"]
        
        # Import the native user credentials module to handle personal authentication tokens
        from google.oauth2.credentials import Credentials
        
        # Map user refresh tokens safely into the OAuth2 framework
        credentials = Credentials(
            token=None,
            refresh_token=ee_creds["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=ee_creds["client_id"],
            client_secret=ee_creds["client_secret"]
        )
        ee.Initialize(credentials=credentials)
    else:
        # Local fallback if running on your desktop machine
        ee.Initialize()
except Exception as e:
    st.error(f"Earth Engine failed to initialize: {str(e)}")
    st.info("If running on Streamlit Cloud, verify that the [EARTHENGINE_CREDENTIALS] block is saved correctly in your App Secrets panel.")

# -------------------------------------------------------------
# 1. CORE SPATIAL DATA SETUPS
# -------------------------------------------------------------
png_boundary = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017") \
    .filter(ee.Filter.eq('country_na', 'Papua New Guinea')) \
    .geometry()

dem = ee.Image('USGS/SRTMGL1_003').clip(png_boundary)
elevation = dem.select('elevation')
highland_mask = elevation.gt(2200)

# -------------------------------------------------------------
# 2. ANALYSIS TIMELINES
# -------------------------------------------------------------
today = datetime.today()
safe_end_date = today - timedelta(days=15)
three_months_ago = safe_end_date - timedelta(days=90)
one_week_ago = today - timedelta(days=7)

# -------------------------------------------------------------
# 3. GEOSPATIAL PROCESSING FUNCTIONS
# -------------------------------------------------------------
def get_drought_layer():
    current_rain = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY') \
        .filterDate(three_months_ago.strftime('%Y-%m-%d'), safe_end_date.strftime('%Y-%m-%d')) \
        .sum().clip(png_boundary)
    baseline_rain = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY') \
        .filter(ee.Filter.calendarRange(three_months_ago.month, safe_end_date.month, 'month')) \
        .filterDate('2000-01-01', '2022-12-31') \
        .mean().multiply(90).clip(png_boundary)
    return current_rain.divide(baseline_rain).multiply(100)

def get_frost_layer():
    collection = ee.ImageCollection('MODIS/061/MOD11A1') \
        .filterDate(one_week_ago.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')) \
        .select('LST_Night_1km')
    modis_lst = collection.min().clip(png_boundary)
    lst_celsius = modis_lst.multiply(0.02).subtract(273.15)
    return lst_celsius.updateMask(highland_mask)

def add_ee_layer(folium_map, ee_image_object, vis_params, name, opacity_val=1.0):
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    folium.raster_layers.TileLayer(
        tiles=map_id_dict['tile_fetcher'].url_format,
        attr='Google Earth Engine',
        name=name,
        overlay=True,
        control=True,
        opacity=opacity_val
    ).add_to(folium_map)

# Cache calculations to stabilize rendering
if 'drought_img' not in st.session_state:
    st.session_state.drought_img = get_drought_layer()
if 'frost_img' not in st.session_state:
    st.session_state.frost_img = get_frost_layer()

# -------------------------------------------------------------
# 4. CARTOGRAPHIC PDF PRINT REPORT ENGINE
# -------------------------------------------------------------
def generate_pdf_report(hazard_mode, folium_map, current_coordinates=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('RepTitle', parent=styles['Heading1'], fontSize=20, spaceAfter=4, textColor=colors.HexColor('#1a365d'))
    meta_style = ParagraphStyle('RepMeta', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#4a5568'))
    body_style = ParagraphStyle('RepBody', parent=styles['Normal'], fontSize=10, spaceBefore=4, spaceAfter=8)
    
    story.append(Paragraph(f"<b>PAPUA NEW GUINEA CLIMATE RISK ASSESSMENT REPORT</b>", title_style))
    story.append(Paragraph(f"<b>Generated:</b> {today.strftime('%Y-%m-%d %H:%M')} | <b>Data Authority:</b> Open-Source National Framework (GEE Pipeline)", meta_style))
    story.append(Spacer(1, 10))
    
    try:
        html_path = os.path.abspath("temp_map.html")
        folium_map.save(html_path)
        
        # Initialize html2image and trace linux chromium executables inside cloud containers
        hti = Html2Image()
        if os.path.exists("/usr/bin/chromium-browser"):
            hti.browser_executable = "/usr/bin/chromium-browser"
        elif os.path.exists("/usr/bin/chromium"):
            hti.browser_executable = "/usr/bin/chromium"
            
        hti.screenshot(url=f"file:///{html_path}", save_as="map_snapshot.png", size=(900, 450))
        
        story.append(Paragraph("<b>CURRENT SPATIAL MONITORING WORKSPACE VIEW</b>", styles['Heading3']))
        story.append(Image("map_snapshot.png", width=650, height=270))
        story.append(Spacer(1, 10))
    except Exception as e:
        story.append(Paragraph(f"<i>Map rendering attachment notice: Visual canvas skipped ({str(e)})</i>", meta_style))
        story.append(Spacer(1, 10))
    
    if hazard_mode == "Drought (Rainfall Anomaly)":
        story.append(Paragraph(f"<b>Target Feature Layer:</b> 3-Month Cumulative Rainfall Anomaly (%)", styles['Heading2']))
        story.append(Paragraph(f"<b>Operational Assessment Window:</b> {three_months_ago.strftime('%B %d, %Y')} to {safe_end_date.strftime('%B %d, %Y')}", body_style))
        story.append(Paragraph("<b>Methodology Brief:</b> Tracks rainfall shortages by summing daily CHIRPS satellite infrared rainfall estimates over a lag-safe 90-day window, then dividing it against a 22-year historical mean (2000-2022) for those exact calendar months.", body_style))
        
        legend_data = [
            [Paragraph("<b>Color Block</b>", styles['Normal']), Paragraph("<b>Anomaly Threshold</b>", styles['Normal']), Paragraph("<b>Risk Status / Action Context</b>", styles['Normal'])],
            ["Deep Red (#8b0000)", "Below 70%", "Severe Drought Conditions - Extreme crop failure risks"],
            ["Bright Orange (#ff4500)", "70% - 85%", "Moderate Moisture Deficit - Secondary water monitoring required"],
            ["Yellow (#ffcc00)", "85% - 95%", "Mild Water Stress - Incipient observation indicators"],
            ["White (#ffffff)", "95% - 105%", "Normal Historical Baseline Profile - Stable conditions"],
            ["Light Blue (#00ccff)", "Above 105%", "Wetter Than Normal - Enhanced catchment runoff matrices"]
        ]
    else:
        story.append(Paragraph(f"<b>Target Feature Layer:</b> Highland Frost Hazard Detection (&gt;2,200m ASL)", styles['Heading2']))
        story.append(Paragraph(f"<b>Operational Assessment Window:</b> {one_week_ago.strftime('%B %d, %Y')} to {today.strftime('%B %d, %Y')}", body_style))
        story.append(Paragraph("<b>Methodology Brief:</b> Extracts the rolling absolute minimum nighttime land surface temperatures observed by NASA's MODIS satellite over the past 7 days, then isolates and masks areas sitting above 2,200 meters elevation via SRTM data to track sweet potato crop vulnerability scales.", body_style))
        
        legend_data = [
            [Paragraph("<b>Color Block</b>", styles['Normal']), Paragraph("<b>Temperature Value</b>", styles['Normal']), Paragraph("<b>Risk Status / Action Context</b>", styles['Normal'])],
            ["Deep Blue (#0000ff)", "Below -2.0°C", "Severe Highland Frost Strike - Rapid crop tissue degradation"],
            ["Cyan (#00ffff)", "-2.0°C to 0.0°C", "Active Frost Line Detected - Vulnerable sweet potato zone impact"],
            ["White (#ffffff)", "0.0°C to 3.0°C", "Near Freezing Baseline - Localized thermal risks present"],
            ["Orange (#ffaa00)", "3.0°C to 5.0°C", "Stable Thermal Range - Normal high-altitude cultivation state"],
            ["Red (#ff0000)", "Above 5.0°C", "Warm Surface Baseline - Low risk configuration"]
        ]
        
    leg_table = Table(legend_data, colWidths=[130, 130, 440])
    leg_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (2,0), colors.HexColor('#e2e8f0')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    
    story.append(Paragraph("<b>OFFICIAL LEGEND SPECIFICATIONS</b>", styles['Heading4']))
    story.append(leg_table)
    
    if current_coordinates:
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>SITE SPECIFIC INQUIRY SPOT-CHECK PIN DATA</b>", styles['Heading4']))
        coord_data = [
            ["Inspected Latitude", f"{current_coordinates[0]:.4f}° N/S"],
            ["Inspected Longitude", f"{current_coordinates[1]:.4f}° E/W"]
        ]
        coord_table = Table(coord_data, colWidths=[150, 200])
        coord_table.setStyle(TableStyle([
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e0')),
            ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f7fafc')),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(coord_table)
        
    story.append(Spacer(1, 15))
    story.append(Paragraph("<b>[ ▲ NORTH ]</b> Map Orientation: True North | Scale: Dynamic Grid Representation Layer Display", meta_style))
    story.append(Paragraph("<b>Data Credits & Attributions:</b> Sourced via UCSB CHIRPS Daily and NASA MODIS MOD11A1 Data Pipelines.", meta_style))
    story.append(Paragraph("<b>System Developer Signature:</b> trekky675 (rudoq.007@gmail.com)", meta_style))
    
    doc.build(story)
    buffer.seek(0)
    
    for temp_file in ["temp_map.html", "map_snapshot.png"]:
        if os.path.exists(temp_file):
            try: os.remove(temp_file)
            except: pass
            
    return buffer

# -------------------------------------------------------------
# 5. SIDEBAR SETTINGS & DOWNLOAD PIPELINES
# -------------------------------------------------------------
st.sidebar.header("Control Panel")
hazard_type = st.sidebar.radio("Select Active Data Layer", ["Drought (Rainfall Anomaly)", "Frost Risk Tracking"])

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Layer Visibility Settings")
layer_opacity = st.sidebar.slider("Data Layer Opacity", min_value=0.0, max_value=1.0, value=0.85, step=0.05)

st.sidebar.markdown("---")
st.sidebar.subheader("📥 Export Active Raster Subsets")

if hazard_type == "Drought (Rainfall Anomaly)":
    try:
        drought_url = st.session_state.drought_img.getDownloadUrl({
            'scale': 5000, 'crs': 'EPSG:4326', 'region': png_boundary, 'format': 'GEO_TIFF'
        })
        st.sidebar.text_input("🔗 GeoTIFF Download Link", drought_url)
    except:
        st.sidebar.warning("Data link calculation pending...")
else:
    try:
        frost_url = st.session_state.frost_img.getDownloadUrl({
            'scale': 1000, 'crs': 'EPSG:4326', 'region': png_boundary, 'format': 'GEO_TIFF'
        })
        st.sidebar.text_input("🔗 GeoTIFF Download Link", frost_url)
    except:
        st.sidebar.warning("Data link calculation pending...")

# -------------------------------------------------------------
# 6. BUILD INTERACTIVE MAP CANVAS
# -------------------------------------------------------------
m = folium.Map(location=[-6.3, 146.5], zoom_start=6, control_scale=True, zoom_control=True)

folium.TileLayer(
    tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    attr="Google Maps", name="Google Satellite Hybrid", overlay=False, control=True
).add_to(m)

folium.TileLayer(
    tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attr="OpenStreetMap Contributors", name="OpenStreetMap (Standard)", overlay=False, control=True
).add_to(m)

folium.TileLayer(
    tiles="https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
    attr="Google Maps", name="Google Terrain Base", overlay=False, control=True
).add_to(m)

m.add_child(MeasureControl(position='topleft', primary_length_unit='kilometers', primary_area_unit='hectares'))
Fullscreen(position='topleft').add_to(m)

if hazard_type == "Drought (Rainfall Anomaly)":
    st.subheader("3-Month Cumulative Rainfall Anomaly (%) Workspace")
    
    st.info(f"📅 **Data Window:** {three_months_ago.strftime('%b %d, %Y')} to {safe_end_date.strftime('%b %d, %Y')} *(Adjusted for 15-day CHIRPS satellite publication lag)*\n\n"
            f"🔬 **Methodology:** Sums daily satellite infrared rainfall estimates across PNG for the past 90 days, then compares it as a percentage against a 22-year historical mean baseline (2000-2022) for the exact same calendar months.")
    
    drought_vis = {'min': 50, 'max': 150, 'palette': ['#8b0000', '#ff4500', '#ffcc00', '#ffffff', '#00ccff', '#00008b']}
    add_ee_layer(m, st.session_state.drought_img, drought_vis, 'Rainfall Anomaly Layer', opacity_val=layer_opacity)
    
    legend_css = (
        '<div style="position: absolute; bottom: 30px; left: 30px; width: 190px; height: 140px; '
        'background-color: white; border: 2px solid #cbd5e0; z-index: 1000; font-size: 11px; '
        'padding: 8px; border-radius: 4px; font-family: sans-serif; opacity: 0.95;">'
        '<b>Rainfall Anomaly (%)</b><br>'
        '<i style="background:#8b0000; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Severe Drought (&lt;70%)<br>'
        '<i style="background:#ff4500; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Moderate Dry (70-85%)<br>'
        '<i style="background:#ffcc00; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Mild Deficit (85-95%)<br>'
        '<i style="background:#ffffff; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px; border:1px solid #ccc;"></i> Normal (95-105%)<br>'
        '<i style="background:#00ccff; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Moderately Wet<br>'
        '<i style="background:#00008b; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Very Wet (&gt;130%)<br>'
        '</div>'
    )
    m.get_root().html.add_child(folium.Element(legend_css))
else:
    st.subheader("Highland Frost Hazard Core Workspace (Active Detection)")
    
    st.info(f"📅 **Data Window:** {one_week_ago.strftime('%b %d, %Y')} to {today.strftime('%b %d, %Y')} *(Near-real-time rolling 7-day minimum composite)*\n\n"
            f"🔬 **Methodology:** Extracts the absolute lowest nighttime land surface temperatures observed by NASA MODIS satellites over the past week. To highlight high-altitude food crop exposure (e.g., sweet potato), a Digital Elevation Model (SRTM 30m) is applied to mask out all terrain beneath 2,200 meters.")
    
    frost_vis = {'min': -5, 'max': 5, 'palette': ['#0000ff', '#00ffff', '#ffffff', '#ffaa00', '#ff0000']}
    add_ee_layer(m, st.session_state.frost_img, frost_vis, 'Night Surface Temperature (°C)', opacity_val=layer_opacity)
    
    legend_css = (
        '<div style="position: absolute; bottom: 30px; left: 30px; width: 190px; height: 120px; '
        'background-color: white; border: 2px solid #cbd5e0; z-index: 1000; font-size: 11px; '
        'padding: 8px; border-radius: 4px; font-family: sans-serif; opacity: 0.95;">'
        '<b>Night Surface Temp (°C)</b><br>'
        '<small>Highland Slopes &gt;2200m</small><br>'
        '<i style="background:#0000ff; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Severe Frost (&lt; -2°C)<br>'
        '<i style="background:#00ffff; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Active Frost (-2 to 0°C)<br>'
        '<i style="background:#ffffff; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px; border:1px solid #ccc;"></i> Near Freezing (0-3°C)<br>'
        '<i style="background:#ffaa00; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Stable Range (3-5°C)<br>'
        '<i style="background:#ff0000; width:12px; height:10px; float:left; margin-right:6px; margin-top:2px;"></i> Warm Baseline (&gt;5°C)<br>'
        '</div>'
    )
    m.get_root().html.add_child(folium.Element(legend_css))

folium.plugins.MousePosition(position='bottomright', separator=' | ', prefix='Coords: ').add_to(m)
folium.LayerControl(position='topright', collapsed=False).add_to(m)

# Render map interface
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
            pixel_val = st.session_state.drought_img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=point_geom, scale=5000
            ).get('precipitation').getInfo()
            if pixel_val is not None:
                st.info(f"📊 **Rainfall Status:** This point received **{pixel_val:.1f}%** of its historical normal rainfall profile over the past 90 days.")
            else:
                st.warning("Selected point falls outside current clipped terrestrial dataset parameters.")
        else:
            pixel_val = st.session_state.frost_img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=point_geom, scale=1000
            ).getInfo()
            if pixel_val:
                temp_val = list(pixel_val.values())[0]
                if temp_val is not None:
                    status = "❄️ CRITICAL FROST DETECTED" if temp_val <= 0 else "☀️ Normal Temperature Range"
                    st.info(f"🌡️ **Surface Temperature Profile:** Observed minimum temperature at this site sits at **{temp_val:.2f}°C** ({status}).")
                else:
                    st.warning("No active highland frost indices found inside the 2,200m crop limit at this point.")
            else:
                st.warning("Selected location sits beneath the 2,200-meter frost altitude baseline mask.")

# Add PDF Generation trigger directly beneath the active analytics reporting panel
st.sidebar.markdown("---")
st.sidebar.subheader("📄 Map Layout & Reporting")
if st.sidebar.button("Generate Layout Report (PDF)"):
    with st.spinner("Compiling cartographic print elements and capturing map view..."):
        pdf_data = generate_pdf_report(hazard_type, m, clicked_coords)
        st.sidebar.download_button(
            label="💾 Download PDF Map Report",
            data=pdf_data,
            file_name=f"PNG_Climate_Report_{today.strftime('%Y%m%d')}.pdf",
            mime="application/pdf"
        )

# -------------------------------------------------------------
# 8. APPLICATION FOOTER & ATTRIBUTIONS (Web Interface Layout)
# -------------------------------------------------------------
st.markdown("---")
footer_col1, footer_col2 = st.columns([3, 1])

with footer_col1:
    st.caption(
        "📊 **Data Credits & Attributions:**\n"
        "* **Precipitation Metrics:** Sourced via University of California Santa Barbara (UCSB) CHIRPS Daily v2.0 Image Infrastructure.\n"
        "* **Thermal Land Surface Profiles:** Extracted via NASA MODIS (MOD11A1 v061) Daily Nighttime 1km Grids.\n"
        "* **Topographical Baseline Modeling:** Constrained via USGS Shuttle Radar Topography Mission (SRTM GL1 30m) Elevation Datasets."
    )

with footer_col2:
    st.markdown(
        "<div style='text-align: right; padding-top: 10px; font-size: 13px; font-family: sans-serif; color: #718096;'>"
        "Developed by: <a href='mailto:rudoq.007@gmail.com' style='color: #3182ce; font-weight: bold; text-decoration: none;'>trekky675</a>"
        "</div>", 
        unsafe_allow_html=True
    )
