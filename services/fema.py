"""FEMA service — flood zone lookup.

Extracted from app.py Phase 2 (lines 648-673).
"""
import os
import sqlite3

import requests as _req


def _get_setting(key, default=''):
    DB_PATH = os.environ.get('DB_PATH') or '/data/floodclaims.db'
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def lookup_fema_flood_zone(address):
    """Look up FEMA flood zone for an address using FEMA's free API."""
    try:
        # Geocode address via Census Bureau (free, no key)
        geo_url = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress'
        r = _req.get(geo_url, params={'address': address, 'benchmark': 'Public_AR_Current', 'format': 'json'}, timeout=8)
        matches = r.json().get('result', {}).get('addressMatches', [])
        if not matches:
            return {}
        lat = matches[0]['coordinates']['y']
        lng = matches[0]['coordinates']['x']
        # FEMA flood zone via NFHL API
        fema_url = 'https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query'
        fr = _req.get(fema_url, params={
            'geometry': f'{lng},{lat}', 'geometryType': 'esriGeometryPoint',
            'inSR': '4326', 'spatialRel': 'esriSpatialRelIntersects',
            'outFields': 'FLD_ZONE,DFIRM_ID', 'returnGeometry': 'false', 'f': 'json'
        }, timeout=8)
        features = fr.json().get('features', [])
        zone = features[0]['attributes']['FLD_ZONE'] if features else 'Unknown'
        map_num = features[0]['attributes']['DFIRM_ID'] if features else ''
        maps_url = f'https://www.google.com/maps/embed/v1/place?key=***&q={lat},{lng}&zoom=15'
        return {'lat': lat, 'lng': lng, 'flood_zone': zone, 'fema_map_number': map_num, 'maps_embed_url': maps_url}
    except Exception as e:
        print(f'FEMA lookup error: {e}')
        return {}
