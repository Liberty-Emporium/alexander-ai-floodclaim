"""Claims service — claim number generation and recalculation.

Extracted from app.py Phase 2 (lines 739-755).
"""
import datetime
import secrets


def gen_claim_number():
    """Generate a unique claim number with date prefix."""
    prefix = datetime.datetime.now().strftime('%Y%m')
    suffix = secrets.token_hex(3).upper()
    return f'FC-{prefix}-{suffix}'


def recalc_claim(claim_id, get_db_func):
    """Recalculate claim total from all room line items."""
    db = get_db_func()
    rooms = db.execute('SELECT id FROM rooms WHERE claim_id=? AND deleted_at IS NULL', (claim_id,)).fetchall()
    total = 0
    for room in rooms:
        rt = db.execute('SELECT COALESCE(SUM(total),0) as s FROM line_items WHERE room_id=? AND deleted_at IS NULL',
                        (room['id'],)).fetchone()['s']
        db.execute('UPDATE rooms SET subtotal=? WHERE id=?', (rt, room['id']))
        total += rt
    db.execute('UPDATE claims SET total_estimate=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (total, claim_id))
    db.commit()
