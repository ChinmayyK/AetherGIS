"""AetherGIS — NASA GIBS WMS Client with caching, retry, and rate-limiting."""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx
import numpy as np
from PIL import Image
import io

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

# ── Scientific Layer Registry ────────────────────────────────────────────────────
#
# Each entry follows operational meteorological conventions:
#   nadir_lon       : Geostationary satellite sub-satellite point (nadir) longitude
#   coverage_*      : Practical coverage limit (zenith angle < ~75°, beyond which
#                     imagery quality degrades unacceptably for meteorological use)
#   preset_regions  : Named WMO/RSMC/IMD sub-domains used operationally
#                     bbox format: [lon_min, lat_min, lon_max, lat_max]  (WGS84)
#   default_preset  : Key of the operationally most common preset for this layer
#
# References:
#   WMO: https://wmo.int/activities/global-satellite-observing-system
#   RSMC New Delhi (IMD): https://rsmcnewdelhi.imd.gov.in
#   JTWC: https://www.metoc.navy.mil/jtwc
#   NHC: https://www.nhc.noaa.gov
#   BoM TCWC: http://www.bom.gov.au/cyclone
# ─────────────────────────────────────────────────────────────────────────────────

GIBS_LAYERS: dict[str, dict] = {

    # ── GOES-East (GOES-16/18, nadir 75.2°W) ─────────────────────────────────
    # Operational sensor for NHC (Miami), RSMC Miami, CPTEC, SMN Argentina
    "GOES-East_ABI_Band2_Red_Visible_1km": {
        "name": "GOES-East Visible — Americas",
        "temporal_resolution_minutes": 10,
        "use_case": "Daytime cloud/storm tracking (Americas and Atlantic)",
        "description": (
            "GOES-16 ABI Band 2 (0.64 µm) Red Visible at 1km. 10-minute CONUS, "
            "Full Disk every 10 min. Primary day-time imagery for NHC operations. "
            "Daytime only — use Band 13 (IR) for 24/7 monitoring."
        ),
        "nadir_lon": -75.2,
        "coverage_lon_min": -135.0, "coverage_lon_max": 15.0,
        "coverage_lat_min": -60.0,  "coverage_lat_max": 60.0,
        "coverage_note": "GOES-East (nadir 75.2°W) covers Americas and Atlantic. Zenith angle >75° west of 135°W / east of 15°E renders imagery unusable.",
        "preset_regions": {
            "atlantic_hurricane_belt": {
                "label": "Atlantic Hurricane Belt (NHC)",
                "bbox": [-100.0, 5.0, -10.0, 35.0],
                "description": "Primary NHC monitoring domain. Covers MDR (Main Development Region) for Atlantic tropical cyclones (10°N–20°N, 20°W–85°W extended).",
                "agency": "NHC / RSMC Miami",
            },
            "gulf_of_mexico": {
                "label": "Gulf of Mexico",
                "bbox": [-98.0, 18.0, -78.0, 32.0],
                "description": "High-value economic zone. Warm SSTs fuel rapid intensification of Gulf hurricanes.",
                "agency": "NHC",
            },
            "caribbean": {
                "label": "Caribbean Sea",
                "bbox": [-87.0, 10.0, -57.0, 27.0],
                "description": "Active tropical cyclone track region. Warm pool SST often >28°C.",
                "agency": "NHC / RSMC Miami",
            },
        },
        "default_preset": "atlantic_hurricane_belt",
    },

    "GOES-East_ABI_Band13_Clean_Infrared": {
        "name": "GOES-East Infrared 10.3µm — Americas",
        "temporal_resolution_minutes": 10,
        "use_case": "24/7 cyclone/convection tracking (Americas and Atlantic)",
        "description": (
            "GOES-16 ABI Band 13 (10.3 µm) Clean Longwave IR at 2km. "
            "This is the primary operational IR channel for NHC tropical analysis. "
            "Cold cloud tops (< -65°C → brightness temp < 208 K) indicate deep convection. "
            "Works day and night — unaffected by solar illumination."
        ),
        "nadir_lon": -75.2,
        "coverage_lon_min": -135.0, "coverage_lon_max": 15.0,
        "coverage_lat_min": -60.0,  "coverage_lat_max": 60.0,
        "coverage_note": "GOES-East (nadir 75.2°W). Use Himawari-9 for India/Asia region.",
        "preset_regions": {
            "atlantic_hurricane_belt": {
                "label": "Atlantic MDR — NHC Primary",
                "bbox": [-100.0, 5.0, -10.0, 35.0],
                "description": "Main Development Region for Atlantic tropical cyclones. Operationally monitored by NHC 24/7 during Jun–Nov.",
                "agency": "NHC / RSMC Miami",
            },
            "gulf_of_mexico": {
                "label": "Gulf of Mexico",
                "bbox": [-98.0, 18.0, -78.0, 32.0],
                "description": "Rapid intensification risk zone. Loop Current raises SSTs >30°C.",
                "agency": "NHC",
            },
            "conus_southeast": {
                "label": "US Southeast / Florida",
                "bbox": [-90.0, 24.0, -75.0, 36.0],
                "description": "High-impact landfall zone. Used by NWS local offices.",
                "agency": "NWS",
            },
        },
        "default_preset": "atlantic_hurricane_belt",
    },

    # ── GOES-West (GOES-18, nadir 137.2°W) ───────────────────────────────────
    # Operational sensor for Central Pacific Hurricane Center, NWS
    "GOES-West_ABI_Band13_Clean_Infrared": {
        "name": "GOES-West Infrared 10.3µm — E. Pacific",
        "temporal_resolution_minutes": 10,
        "use_case": "24/7 cyclone tracking (East Pacific and US West Coast)",
        "description": (
            "GOES-18 ABI Band 13 (10.3 µm) Clean Longwave IR. "
            "Primary sensor for Central Pacific Hurricane Center (CPHC) and "
            "Eastern Pacific tropical cyclone monitoring (EPAC basin). "
            "The EPAC basin produces more named storms per year than the Atlantic."
        ),
        "nadir_lon": -137.2,
        "coverage_lon_min": -165.0, "coverage_lon_max": -105.0,
        "coverage_lat_min": -60.0,  "coverage_lat_max": 60.0,
        "coverage_note": "GOES-West (nadir 137.2°W) covers Eastern Pacific. Optimal from 165°W to 105°W.",
        "preset_regions": {
            "epac_main_development": {
                "label": "E. Pacific MDR (CPHC/NHC)",
                "bbox": [-140.0, 5.0, -100.0, 25.0],
                "description": "Main Development Region for Eastern Pacific tropical cyclones. Peak season May–November.",
                "agency": "NHC / CPHC",
            },
            "baja_california": {
                "label": "Gulf of California / Baja",
                "bbox": [-116.0, 20.0, -104.0, 32.0],
                "description": "Landfall risk zone for EPAC storms recurving northward.",
                "agency": "CONAGUA Mexico",
            },
        },
        "default_preset": "epac_main_development",
    },

    # ── Himawari-9 (JAXA/JMA, nadir 140.7°E) ─────────────────────────────────
    # Primary operational satellite for RSMC New Delhi (IMD), RSMC Tokyo (JMA),
    # RSMC Nadi (Fiji), BoM TCWC (Darwin/Perth/Brisbane)
    "Himawari_AHI_Band13_Clean_Infrared": {
        "name": "Himawari-9 Infrared 10.4µm — India / Asia-Pacific",
        "temporal_resolution_minutes": 10,
        "use_case": "24/7 cyclone/monsoon/convection tracking (Indian Ocean, Bay of Bengal, Arabian Sea, W. Pacific)",
        "description": (
            "Himawari-9 AHI Band 13 (10.4 µm) Clean Longwave IR at ~2km. "
            "Primary operational satellite for IMD (India Meteorological Dept), JMA, and BoM. "
            "Indian Ocean basin TC peak season: Apr–Jun and Oct–Dec (NIO); Nov–Apr (SIO). "
            "Cloud top temperatures: < -65°C (208 K) = intense convection, "
            "< -80°C (193 K) = likely CDO (Central Dense Overcast) / eyewall."
        ),
        "nadir_lon": 140.7,
        "coverage_lon_min": 80.0, "coverage_lon_max": 200.0,
        "coverage_lat_min": -60.0, "coverage_lat_max": 60.0,
        "coverage_note": "Himawari-9 (nadir 140.7°E). Covers India eastward to Pacific. Primary sensor for RSMC New Delhi (IMD) cyclone operations.",
        "preset_regions": {
            "bay_of_bengal": {
                "label": "Bay of Bengal — RSMC New Delhi (Primary)",
                "bbox": [78.0, 4.0, 100.0, 24.0],
                "description": (
                    "Primary monitoring domain for RSMC New Delhi (IMD). "
                    "The BoB is one of the world's most active TC basins — "
                    "warm SSTs (>28°C), high humidity, and weak wind shear during Oct–Dec. "
                    "Storms here directly impact India's east coast, Bangladesh, Myanmar."
                ),
                "agency": "RSMC New Delhi / IMD",
            },
            "arabian_sea": {
                "label": "Arabian Sea — IMD / JTWC",
                "bbox": [52.0, 8.0, 78.0, 28.0],
                "description": (
                    "Secondary NIO basin. Arabian Sea TCs are rarer but often intensify "
                    "rapidly due to warm SSTs and low shear in Apr–Jun and Oct–Nov. "
                    "Direct risk to India's west coast (Gujarat, Maharashtra), Oman, Pakistan."
                ),
                "agency": "RSMC New Delhi / IMD / JTWC",
            },
            "india_subcontinent": {
                "label": "Indian Subcontinent Overview",
                "bbox": [65.0, 5.0, 100.0, 38.0],
                "description": "Full India overview for monsoon onset/withdrawal monitoring and multi-basin TC surveillance.",
                "agency": "IMD",
            },
            "south_china_sea": {
                "label": "South China Sea / Philippines",
                "bbox": [105.0, 0.0, 130.0, 25.0],
                "description": "Highly active WPac TC breeding ground. Peak season Jun–Dec. Monitored by RSMC Tokyo (JMA) and PAGASA.",
                "agency": "JMA / RSMC Tokyo / PAGASA",
            },
            "western_pacific": {
                "label": "Western Pacific — RSMC Tokyo",
                "bbox": [120.0, 5.0, 160.0, 35.0],
                "description": "World's most active TC basin (WPac). ~26 named storms/year. Primary domain for JMA/JTWC operations.",
                "agency": "JMA / RSMC Tokyo / JTWC",
            },
            "india_cloud_movements": {
                "label": "India Cloud Movements (IR) — RSMC New Delhi",
                "bbox": [65.0, 5.0, 100.0, 38.0],
                "description": (
                    "Optimized preset for tracking monsoon convection and cloud-top movement "
                    "across the Indian Subcontinent. Infrared Band 13 allows 24/7 monitoring "
                    "of thermal structure and convective intensity."
                ),
                "agency": "RSMC New Delhi / IMD",
            },
            "australian_region": {
                "label": "Australian Region — BoM TCWC",
                "bbox": [100.0, -40.0, 160.0, -10.0],
                "description": "Southern Indian Ocean + Coral Sea. BoM TCWCs (Darwin, Perth, Brisbane) issue advisories Nov–Apr.",
                "agency": "BoM TCWC Darwin/Perth/Brisbane",
            },
        },
        "default_preset": "bay_of_bengal",
    },

    "Himawari_AHI_Band3_Red_Visible_1km": {
        "name": "Himawari-9 Visible 0.64µm — Asia-Pacific",
        "temporal_resolution_minutes": 10,
        "use_case": "Daytime cloud morphology and TC structure analysis (Asia-Pacific)",
        "description": (
            "Himawari-9 AHI Band 3 (0.64 µm) Red Visible at ~1km. "
            "Used for detailed daytime analysis: eye structure, spiral banding, "
            "dry air intrusion. Paired with Band 13 IR for combined day/night operations."
        ),
        "nadir_lon": 140.7,
        "coverage_lon_min": 80.0, "coverage_lon_max": 200.0,
        "coverage_lat_min": -60.0, "coverage_lat_max": 60.0,
        "coverage_note": "Himawari-9 visible channel — daytime only. For 24/7 monitoring use Band 13 IR.",
        "preset_regions": {
            "bay_of_bengal": {
                "label": "Bay of Bengal",
                "bbox": [78.0, 4.0, 100.0, 24.0],
                "description": "Primary IMD monitoring domain for NIO cyclone season.",
                "agency": "RSMC New Delhi / IMD",
            },
            "arabian_sea": {
                "label": "Arabian Sea",
                "bbox": [52.0, 8.0, 78.0, 28.0],
                "description": "NIO secondary basin. Landfall risk: India West Coast, Oman, Pakistan.",
                "agency": "RSMC New Delhi / IMD",
            },
            "western_pacific": {
                "label": "Western Pacific",
                "bbox": [120.0, 5.0, 160.0, 35.0],
                "description": "World's most active TC basin. JMA/JTWC primary domain.",
                "agency": "JMA / RSMC Tokyo",
            },
        },
        "default_preset": "bay_of_bengal",
    },

    # ── MODIS (Terra/Aqua, polar orbit, global) ───────────────────────────────
    # Terra: ~10:30 LT equator crossing, Aqua: ~13:30 LT
    # 250m visible + 1km thermal — gold standard for aerosol, fire, SST
    "MODIS_Terra_CorrectedReflectance_TrueColor": {
        "name": "MODIS Terra True Color (Daily, Global)",
        "temporal_resolution_minutes": 1440,
        "use_case": "High-resolution post-event analysis, aerosol optical depth, land/ocean daily",
        "description": (
            "Terra MODIS Band 1-4-3 corrected reflectance composite at 250m. "
            "Terra crosses equator ~10:30 local time descending. "
            "Not suitable for real-time TC tracking (once per day), but valuable for "
            "post-storm damage assessment, smoke plume tracking, and SST anomaly context."
        ),
        "nadir_lon": None,  # Polar orbit — global
        "coverage_lon_min": -180.0, "coverage_lon_max": 180.0,
        "coverage_lat_min": -90.0,  "coverage_lat_max": 90.0,
        "coverage_note": "Global polar orbit. Once per day at local ~10:30 AM.",
        "preset_regions": {
            "bay_of_bengal": {
                "label": "Bay of Bengal",
                "bbox": [78.0, 4.0, 100.0, 24.0],
                "description": "Post-storm damage assessment domain for NIO TCs.",
                "agency": "IMD / RSMC New Delhi",
            },
            "india": {
                "label": "Indian Subcontinent",
                "bbox": [65.0, 5.0, 100.0, 38.0],
                "description": "Full India overview for monsoon, aerosol, fire smoke monitoring.",
                "agency": "IMD / ISRO",
            },
            "south_asia": {
                "label": "South/Southeast Asia",
                "bbox": [60.0, 5.0, 115.0, 40.0],
                "description": "Regional overview including Bay of Bengal, Arabian Sea, and SE Asia.",
                "agency": "WMO RA II",
            },
            "global_overview": {
                "label": "Global Overview",
                "bbox": [-180.0, -60.0, 180.0, 60.0],
                "description": "Full global extent for large-scale atmospheric pattern analysis.",
                "agency": "WMO",
            },
        },
        "default_preset": "bay_of_bengal",
    },

    "MODIS_Aqua_CorrectedReflectance_TrueColor": {
        "name": "MODIS Aqua True Color (Daily, Global)",
        "temporal_resolution_minutes": 1440,
        "use_case": "Afternoon pass complement to Terra — SST, aerosol, cloud (global)",
        "description": (
            "Aqua MODIS Band 1-4-3 corrected reflectance at 250m. "
            "Aqua crosses equator ~13:30 local time ascending — afternoon pass. "
            "Aqua's AMSR-E and CERES instruments also provide SST and radiation budget. "
            "Complementary to Terra: together they give ~2 overpasses per day."
        ),
        "nadir_lon": None,
        "coverage_lon_min": -180.0, "coverage_lon_max": 180.0,
        "coverage_lat_min": -90.0,  "coverage_lat_max": 90.0,
        "coverage_note": "Global polar orbit. Once per day at local ~1:30 PM.",
        "preset_regions": {
            "bay_of_bengal": {
                "label": "Bay of Bengal",
                "bbox": [78.0, 4.0, 100.0, 24.0],
                "description": "Standard NIO cyclone monitoring domain.",
                "agency": "IMD / RSMC New Delhi",
            },
            "india": {
                "label": "Indian Subcontinent",
                "bbox": [65.0, 5.0, 100.0, 38.0],
                "description": "Full India + surrounding waters overview.",
                "agency": "IMD / ISRO",
            },
        },
        "default_preset": "bay_of_bengal",
    },

    # ── VIIRS (Suomi-NPP/NOAA-20, polar) ─────────────────────────────────────
    "VIIRS_SNPP_DayNightBand_ENCC": {
        "name": "VIIRS Day-Night Band — Nighttime Lights (Global)",
        "temporal_resolution_minutes": 1440,
        "use_case": "Post-storm power outage mapping, urban extent, nighttime fire/flare detection",
        "description": (
            "Suomi-NPP VIIRS Day-Night Band (DNB) Enhanced Near Constant Contrast. "
            "Unique 750m panchromatic band sensitive to moonlit clouds, city lights, fires. "
            "Post-landfall use: power outage mapping (dark areas that were lit before storm). "
            "Also detects gas flares, fishing vessel lights, volcanic activity."
        ),
        "nadir_lon": None,
        "coverage_lon_min": -180.0, "coverage_lon_max": 180.0,
        "coverage_lat_min": -90.0,  "coverage_lat_max": 90.0,
        "coverage_note": "Global polar orbit. Nighttime pass only for meaningful DNB data.",
        "preset_regions": {
            "india": {
                "label": "Indian Subcontinent — Power Outage Mapping",
                "bbox": [65.0, 5.0, 100.0, 38.0],
                "description": "Post-cyclone power outage detection. Compare pre/post-storm city light intensity.",
                "agency": "NDMA / IMD",
            },
            "bay_of_bengal": {
                "label": "Bay of Bengal Region",
                "bbox": [78.0, 4.0, 100.0, 24.0],
                "description": "Fishing vessel detection and post-storm darkness mapping.",
                "agency": "IMD / Coast Guard",
            },
        },
        "default_preset": "india",
    },
}

# Curated list of supported ISRO Bhuvan layers (PRD Phase 2)
BHUVAN_LAYERS: dict[str, dict] = {
    "rs2_awifs_india": {
        "name": "Resourcesat-2 AWiFS (India)",
        "temporal_resolution_minutes": 7200,  # ~5 days
        "use_case": "Agricultural/Vegetation monitoring",
        "description": "5-day repeat cycle high-resolution imagery over India",
        "base_url": "https://bhuvan-ras2.nrsc.gov.in/bhuvan/wms",
    },
    "lulc_250k": {
        "name": "LULC 250k (India)",
        "temporal_resolution_minutes": 525600, # Annual
        "use_case": "Land use / Land cover analysis",
        "description": "Annual Land Use and Land Cover maps of India",
        "base_url": "https://bhuvan-ras2.nrsc.gov.in/bhuvan/wms",
    },
}

# ── MOSDAC layer-name mapping ─────────────────────────────────────────────────
# Maps our canonical layer IDs (= MOSDAC WMS LAYERS param values) to human
# metadata.  The key *is* the WMS layer name sent in the LAYERS= query param.
#
# INSAT-3D  (82.0°E): operational February 2014 – present
# INSAT-3DR (83.0°E): operational September 2016 – present
#
# MOSDAC WMS endpoint: https://mosdac.gov.in/live/wms
# References:
#   https://mosdac.gov.in/                        (ISRO/SAC portal)
#   https://www.isro.gov.in/INSAT_3D.html
#   ISRO Satellite Centre INSAT-3D/3DR user guides
# ─────────────────────────────────────────────────────────────────────────────

_INSAT_COMMON_PRESETS: dict = {
    "india": {
        "label": "Indian Subcontinent",
        "bbox": [65.0, 5.0, 100.0, 38.0],
        "description": "Full India overview including surrounding seas. "
                       "Primary domain for IMD operational monitoring.",
        "agency": "MOSDAC / IMD / ISRO",
    },
    "bay_of_bengal": {
        "label": "Bay of Bengal",
        "bbox": [78.0, 4.0, 100.0, 24.0],
        "description": "Primary NIO cyclone basin. Monitored by RSMC New Delhi (IMD).",
        "agency": "IMD / RSMC New Delhi",
    },
    "arabian_sea": {
        "label": "Arabian Sea",
        "bbox": [52.0, 8.0, 78.0, 28.0],
        "description": "NIO secondary basin. Rapid intensification risk. "
                       "Landfall risk: Gujarat, Maharashtra, Oman, Pakistan.",
        "agency": "IMD / RSMC New Delhi",
    },
    "south_asia": {
        "label": "South Asia Regional",
        "bbox": [50.0, -10.0, 120.0, 40.0],
        "description": "Full INSAT-3D disk view of South / South-East Asia and surrounding oceans.",
        "agency": "MOSDAC / IMD",
    },
}

INSAT_LAYERS: dict[str, dict] = {
    # ── INSAT-3D (82.0°E nadir) ───────────────────────────────────────────────
    "INSAT3D_VIS": {
        "name": "INSAT-3D Visible (VIS, 0.65 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Daytime cloud/storm/cyclone tracking over India & Indian Ocean",
        "description": (
            "INSAT-3D VIS channel (0.65 µm) at ~1 km resolution. "
            "Half-hourly composites via MOSDAC live WMS. "
            "Daytime only — use TIR1 for 24/7 monitoring. "
            "Primary sensor for IMD operational cyclone surveillance. "
            "Nadir at 82.0°E; disk covers 40°–120°E, 40°S–40°N."
        ),
        "nadir_lon": 82.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3D (nadir 82°E). Daytime visible. "
                         "Use INSAT3D_TIR1 for night/24-7 monitoring.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
    "INSAT3D_TIR1": {
        "name": "INSAT-3D Thermal Infrared 1 (TIR1, 10.8 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "24/7 cyclone/monsoon/convection tracking (India & Indian Ocean)",
        "description": (
            "INSAT-3D TIR1 channel (10.8 µm) at ~4 km resolution. "
            "Primary operational IR channel for IMD. "
            "Cold cloud tops (< −65 °C / 208 K) indicate deep convection; "
            "< −80 °C (193 K) = likely CDO / eyewall. "
            "Works day and night — not affected by solar illumination."
        ),
        "nadir_lon": 82.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3D (nadir 82°E). Primary 24/7 IR channel. "
                         "Pair with VIS for daytime structural analysis.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "bay_of_bengal",
    },
    "INSAT3D_TIR2": {
        "name": "INSAT-3D Thermal Infrared 2 (TIR2, 12.0 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Sea Surface Temperature retrieval & split-window cloud-top temperature",
        "description": (
            "INSAT-3D TIR2 channel (12.0 µm) at ~4 km resolution. "
            "Used in split-window algorithm with TIR1 for SST and cloud-top temperature "
            "retrieval. Complementary to TIR1 for atmospheric correction. "
            "Monsoon SST warm-pool tracking in Arabian Sea and Bay of Bengal."
        ),
        "nadir_lon": 82.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3D TIR2 (12 µm). Use alongside TIR1 for split-window SST.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
    "INSAT3D_MIR": {
        "name": "INSAT-3D Mid Infrared (MIR, 3.9 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Fire detection, low cloud/fog discrimination, nighttime sea surface features",
        "description": (
            "INSAT-3D MIR channel (3.9 µm) at ~4 km resolution. "
            "Highly sensitive to fire hotspots (forest fires, crop burning). "
            "Day: sun glint over ocean reveals low-level wind patterns. "
            "Night: fog / stratus discrimination from cloud-free sky."
        ),
        "nadir_lon": 82.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3D MIR (3.9 µm). Best for fire/fog detection.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
    "INSAT3D_SWIR": {
        "name": "INSAT-3D Short-Wave Infrared (SWIR, 1.625 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Snow/ice vs. cloud discrimination, vegetation stress, aerosol",
        "description": (
            "INSAT-3D SWIR channel (1.625 µm) at ~1 km resolution. "
            "Unique capability for discriminating snow/ice from cloud (ice absorbs at 1.6 µm, "
            "water clouds reflect). Also used for aerosol optical depth over bright surfaces "
            "and vegetation stress detection."
        ),
        "nadir_lon": 82.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3D SWIR (1.625 µm). Daytime only.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
    "INSAT3D_WV": {
        "name": "INSAT-3D Water Vapour (WV, 6.8 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Upper-tropospheric moisture, monsoon dynamics, AMV derivation",
        "description": (
            "INSAT-3D WV channel (6.8 µm) at ~8 km resolution. "
            "Sensitive to upper-tropospheric water vapour (300–600 hPa). "
            "Used for Atmospheric Motion Vectors (AMVs) — primary wind product for NWP. "
            "Dark areas = dry upper atmosphere (subsidence / clear outflow); "
            "bright = moist upper troposphere (deep convection, monsoon trough)."
        ),
        "nadir_lon": 82.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3D WV (6.8 µm). Upper-tropospheric moisture channel.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },

    # ── INSAT-3DR (83.0°E nadir) ──────────────────────────────────────────────
    # INSAT-3DR is the redundancy/backup satellite for INSAT-3D, operational
    # since September 2016. It carries the same imager payload.
    "INSAT3DR_VIS": {
        "name": "INSAT-3DR Visible (VIS, 0.65 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Daytime cloud/storm tracking (India & Indian Ocean) — 3DR",
        "description": (
            "INSAT-3DR VIS channel (0.65 µm) at ~1 km resolution. "
            "INSAT-3DR is the backup/redundancy satellite at 83.0°E nadir. "
            "Operational since September 2016. Same sensor design as INSAT-3D. "
            "Often cross-referenced with INSAT-3D for consistency validation."
        ),
        "nadir_lon": 83.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3DR (nadir 83°E). Redundancy satellite. Daytime VIS.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
    "INSAT3DR_TIR1": {
        "name": "INSAT-3DR Thermal Infrared 1 (TIR1, 10.8 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "24/7 cyclone/convection tracking (India & Indian Ocean) — 3DR",
        "description": (
            "INSAT-3DR TIR1 channel (10.8 µm) at ~4 km resolution. "
            "Operational since September 2016. Backup satellite for INSAT-3D. "
            "Used operationally during INSAT-3D maintenance/eclipse windows."
        ),
        "nadir_lon": 83.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3DR TIR1 (10.8 µm). 24/7 backup IR channel.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "bay_of_bengal",
    },
    "INSAT3DR_TIR2": {
        "name": "INSAT-3DR Thermal Infrared 2 (TIR2, 12.0 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "SST retrieval & split-window cloud-top temperature — 3DR",
        "description": (
            "INSAT-3DR TIR2 channel (12.0 µm) at ~4 km resolution. "
            "Operational since September 2016. "
            "Split-window SST product derived jointly with TIR1."
        ),
        "nadir_lon": 83.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3DR TIR2 (12 µm). Use alongside TIR1 for SST.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
    "INSAT3DR_MIR": {
        "name": "INSAT-3DR Mid Infrared (MIR, 3.9 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Fire detection, low cloud/fog — 3DR",
        "description": (
            "INSAT-3DR MIR channel (3.9 µm) at ~4 km resolution. "
            "Operational since September 2016. "
            "Same fire/fog detection capability as INSAT-3D MIR."
        ),
        "nadir_lon": 83.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3DR MIR (3.9 µm). Fire/fog detection backup.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
    "INSAT3DR_WV": {
        "name": "INSAT-3DR Water Vapour (WV, 6.8 µm)",
        "temporal_resolution_minutes": 30,
        "use_case": "Upper-tropospheric moisture & AMV derivation — 3DR",
        "description": (
            "INSAT-3DR WV channel (6.8 µm) at ~8 km resolution. "
            "Operational since September 2016. "
            "Used together with INSAT-3D WV for improved AMV coverage."
        ),
        "nadir_lon": 83.0,
        "coverage_lon_min": 40.0, "coverage_lon_max": 120.0,
        "coverage_lat_min": -40.0, "coverage_lat_max": 40.0,
        "coverage_note": "INSAT-3DR WV (6.8 µm). Upper-tropospheric moisture backup.",
        "preset_regions": _INSAT_COMMON_PRESETS,
        "default_preset": "india",
    },
}


# ── INSAT temporal slot helper ─────────────────────────────────────────────────

def _round_to_insat_slot(ts: datetime) -> datetime:
    """
    Round a datetime down to the nearest INSAT-3D/3DR 30-minute composite slot.

    MOSDAC composites are produced at :00 and :30 of each UTC hour.
    Supplying an off-slot time causes MOSDAC to return a WMS ServiceException
    rather than the nearest available frame.

    Examples:
        14:07 UTC  →  14:00 UTC
        14:31 UTC  →  14:30 UTC
        14:59 UTC  →  14:30 UTC
    """
    slot_minute = 0 if ts.minute < 30 else 30
    return ts.replace(minute=slot_minute, second=0, microsecond=0)


@dataclass
class SatelliteFrame:
    """A single retrieved satellite image frame."""
    timestamp: datetime
    layer_id: str
    bbox: list[float]
    resolution: int
    image: np.ndarray  # float32 RGB [H, W, 3] in [0.0, 1.0]
    image_hash: str
    source: str = "nasa_gibs"
    is_valid: bool = True
    validation_flags: list[str] = field(default_factory=list)


class WMSClientError(Exception):
    """Raised when WMS retrieval fails."""
    pass


class NASAGIBSClient:
    """
    Async NASA GIBS WMS client.

    Features:
    - ISO 8601 time-dimensioned requests
    - Exponential backoff retry (3 attempts)
    - In-memory tile cache (keyed by request hash)
    - 1-second rate-limit delay between requests (Rule DI-04)
    - Frame content validation (Rule DQ-01)
    """

    WMS_PARAMS_BASE = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "SRS": "EPSG:4326",
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
        "STYLES": "",
    }

    def __init__(self) -> None:
        self.settings = get_settings()
        self._cache: dict[str, SatelliteFrame] = {}
        self._last_request_time: float = 0.0
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "NASAGIBSClient":
        self._client = httpx.AsyncClient(
            timeout=self.settings.wms_timeout_seconds,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    def _build_request_params(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
    ) -> dict[str, str]:
        minlon, minlat, maxlon, maxlat = bbox
        return {
            **self.WMS_PARAMS_BASE,
            "LAYERS": layer_id,
            "BBOX": f"{minlon},{minlat},{maxlon},{maxlat}",
            "WIDTH": str(resolution),
            "HEIGHT": str(resolution),
            "TIME": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _cache_key(self, params: dict) -> str:
        canonical = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hashlib.md5(canonical.encode(), usedforsecurity=False).hexdigest()

    async def _rate_limit(self) -> None:
        """Enforce minimum 1-second delay between requests (Rule DI-04)."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.settings.wms_rate_limit_delay:
            await asyncio.sleep(self.settings.wms_rate_limit_delay - elapsed)
        self._last_request_time = time.monotonic()

    async def _fetch_with_retry(self, params: dict) -> bytes:
        """Fetch WMS tile with exponential backoff retry."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.settings.wms_max_retries + 1):
            try:
                await self._rate_limit()
                resp = await self._client.get(
                    self.settings.nasa_gibs_base_url,
                    params=params,
                )
                resp.raise_for_status()
                # WMS may return XML error document even on 200
                if resp.headers.get("content-type", "").startswith("application/vnd.ogc"):
                    raise WMSClientError(f"WMS service exception: {resp.text[:200]}")
                return resp.content
            except (httpx.HTTPError, WMSClientError) as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "WMS request failed",
                    attempt=attempt,
                    wait_seconds=wait,
                    error=str(exc),
                )
                if attempt < self.settings.wms_max_retries:
                    await asyncio.sleep(wait)

        raise WMSClientError(
            f"WMS request failed after {self.settings.wms_max_retries} attempts: {last_exc}"
        )

    def _decode_image(self, raw_bytes: bytes) -> np.ndarray:
        """
        Convert raw PNG bytes → float32 RGB [H, W, 3] in [0.0, 1.0].

        NASA GIBS returns TRANSPARENT=TRUE PNGs. Converting directly to RGB
        makes all transparent pixels black (0,0,0), causing every frame to fail
        the HIGH_BLACK_RATIO validation check.

        Fix: open as RGBA, composite onto a neutral mid-grey background so that
        transparent areas become 0.5 (not 0.0), then convert to RGB.
        """
        img = Image.open(io.BytesIO(raw_bytes))
        if img.mode == "RGBA":
            # Composite on a neutral grey (128,128,128) so transparent = 0.5
            background = Image.new("RGBA", img.size, (128, 128, 128, 255))
            background.paste(img, mask=img.split()[3])  # 3 = alpha channel
            img = background.convert("RGB")
        else:
            img = img.convert("RGB")
        arr = np.array(img, dtype=np.float32) / 255.0
        return arr

    def _validate_frame(self, arr: np.ndarray, layer_id: str) -> tuple[bool, list[str]]:
        """
        Rule DQ-01: Input Validation Gate.
        Returns (is_valid, list_of_flags).

        After compositing transparent PNGs onto a grey background, per-pixel
        values in transparent areas are ~0.5 (grey). So we check for near-black
        (< 0.05) which represents genuinely missing/empty data regions, AND we
        now require a very high ratio (> 90%) before rejecting, to allow for
        satellite disk images which have black edges.
        """
        flags: list[str] = []

        # Black pixel check: > 90% near-black = empty / missing tile
        black_mask = np.all(arr < 0.05, axis=-1)
        black_ratio = float(black_mask.mean())
        if black_ratio > 0.90:
            flags.append(f"HIGH_BLACK_RATIO:{black_ratio:.2f}")
            return False, flags

        # Sensor noise check (unusually high std dev)
        per_channel_std = arr.std(axis=(0, 1))
        if float(per_channel_std.max()) > 0.45:
            flags.append(f"HIGH_NOISE_STD:{per_channel_std.max():.3f}")

        # Fill/saturated pixel check (> 20% fully saturated)
        saturated = np.all(arr > 0.98, axis=-1)
        if float(saturated.mean()) > 0.20:
            flags.append("HIGH_SATURATION")

        return True, flags

    async def fetch_frame(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int = 1024,
    ) -> SatelliteFrame:
        """Fetch a single satellite frame for the given parameters."""
        if layer_id not in GIBS_LAYERS and layer_id not in BHUVAN_LAYERS and layer_id not in INSAT_LAYERS:
            raise WMSClientError(f"Unknown layer: {layer_id}")

        params = self._build_request_params(layer_id, bbox, timestamp, resolution)
        cache_key = self._cache_key(params)

        if cache_key in self._cache:
            logger.debug("Cache hit", cache_key=cache_key[:8])
            return self._cache[cache_key]

        logger.info("Fetching WMS frame", layer=layer_id, time=timestamp.isoformat())
        raw = await self._fetch_with_retry(params)
        arr = self._decode_image(raw)
        img_hash = hashlib.md5(raw, usedforsecurity=False).hexdigest()

        is_valid, flags = self._validate_frame(arr, layer_id)
        if not is_valid:
            logger.warning(
                "Frame validation FAILED (DQ-01) — FAIL LOUDLY",
                layer=layer_id,
                timestamp=timestamp.isoformat(),
                flags=flags,
            )

        frame = SatelliteFrame(
            timestamp=timestamp,
            layer_id=layer_id,
            bbox=bbox,
            resolution=resolution,
            image=arr,
            image_hash=img_hash,
            is_valid=is_valid,
            validation_flags=flags,
        )
        self._cache[cache_key] = frame
        return frame

    async def fetch_sequence(
        self,
        layer_id: str,
        bbox: list[float],
        timestamps: list[datetime],
        resolution: int = 1024,
    ) -> list[SatelliteFrame]:
        """Fetch a sequence of frames, filtering invalid ones."""
        if len(timestamps) > self.settings.max_frames_per_session:
            raise WMSClientError(
                f"Too many frames requested: {len(timestamps)} > {self.settings.max_frames_per_session}"
            )

        frames: list[SatelliteFrame] = []
        last_valid_arr: np.ndarray | None = None

        for ts in timestamps:
            frame = await self.fetch_frame(layer_id, bbox, ts, resolution)

            if not frame.is_valid:
                logger.warning(
                    "Discarding invalid frame",
                    timestamp=ts.isoformat(),
                    flags=frame.validation_flags,
                )
                continue

            # Perceptual deduplication: skip if pixel content is near-identical
            # to the previous valid frame (MAD < 0.002 = < 0.2% difference)
            if last_valid_arr is not None:
                mad = float(np.abs(frame.image - last_valid_arr).mean())
                if mad < 0.002:
                    logger.info(
                        "Skipping perceptual duplicate frame",
                        timestamp=ts.isoformat(),
                        mad=round(mad, 5),
                    )
                    continue

            last_valid_arr = frame.image
            frames.append(frame)

        return frames


    @staticmethod
    def get_layer_info(data_source: str | None = None) -> list[dict]:
        """Return curated layer metadata for the UI (NASA + ISRO)."""
        all_layers = []

        # Add NASA layers
        if not data_source or data_source == "nasa_gibs":
            for lid, info in GIBS_LAYERS.items():
                all_layers.append({
                    "layer_id": lid,
                    "data_source": "nasa_gibs",
                    "crs": "EPSG:4326",
                    **info,
                })

        # Add Bhuvan layers
        if not data_source or data_source == "isro_bhuvan":
            for lid, info in BHUVAN_LAYERS.items():
                all_layers.append({
                    "layer_id": lid,
                    "data_source": "isro_bhuvan",
                    "crs": "EPSG:4326",
                    **info,
                })

        # Add INSAT layers
        if not data_source or data_source == "insat":
            for lid, info in INSAT_LAYERS.items():
                all_layers.append({
                    "layer_id": lid,
                    "data_source": "insat",
                    "crs": "EPSG:4326",
                    **info,
                })

        return all_layers


class BhuvanClient(NASAGIBSClient):
    """
    Async ISRO Bhuvan WMS client.
    Extends NASAGIBSClient with Bhuvan-specific endpoint and layer handling.
    """

    def __init__(self) -> None:
        super().__init__()
        self.base_url = self.settings.bhuvan_wms_url

    async def _fetch_with_retry(self, params: dict) -> bytes:
        """Override to use Bhuvan base URL."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.settings.wms_max_retries + 1):
            try:
                await self._rate_limit()
                resp = await self._client.get(
                    self.base_url,
                    params=params,
                )
                resp.raise_for_status()
                if resp.headers.get("content-type", "").startswith("application/vnd.ogc"):
                    raise WMSClientError(f"Bhuvan WMS service exception: {resp.text[:200]}")
                return resp.content
            except (httpx.HTTPError, WMSClientError) as exc:
                last_exc = exc
                wait = 2 ** attempt
                if attempt < self.settings.wms_max_retries:
                    await asyncio.sleep(wait)

        raise WMSClientError(
            f"Bhuvan WMS request failed after {self.settings.wms_max_retries} attempts: {last_exc}"
        )

class INSATClient(NASAGIBSClient):
    """
    Async MOSDAC WMS client for INSAT-3D / INSAT-3DR imagery.

    Fetches half-hourly satellite composites from the MOSDAC live WMS endpoint
    (https://mosdac.gov.in/live/wms) operated by ISRO/SAC.

    The WMS layer name is the canonical INSAT_LAYERS key (e.g. "INSAT3D_VIS",
    "INSAT3DR_TIR1") — these match MOSDAC's own LAYERS= parameter values
    directly, so no extra translation table is needed.

    Auth: set MOSDAC_API_KEY in the environment / .env file if a bearer token
    is required (public endpoint works without a key for standard resolution).

    References:
        https://mosdac.gov.in/
        ISRO INSAT-3D/3DR Imager User Guide (SAC, 2014/2016)
    """

    def __init__(self) -> None:
        super().__init__()
        # ── CRITICAL FIX: use MOSDAC endpoint, NOT nasa_gibs_base_url ──────
        self.base_url = self.settings.mosdac_wms_url   # 'https://mosdac.gov.in/live/wms'

    def _build_request_params(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
    ) -> dict[str, str]:
        """
        Build WMS 1.1.1 GetMap params for the MOSDAC endpoint.

        layer_id must be a key in INSAT_LAYERS (e.g. "INSAT3D_VIS").
        This value is passed directly as the LAYERS= WMS parameter because
        MOSDAC's layer names match our canonical IDs 1:1.

        BBOX convention for MOSDAC WMS 1.1.1:
            minlon,minlat,maxlon,maxlat  (EPSG:4326, same as GIBS)
        """
        if layer_id not in INSAT_LAYERS:
            raise WMSClientError(
                f"Unknown INSAT layer: {layer_id!r}. "
                f"Valid layers: {list(INSAT_LAYERS.keys())}"
            )
        minlon, minlat, maxlon, maxlat = bbox
        params: dict[str, str] = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetMap",
            # ── CRITICAL FIX: use the actual MOSDAC layer name, NOT Himawari ──
            "LAYERS": layer_id,
            "STYLES": "",
            "SRS": "EPSG:4326",
            "BBOX": f"{minlon},{minlat},{maxlon},{maxlat}",
            "WIDTH": str(resolution),
            "HEIGHT": str(resolution),
            "FORMAT": "image/png",
            "TRANSPARENT": "TRUE",
            # MOSDAC uses ISO 8601 UTC timestamps — round to nearest 30-min slot
            "TIME": _round_to_insat_slot(timestamp).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # Attach API key if configured (goes as a query param on MOSDAC)
        if self.settings.mosdac_api_key:
            params["key"] = self.settings.mosdac_api_key
        return params

    async def _fetch_with_retry(self, params: dict) -> bytes:
        """
        Fetch a MOSDAC WMS tile with exponential-backoff retry.

        MOSDAC returns HTTP 200 + OGC ServiceException XML when no data is
        available for a time slot (e.g. pre-launch or eclipse gap), so we
        check the content-type *and* sniff for the OGC error envelope before
        accepting the response as valid image bytes.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.settings.wms_max_retries + 1):
            try:
                await self._rate_limit()
                resp = await self._client.get(
                    self.base_url,
                    params=params,
                    follow_redirects=True,
                )
                resp.raise_for_status()

                ct = resp.headers.get("content-type", "")
                # MOSDAC can return OGC exceptions as 200 + XML/text
                if ct.startswith(("application/vnd.ogc", "text/xml", "application/xml")):
                    raise WMSClientError(
                        f"MOSDAC WMS service exception (layer={params.get('LAYERS')}, "
                        f"time={params.get('TIME')}): {resp.text[:300]}"
                    )
                if not ct.startswith("image"):
                    raise WMSClientError(
                        f"MOSDAC returned unexpected content-type {ct!r} "
                        f"(layer={params.get('LAYERS')}, time={params.get('TIME')})"
                    )
                return resp.content

            except (httpx.HTTPError, WMSClientError) as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "MOSDAC WMS request failed",
                    attempt=attempt,
                    layer=params.get("LAYERS"),
                    time=params.get("TIME"),
                    error=str(exc),
                )
                if attempt < self.settings.wms_max_retries:
                    await asyncio.sleep(wait)

        raise WMSClientError(
            f"MOSDAC WMS request failed after {self.settings.wms_max_retries} attempts "
            f"(layer={params.get('LAYERS')}, time={params.get('TIME')}): {last_exc}"
        )


def get_wms_client(data_source: str):
    """Factory to get the appropriate WMS client."""
    if data_source == "nasa_gibs":
        return NASAGIBSClient()
    elif data_source == "isro_bhuvan":
        return BhuvanClient()
    elif data_source == "insat":
        return INSATClient()
    raise ValueError(f"Unknown data source: {data_source}")
