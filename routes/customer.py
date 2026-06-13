"""Routes for customer blueprint."""

import datetime
import os
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, current_app
from models.database import get_db
from utils.auth_decorators import login_required
from werkzeug.utils import secure_filename

bp = Blueprint("customer", __name__)


@bp.route('/customer/upload/<token>', methods=['GET'])
def customer_upload_page(token):
    """Render the customer upload page (no auth required)."""
    try:
        db = get_db()
        portal = db.execute(
            'SELECT * FROM client_portal_tokens WHERE token=?',
            (token,)
        ).fetchone()
        if not portal:
            return render_template('portal_invalid.html'), 403
        return render_template('customer_upload.html', token=token)
    except Exception:
        return 'This feature is not yet available. Please contact support.', 503


@bp.route('/customer/upload/<token>', methods=['POST'])
def customer_upload_photos(token):
    """Customer uploads photos via SMS link. No login required."""
    try:
        db = get_db()
        portal = db.execute(
            'SELECT * FROM client_portal_tokens WHERE token=?',
            (token,)
        ).fetchone()
        if not portal:
            return jsonify({'error': 'invalid_or_expired_token'}), 403

        claim_id = portal['claim_id']
        photos = request.files.getlist('photos')

        if not photos:
            return jsonify({'error': 'no_photos'}), 400

        upload_dir = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'static/uploads'), str(claim_id))
        os.makedirs(upload_dir, exist_ok=True)

        saved_count = 0
        for i, photo in enumerate(photos):
            if photo and photo.filename:
                filename = f'customer_{token[:8]}_{int(datetime.datetime.now().timestamp())}_{i}_{secure_filename(photo.filename)}'
                filepath = os.path.join(upload_dir, filename)
                photo.save(filepath)
                db.execute(
                    'INSERT INTO photos (claim_id, filename, ai_description, customer_submitted, analysis_status) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (claim_id, filename, '', 1, 'pending')
                )
                saved_count += 1

        db.commit()

        return jsonify({
            'success': True,
            'photos_uploaded': saved_count,
            'message': f'{saved_count} photos uploaded successfully'
        })
    except Exception:
        return jsonify({'error': 'upload_failed'}), 503


@bp.route('/claim/<int:claim_id>/generate-upload-link', methods=['POST'])
@login_required
def generate_upload_link(claim_id):
    """Generate a customer upload link (token + SMS)."""
    try:
        db = get_db()
        claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
        if not claim:
            return jsonify({'error': 'claim_not_found'}), 404

        token = secrets.token_urlsafe(32)
        db.execute(
            'INSERT INTO client_portal_tokens (claim_id, token, created_at) VALUES (?, ?, ?)',
            (claim_id, token, datetime.datetime.now().isoformat())
        )
        db.commit()

        upload_url = url_for('customer.customer_upload_photos', token=token, _external=True)

        return jsonify({
            'success': True,
            'upload_url': upload_url,
            'token': token,
            'sms_message': f'FloodClaims Pro: Upload photos of your damage here: {upload_url}'
        })
    except Exception:
        return jsonify({'error': 'link_generation_failed'}), 503
