"""Routes for analytics blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models.database import get_db, get_setting
from utils.auth_decorators import login_required, admin_required
import json
import datetime

bp = Blueprint("analytics", __name__)

@bp.route('/analytics')
@login_required
def analytics():
    db = get_db()
    # All claims visible to this user
    if session['role'] == 'admin':
        claims = db.execute('SELECT * FROM claims ORDER BY created_at ASC').fetchall()
    else:
        claims = db.execute('SELECT * FROM claims WHERE adjuster_id=? ORDER BY created_at ASC',
                            (session['user_id'],)).fetchall()

    total   = len(claims)
    closed  = [c for c in claims if c['status'] == 'Closed']
    open_c  = [c for c in claims if c['status'] != 'Closed']
    pipeline_val = sum(c['total_estimate'] for c in open_c)
    closed_val   = sum(c['total_estimate'] for c in closed)

    # Avg cycle time (created_at → last updated_at for closed claims)
    cycle_times = []
    for c in closed:
        try:
            start = datetime.datetime.fromisoformat(c['created_at'])
            end   = datetime.datetime.fromisoformat(c['updated_at'])
            diff  = (end - start).days
            if diff >= 0:
                cycle_times.append(diff)
        except Exception:
            pass
    avg_cycle = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None

    # Claims by month (last 12 months)
    from collections import defaultdict
    monthly = defaultdict(lambda: {'count': 0, 'value': 0.0})
    for c in claims:
        try:
            mo = c['created_at'][:7]  # 'YYYY-MM'
            monthly[mo]['count'] += 1
            monthly[mo]['value'] += c['total_estimate']
        except Exception:
            pass
    months_sorted = sorted(monthly.keys())[-12:]
    chart_labels  = months_sorted
    chart_counts  = [monthly[m]['count'] for m in months_sorted]
    chart_values  = [round(monthly[m]['value'], 2) for m in months_sorted]

    # Status breakdown
    status_counts = {
        'New':         sum(1 for c in claims if c['status'] == 'New'),
        'In Progress': sum(1 for c in claims if c['status'] == 'In Progress'),
        'Submitted':   sum(1 for c in claims if c['status'] == 'Submitted'),
        'Closed':      sum(1 for c in claims if c['status'] == 'Closed'),
    }

    # Top adjusters (admin only)
    top_adjusters = []
    if session['role'] == 'admin':
        rows = db.execute('''
            SELECT u.name, COUNT(c.id) as cnt, COALESCE(SUM(c.total_estimate),0) as total
            FROM claims c JOIN users u ON c.adjuster_id=u.id
            GROUP BY c.adjuster_id ORDER BY cnt DESC LIMIT 10
        ''').fetchall()
        top_adjusters = [dict(r) for r in rows]

    return render_template('analytics.html',
                           total=total, pipeline_val=pipeline_val, closed_val=closed_val,
                           avg_cycle=avg_cycle, status_counts=status_counts,
                           chart_labels=json.dumps(chart_labels),
                           chart_counts=json.dumps(chart_counts),
                           chart_values=json.dumps(chart_values),
                           top_adjusters=top_adjusters)


# ─────────────────────────────────────────────────────────────────────────────
# SUBMIT PACKAGE — one-click carrier submission export
# ─────────────────────────────────────────────────────────────────────────────


