"""
Aquila 3D — Meshy AI Image-to-3D Integration Service
======================================================
Handles communication with Meshy AI API for generating 3D models
from flood damage photos.

API Docs: https://docs.meshy.ai/en/api/image-to-3d
"""
import os
import time
import json
import base64
import logging
import requests as _requests
from models.database import get_db, get_setting, set_setting

logger = logging.getLogger(__name__)

# ── Meshy API Config ──────────────────────────────────────────────────────────
MESHY_BASE_URL = "https://api.meshy.ai"
MESHY_VERSION = "v2"  # API v2

# Timeout constants
MESHY_POLL_INTERVAL = 5      # seconds between status polls
MESHY_MAX_POLL_ATTEMPTS = 60  # max polling attempts (5 min)


def _get_meshy_headers():
    """Build Meshy API headers with API key from settings or env."""
    api_key = get_setting('meshy_api_key') or os.environ.get('MESHY_API_KEY', '')
    if not api_key:
        raise ValueError('Meshy API key not configured. Go to Settings → API Keys → Meshy.')
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }


def _get_meshy_key_or_raise():
    """Get Meshy API key, raising a clear error if missing."""
    key = get_setting('meshy_api_key') or os.environ.get('MESHY_API_KEY', '')
    if not key:
        raise ValueError('MESHY_API_KEY not set')
    return key


# ── Health Check ──────────────────────────────────────────────────────────────

def check_meshy_key():
    """Check if the configured Meshy API key is valid."""
    try:
        key = _get_meshy_key_or_raise()
        headers = {
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
        }
        resp = _requests.get(
            f'{MESHY_BASE_URL}/v2/user/credits',
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                'ok': True,
                'credits': data.get('credits', '?'),
                'message': f"Meshy API key valid — {data.get('credits', '?')} credits remaining",
            }
        elif resp.status_code == 401:
            return {'ok': False, 'error': 'Invalid Meshy API key (401 Unauthorized)'}
        else:
            return {'ok': False, 'error': f'Meshy API error: {resp.status_code} — {resp.text[:200]}'}
    except ValueError as e:
        return {'ok': False, 'error': str(e)}
    except Exception as e:
        return {'ok': False, 'error': f'Meshy connection error: {e}'}


# ── Image-to-3D Generation ───────────────────────────────────────────────────

def image_to_3d_from_url(image_url, name=None, texture=True):
    """
    Submit an image URL for 3D model generation.

    Args:
        image_url: Publicly accessible URL of the flood damage photo
        name: Optional name for the model
        texture: Whether to generate textures (default True)

    Returns:
        dict with task_id on success
    """
    headers = _get_meshy_headers()
    payload = {
        'image_url': image_url,
        'mode': 'texture' if texture else 'no_texture',
        'name': name or 'Aquila 3D Model',
    }
    resp = _requests.post(
        f'{MESHY_BASE_URL}/v2/image-to-3d',
        headers=headers,
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f'Meshy image-to-3d failed: {resp.status_code} — {resp.text[:300]}')
    data = resp.json()
    return {
        'task_id': data.get('task_id') or data.get('id'),
        'raw': data,
    }


def image_to_3d_from_file(image_path, name=None, texture=True):
    """
    Submit a local image file for 3D model generation (base64 encoded).

    Args:
        image_path: Path to the image file on disk
        name: Optional name for the model
        texture: Whether to generate textures

    Returns:
        dict with task_id on success
    """
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lower().lstrip('.')
    mime = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}.get(ext, 'image/jpeg')
    return image_to_3d_from_base64(b64, mime, name=name, texture=texture)


def image_to_3d_from_base64(b64_data, mime_type='image/jpeg', name=None, texture=True):
    """
    Submit a base64-encoded image for 3D model generation.
    """
    headers = _get_meshy_headers()
    payload = {
        'image_base64': f'data:{mime_type};base64,{b64_data}',
        'mode': 'texture' if texture else 'no_texture',
        'name': name or 'Aquila 3D Model',
    }
    resp = _requests.post(
        f'{MESHY_BASE_URL}/v2/image-to-3d',
        headers=headers,
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f'Meshy image-to-3d failed: {resp.status_code} — {resp.text[:300]}')
    data = resp.json()
    return {
        'task_id': data.get('task_id') or data.get('id'),
        'raw': data,
    }


# ── Task Polling ──────────────────────────────────────────────────────────────

def get_task_status(task_id):
    """Check the status of a Meshy 3D generation task."""
    headers = _get_meshy_headers()
    resp = _requests.get(
        f'{MESHY_BASE_URL}/v2/image-to-3d/{task_id}',
        headers=headers,
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'Meshy task status failed: {resp.status_code} — {resp.text[:300]}')
    return resp.json()


def poll_task_completion(task_id, max_attempts=MESHY_MAX_POLL_ATTEMPTS,
                         interval=MESHY_POLL_INTERVAL, progress_callback=None):
    """
    Poll a task until it completes or fails.

    Args:
        task_id: Meshy task ID
        max_attempts: Max polling attempts
        interval: Seconds between polls
        progress_callback: Optional callable(task_data) called on each poll

    Returns:
        Final task data dict with model_url (GLB)

    Raises:
        RuntimeError on timeout or failure
    """
    for attempt in range(max_attempts):
        data = get_task_status(task_id)
        status = data.get('status', 'unknown')

        if progress_callback:
            progress_callback(data)

        if status == 'SUCCEEDED':
            return data
        elif status == 'FAILED':
            raise RuntimeError(f'Meshy task {task_id} failed: {data.get("task_error", "Unknown error")}')

        time.sleep(interval)

    raise TimeoutError(f'Meshy task {task_id} timed out after {max_attempts * interval}s')


# ── Result Extraction ─────────────────────────────────────────────────────────

def extract_model_urls(task_data):
    """Extract model file URLs from a completed task's response."""
    return {
        'glb': task_data.get('model_urls', {}).get('glb', ''),
        'obj': task_data.get('model_urls', {}).get('obj', ''),
        'fbx': task_data.get('model_urls', {}).get('fbx', ''),
        'usdz': task_data.get('model_urls', {}).get('usdz', ''),
        'thumbnail': task_data.get('thumbnail_url', ''),
        'previews': task_data.get('video_url', ''),
        'status': task_data.get('status', ''),
        'progress': task_data.get('progress', 0),
    }


# ── Database Operations ───────────────────────────────────────────────────────

def save_aquila_job(claim_id, photo_id, task_id, model_name=None):
    """Save a new Aquila 3D generation job to the database."""
    db = get_db()
    db.execute(
        '''INSERT INTO aquila_jobs
           (claim_id, photo_id, meshy_task_id, model_name, status, created_at)
           VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)''',
        (claim_id, photo_id, task_id, model_name or f'Aquila-{task_id[:8]}')
    )
    db.commit()
    return db.execute('SELECT * FROM aquila_jobs WHERE rowid = last_insert_rowid()').fetchone()


def update_aquila_job(job_id, status, model_url=None, model_data=None, error=None):
    """Update an Aquila job's status and results."""
    db = get_db()
    db.execute(
        '''UPDATE aquila_jobs SET
           status=?, model_url=?, model_data=?, error=?, updated_at=CURRENT_TIMESTAMP,
           completed_at=CASE WHEN INSTR('succeeded,failed', status) > 0 THEN CURRENT_TIMESTAMP ELSE completed_at END
           WHERE id=?''',
        (status, model_url, json.dumps(model_data) if model_data else None, error, job_id)
    )
    db.commit()


def get_aquila_jobs(claim_id):
    """Get all Aquila 3D jobs for a claim."""
    db = get_db()
    return db.execute(
        '''SELECT aj.*, p.filename as photo_filename, p.caption as photo_caption
           FROM aquila_jobs aj
           LEFT JOIN photos p ON aj.photo_id = p.id
           WHERE aj.claim_id = ?
           ORDER BY aj.created_at DESC''',
        (claim_id,)
    ).fetchall()


def get_aquila_job(job_id):
    """Get a single Aquila job by ID."""
    db = get_db()
    return db.execute('SELECT * FROM aquila_jobs WHERE id=?', (job_id,)).fetchone()


def get_aquila_job_by_meshy_task(meshy_task_id):
    """Get an Aquila job by its Meshy task ID."""
    db = get_db()
    return db.execute('SELECT * FROM aquila_jobs WHERE meshy_task_id=?', (meshy_task_id,)).fetchone()
