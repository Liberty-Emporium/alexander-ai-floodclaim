"""Routes for billing blueprint."""

import os

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import get_db, get_setting, set_setting
from utils.auth_decorators import login_required
from utils.security import csrf_required

bp = Blueprint("billing", __name__)

# Stripe configuration
try:
    import stripe as _stripe
    STRIPE_OK = True
except Exception:
    STRIPE_OK = False

STRIPE_PLANS = [
    {'id': 'basic', 'name': 'Basic', 'price': 49, 'price_id': '', 'features': ['Up to 50 claims/month', 'AI photo analysis', 'Client portal']},
    {'id': 'pro', 'name': 'Professional', 'price': 99, 'price_id': '', 'features': ['Unlimited claims', 'AI photo analysis', 'Client portal', 'Team management', 'Priority support']},
    {'id': 'enterprise', 'name': 'Enterprise', 'price': 249, 'price_id': '', 'features': ['Everything in Pro', 'White-label', 'API access', 'Dedicated support']},
]

@bp.route('/billing')
@login_required
def billing():
    db  = get_db()
    sub = db.execute('SELECT * FROM stripe_customers WHERE user_id=?', (session['user_id'],)).fetchone()
    stripe_pub = get_setting('stripe_publishable_key') or os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
    return render_template('billing.html', plans=STRIPE_PLANS, sub=sub, stripe_pub=stripe_pub)



@bp.route('/billing/checkout', methods=['POST'])
@login_required
@csrf_required
def billing_checkout():
    plan_id    = request.form.get('plan', 'basic')
    stripe_key = get_setting('stripe_secret_key') or os.environ.get('STRIPE_SECRET_KEY', '')
    if not stripe_key or not STRIPE_OK:
        flash('Stripe not configured — add your STRIPE_SECRET_KEY in Settings first.', 'error')
        return redirect(url_for('billing.billing'))
    try:
        _stripe.api_key = stripe_key
        plan = next((p for p in STRIPE_PLANS if p['id'] == plan_id), STRIPE_PLANS[0])
        checkout = _stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price_data': {
                'currency': 'usd',
                'product_data': {'name': f'FloodClaims Pro — {plan["name"]} Plan'},
                'unit_amount': plan['price_cents'],
                'recurring': {'interval': 'month'},
            }, 'quantity': 1}],
            mode='subscription',
            success_url=url_for('billing_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('billing', _external=True),
            customer_email=session.get('email', ''),
            metadata={'user_id': str(session['user_id']), 'plan': plan_id},
        )
        return redirect(checkout.url, code=303)
    except Exception as e:
        flash(f'Stripe error: {e}', 'error')
        return redirect(url_for('billing.billing'))



@bp.route('/billing/success')
@login_required
def billing_success():
    session_id = request.args.get('session_id', '')
    stripe_key = get_setting('stripe_secret_key') or os.environ.get('STRIPE_SECRET_KEY', '')
    if session_id and stripe_key and STRIPE_OK:
        try:
            _stripe.api_key = stripe_key
            cs = _stripe.checkout.Session.retrieve(session_id)
            plan_id = cs.get('metadata', {}).get('plan', 'basic')
            db = get_db()
            db.execute('''
                INSERT INTO stripe_customers (user_id, stripe_customer, stripe_sub_id, plan, status)
                VALUES (?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                  stripe_customer=excluded.stripe_customer,
                  stripe_sub_id=excluded.stripe_sub_id,
                  plan=excluded.plan, status=excluded.status
            ''', (session['user_id'], cs.get('customer',''), cs.get('subscription',''), plan_id, 'active'))
            db.commit()
        except Exception:
            pass
    flash('🎉 Subscription activated! Welcome to FloodClaims Pro.', 'success')
    return redirect(url_for('billing.billing'))



@bp.route('/billing/portal', methods=['POST'])
@login_required
@csrf_required
def billing_portal():
    stripe_key = get_setting('stripe_secret_key') or os.environ.get('STRIPE_SECRET_KEY', '')
    if not stripe_key or not STRIPE_OK:
        flash('Stripe not configured.', 'error')
        return redirect(url_for('billing.billing'))
    db  = get_db()
    sub = db.execute('SELECT * FROM stripe_customers WHERE user_id=?', (session['user_id'],)).fetchone()
    if not sub or not sub['stripe_customer']:
        flash('No active subscription found.', 'error')
        return redirect(url_for('billing.billing'))
    try:
        _stripe.api_key = stripe_key
        portal = _stripe.billing_portal.Session.create(
            customer=sub['stripe_customer'],
            return_url=url_for('billing', _external=True)
        )
        return redirect(portal.url, code=303)
    except Exception as e:
        flash(f'Stripe portal error: {e}', 'error')
        return redirect(url_for('billing.billing'))


