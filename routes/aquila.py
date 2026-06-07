"""
Aquila 3D — API Routes for FloodClaims Pro
===========================================
Endpoints for generating and viewing 3D models from flood damage photos.

Flow:
  1. User selects a photo → POST /aquila/generate
  2. Backend sends image to Meshy → returns job_id
  3. Client polls GET /aquila/status/<job_id> until complete
  4. User views model via GET /aquila/view/<job_id> (3D viewer)
"""
import json
import os
import threading
import logging
from flask import (
    Blueprint, request, jsonify, session, redirect, url_for,
    render_template, flash
)
from models.database import get_db, get_setting
from utils.auth_decorators import login_required
from utils.security import csrf_required
from services.aquila import (
    check_meshy_key, image_to_3d_from_file,
    get_task_status, extract_model_urls,
    save_aquila_job, update_aquila_job,
    get_aquila_jobs, get_aquila_job,
    MESHY_BASE_URL
)

logger = logging.getLogger(__name__)

bp = Blueprint('aquila', __name__, url_prefix='/aquila')

UPLOAD_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')


# ── Key Management ────────────────────────────────────────────────────────────

@bp.route('/key-check', methods=['GET'])
@login_required
def aquila_key_check():
    """Check if Meshy API key is configured and valid."""
    result = check_meshy_key()
    return jsonify(result)


# ── 3D Generation ────────────────────────────────────────────────────────────

@bp.route('/generate', methods=['POST'])
@login_required
@csrf_required
def aquila_generate():
    """
    Start a 3D model generation from a photo.
    Accepts photo_id (from existing photos) or a new file upload.
    Returns immediately with job_id for polling.
    """
    photo_id = request.form.get('photo_id') or request.json.get('photo_id') if request.is_json else None
    model_name = request.form.get('model_name') or 'Aquila 3D Model'

    db = get_db()

    # Get the image — either from existing photo or new upload
    if photo_id:
        photo = db.execute('SELECT * FROM photos WHERE id=? AND claim_id=?',
                           (photo_id, request.form.get('claim_id'))).fetchone()
        if not photo:
            return jsonify({'ok': False, 'error': 'Photo not found'}), 404
        image_path = os.path.join(UPLOAD_DIR, 'uploads', photo['filename'])
        claim_id = db.execute('SELECT claim_id FROM photos WHERE id=?', (photo_id,)).fetchone()['claim_id']
    else:
        # Handle new file upload
        file = request.files.get('photo')
        if not file:
            return jsonify({'ok': False, 'error': 'No photo provided'}), 400
        claim_id = request.form.get('claim_id')
        if not claim_id:
            return jsonify({'ok': False, 'error': 'claim_id required'}), 400
        # Save the file
        import secrets
        from utils.security import allowed_file
        if not allowed_file(file.filename):
            return jsonify({'ok': False, 'error': 'Invalid file type'}), 400
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f'aquila_{secrets.token_hex(12)}.{ext}'
        save_path = os.path.join(UPLOAD_DIR, 'uploads', filename)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        file.save(save_path)
        image_path = save_path

        # Insert photo record
        db.execute(
            'INSERT INTO photos (claim_id, filename, caption) VALUES (?,?,?)',
            (claim_id, filename, f'Aquila source: {model_name}')
        )
        db.commit()
        photo_id = db.execute('SELECT id FROM photos WHERE rowid = last_insert_rowid()').fetchone()['id']

    # Submit to Meshy
    try:
        result = image_to_3d_from_file(image_path, name=model_name, texture=True)
        task_id = result['task_id']
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'ok': False, 'error': str(e)}), 502
    except Exception as e:
        logger.exception('Meshy submission failed')
        return jsonify({'ok': False, 'error': f'Meshy API error: {e}'}), 500

    # Save job to database
    job = save_aquila_job(claim_id, photo_id, task_id, model_name)

    # Log activity
    try:
        actor = session.get('name', 'System')
        db.execute(
            'INSERT INTO activity_log (claim_id, actor, action) VALUES (?,?,?)',
            (claim_id, actor, f'Aquila 3D generation started: {model_name} (task: {task_id[:12]}...)')
        )
        db.commit()
    except Exception:
        pass

    return jsonify({
        'ok': True,
        'job_id': job['id'],
        'meshy_task_id': task_id,
        'status': 'pending',
        'poll_url': f'/aquila/status/{job["id"]}',
        'message': '3D model generation started. Poll for status.',
    })


@bp.route('/status/<int:job_id>', methods=['GET'])
@login_required
def aquila_status(job_id):
    """Check the status of an Aquila 3D generation job."""
    job = get_aquila_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    # If still pending, check with Meshy
    if job['status'] in ('pending', 'processing'):
        try:
            task_data = get_task_status(job['meshy_task_id'])
            status = task_data.get('status', 'PROCESSING').lower()
            progress = task_data.get('progress', 0)

            if status == 'succeeded':
                urls = extract_model_urls(task_data)
                update_aquila_job(job_id, 'succeeded', model_url=urls['glb'], model_data=urls)
                return jsonify({
                    'ok': True, 'status': 'succeeded',
                    'progress': 100,
                    'model_url': urls['glb'],
                    'thumbnail': urls['thumbnail'],
                    'viewer_url': f'/aquila/view/{job_id}',
                })
            elif status == 'failed':
                error = task_data.get('task_error', 'Unknown error')
                update_aquila_job(job_id, 'failed', error=error)
                return jsonify({'ok': False, 'status': 'failed', 'error': error})
            else:
                update_aquila_job(job_id, 'processing')
                return jsonify({
                    'ok': True, 'status': 'processing',
                    'progress': progress,
                    'message': f'Generating 3D model... {progress}%',
                })
        except Exception as e:
            logger.exception(f'Aquila status check failed for job {job_id}')
            return jsonify({'ok': True, 'status': job['status'], 'progress': 0,
                            'message': 'Checking...'})

    # Return cached status
    result = {
        'ok': True,
        'status': job['status'],
        'progress': 100 if job['status'] == 'succeeded' else 0,
    }
    if job['status'] == 'succeeded':
        result['model_url'] = job['model_url']
        result['viewer_url'] = f'/aquila/view/{job_id}'
    elif job['status'] == 'failed':
        result['error'] = job['error'] or 'Generation failed'
    return jsonify(result)


# ── 3D Viewer ────────────────────────────────────────────────────────────────

@bp.route('/view/<int:job_id>')
@login_required
def aquila_view(job_id):
    """Render the 3D viewer page for a completed model."""
    job = get_aquila_job(job_id)
    if not job:
        flash('3D model not found.', 'error')
        return redirect(url_for('auth.dashboard'))
    if job['status'] != 'succeeded':
        flash('3D model is still being generated. Check back shortly.', 'info')
        return redirect(url_for('auth.dashboard'))

    model_data = {}
    if job['model_data']:
        try:
            model_data = json.loads(job['model_data'])
        except Exception:
            pass

    return render_template(
        'aquila_viewer.html',
        job=job,
        model_url=job['model_url'],
        thumbnail=model_data.get('thumbnail', ''),
        model_name=job['model_name'] or 'Aquila 3D Model',
    )


# ── Job Listing ──────────────────────────────────────────────────────────────

@bp.route('/jobs/<int:claim_id>', methods=['GET'])
@login_required
def aquila_jobs(claim_id):
    """List all Aquila 3D jobs for a claim."""
    jobs = get_aquila_jobs(claim_id)
    return jsonify({
        'ok': True,
        'jobs': [dict(j) for j in jobs],
    })


# ── Settings ─────────────────────────────────────────────────────────────────

@bp.route('/settings', methods=['POST'])
@login_required
@csrf_required
def aquila_save_settings():
    """Save Meshy API key to settings."""
    data = request.get_json(silent=True) or {}
    api_key = data.get('meshy_api_key', '').strip()
    if not api_key:
        return jsonify({'ok': False, 'error': 'API key required'}), 400
    from models.database import set_setting
    set_setting('meshy_api_key', api_key)
    # Verify the key works
    result = check_meshy_key()
    return jsonify(result)
