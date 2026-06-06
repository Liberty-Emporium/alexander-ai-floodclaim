"""Routes for photos blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from models.database import get_db, get_setting
from utils.auth_decorators import login_required
from utils.security import allowed_file, csrf_required
from services.ai import ai_describe_photo, call_openrouter
import os
import json

bp = Blueprint("photos", __name__)

@bp.route('/claims/<int:claim_id>/photo/upload', methods=['POST'])
@login_required
@csrf_required
def upload_photo(claim_id):
    db      = get_db()
    file    = request.files.get('photo')
    room_id = request.form.get('room_id') or None
    caption = request.form.get('caption', '')
    if not file or not allowed_file(file.filename):
        flash('Invalid file type. Please upload a PNG, JPG, GIF, or WEBP.', 'error')
        return redirect(url_for('claim_detail', claim_id=claim_id))
    # ── File size check (10MB max) ──
    file.seek(0, 2)  # seek to end
    file_size = file.tell()
    file.seek(0)  # reset
    if file_size > 10 * 1024 * 1024:
        flash('File too large. Maximum size is 10MB. Please compress your image and try again.', 'error')
        return redirect(url_for('claim_detail', claim_id=claim_id))
    ext       = file.filename.rsplit('.', 1)[1].lower()
    filename  = f'{secrets.token_hex(12)}.{ext}'
    save_path = os.path.join(UPLOAD_DIR, filename)
    # ── Auto-compress large images ──
    try:
        from PIL import Image as _PILImage
        img = _PILImage.open(file)
        # Resize if max dimension > 2048px
        max_dim = max(img.size)
        if max_dim > 2048:
            scale = 2048 / max_dim
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, _PILImage.LANCZOS)
        # Convert RGBA to RGB for JPEG
        if img.mode == 'RGBA' and ext in ('jpg', 'jpeg'):
            img = img.convert('RGB')
        # Save with quality optimization
        save_kwargs = {'optimize': True}
        if ext in ('jpg', 'jpeg'):
            save_kwargs['quality'] = 80
        elif ext == 'png':
            save_kwargs['compress_level'] = 6
        img.save(save_path, **save_kwargs)
        size_kb = os.path.getsize(save_path) / 1024
        if int(file_size / 1024) > 500:
            compressed_pct = int((1 - size_kb / (file_size / 1024)) * 100)
            flash_msg = f'Photo uploaded and compressed ({compressed_pct}% smaller)'
        else:
            flash_msg = 'Photo uploaded!'
    except Exception:
        # PIL not available or error — save original
        file.save(save_path)
        flash_msg = 'Photo uploaded!'
    ai_desc = ai_describe_photo(save_path)
    db.execute(
        'INSERT INTO photos (claim_id, room_id, filename, caption, ai_description) '
        'VALUES (?,?,?,?,?)',
        (claim_id, room_id, filename, caption, ai_desc))
    db.commit()
    _log_activity(claim_id, f'Photo uploaded: {filename}')
    flash(flash_msg + (' AI analysis complete.' if ai_desc else
          ' Add an OpenRouter key in Settings to enable AI analysis.'), 'success')
    return redirect(url_for('claim_detail', claim_id=claim_id))



@bp.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)



@bp.route('/photos/<int:photo_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_photo(photo_id):
    db    = get_db()
    photo = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
    if not photo:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    # Delete the file from disk
    try:
        file_path = os.path.join(UPLOAD_DIR, photo['filename'])
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass
    db.execute('UPDATE photos SET deleted_at=CURRENT_TIMESTAMP WHERE id=?', (photo_id,))
    db.commit()
    _log_activity(photo['claim_id'], f'Photo soft-deleted: {photo["filename"]}')
    return jsonify({'ok': True})



@bp.route('/photos/<int:photo_id>/ai-description', methods=['POST'])
@login_required
def edit_ai_description(photo_id):
    """Save a manually edited AI description for a photo."""
    data = request.get_json(silent=True) or {}
    description = data.get('description', '').strip()
    db = get_db()
    db.execute('UPDATE photos SET ai_description=? WHERE id=?', (description, photo_id))
    db.commit()
    return jsonify({'ok': True})




@bp.route('/photos/<int:photo_id>/analyze', methods=['POST'])
@login_required
def analyze_photo_route(photo_id):
    db    = get_db()
    photo = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
    if not photo:
        return jsonify({'error': 'Photo not found'}), 404
    image_path = os.path.join(UPLOAD_DIR, photo['filename'])
    if not os.path.exists(image_path):
        return jsonify({'error': 'Image file not found on disk'}), 404
    desc = ai_describe_photo(image_path)
    if not desc:
        return jsonify({'error': 'AI unavailable — add an OpenRouter key in ⚙️ Settings'})
    db.execute('UPDATE photos SET ai_description=? WHERE id=?', (desc, photo_id))
    db.commit()
    return jsonify({'ok': True, 'description': desc})



@bp.route('/photos/<int:photo_id>/edit', methods=['POST'])
@login_required
@csrf_required
def edit_photo(photo_id):
    db      = get_db()
    photo   = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
    if not photo:
        flash('Photo not found.', 'error')
        return redirect(url_for('auth.dashboard'))
    caption = request.form.get('caption', '').strip()
    room_id = request.form.get('room_id') or None
    db.execute('UPDATE photos SET caption=?, room_id=? WHERE id=?',
               (caption, room_id, photo_id))
    db.commit()
    flash('Photo updated!', 'success')
    return redirect(url_for('claim_detail', claim_id=photo['claim_id']))


