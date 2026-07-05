import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import ee

DEFAULT_PROJECT = "trekky675"


def initialise_earth_engine() -> str:
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path or not Path(creds_path).exists():
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set or the credential file does not exist.")

    with open(creds_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    project_id = os.environ.get("EARTHENGINE_PROJECT") or info.get("project_id") or DEFAULT_PROJECT
    credentials = ee.ServiceAccountCredentials(info["client_email"], creds_path)
    ee.Initialize(credentials, project=project_id)
    return project_id


def png_geometry():
    return ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017").filter(
        ee.Filter.eq("country_na", "Papua New Guinea")
    ).geometry()


def province_collection():
    return ee.FeatureCollection("FAO/GAUL/2015/level1").filter(
        ee.Filter.eq("ADM0_NAME", "Papua New Guinea")
    )


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
    return (
        night_lst.multiply(0.02)
        .subtract(273.15)
        .rename("night_lst_celsius")
        .updateMask(highland_mask)
    )


def classify_drought(value):
    if value is None:
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
    if value is None:
        return "No highland signal"
    if value <= -2:
        return "Severe frost signal"
    if value <= 0:
        return "Active frost line"
    if value <= 3:
        return "Near-freezing"
    return "Warmer / lower frost signal"


def provincial_summary(drought_start: str, drought_end: str, frost_start: str, frost_end: str):
    rain = build_drought_layer(drought_start, drought_end)
    frost = build_frost_layer(frost_start, frost_end)
    combined = rain.addBands(frost)
    fc = combined.reduceRegions(
        collection=province_collection(),
        reducer=ee.Reducer.mean(),
        scale=5000,
        tileScale=4,
    ).getInfo()

    rows = []
    for feature in fc.get("features", []):
        props = feature.get("properties", {})
        rain_val = props.get("rainfall_pct_normal")
        frost_val = props.get("night_lst_celsius")
        rows.append(
            {
                "province": props.get("ADM1_NAME", "Unknown"),
                "rainfall_pct_normal": round(rain_val, 1) if rain_val is not None else None,
                "drought_interpretation": classify_drought(rain_val),
                "mean_night_lst_c": round(frost_val, 1) if frost_val is not None else None,
                "frost_interpretation": classify_frost(frost_val),
            }
        )
    return sorted(rows, key=lambda r: r["province"])


def build_payload(anchor_date: str):
    selected_today = datetime.strptime(anchor_date, "%Y-%m-%d").date()
    safe_end = selected_today - timedelta(days=15)
    start_90 = safe_end - timedelta(days=90)
    start_7 = selected_today - timedelta(days=7)

    province_rows = provincial_summary(
        str(start_90),
        str(safe_end),
        str(start_7),
        str(selected_today),
    )

    return {
        "generated_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "analysis_date": str(selected_today),
        "drought_window": f"{start_90} to {safe_end}",
        "frost_window": f"{start_7} to {selected_today}",
        "workspace_url": "https://png-climate-workspace-v1.streamlit.app/",
        "notes": "Live PNG drought and frost screening workspace summary from the separate Streamlit/GEE processing workflow.",
        "available_layers": ["Drought", "Frost", "Both"],
        "province_summary": province_rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--anchor-date", default=datetime.utcnow().date().isoformat())
    args = parser.parse_args()

    initialise_earth_engine()
    payload = build_payload(args.anchor_date)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote live processing status JSON to {out_path}")


if __name__ == "__main__":
    main()
