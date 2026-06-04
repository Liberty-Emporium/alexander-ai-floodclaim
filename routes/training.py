"""Routes for training blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import get_db, get_setting
from utils.auth_decorators import login_required, admin_required
import json
import datetime
import os

bp = Blueprint("training", __name__)

def _generate_practice_questions(db):
    """Generate 20 random practice questions covering flood adjustment knowledge."""
    import random
    question_pool = [
        # Water Categories & Classes
        {"q": "What is Water Category 1?", "options": ["Clean water from a broken pipe", "Grey water from a washing machine", "Black water from sewage", "Salt water from the ocean"], "answer": "A", "topic": "Water Categories"},
        {"q": "What is Water Category 3 also known as?", "options": ["Clean water", "Grey water", "Black water / Grossly contaminated", "Mineral water"], "answer": "C", "topic": "Water Categories"},
        {"q": "Which water class affects only the floor area of a room?", "options": ["Class 1", "Class 2", "Class 3", "Class 4"], "answer": "A", "topic": "Water Classes"},
        {"q": "What does Water Class 4 indicate?", "options": ["Only the floor is wet", "Walls are affected up to 24 inches", "The entire room is saturated", "Specialty drying for hardwood, concrete, or plaster"], "answer": "D", "topic": "Water Classes"},
        {"q": "Water from a toilet overflow with urine is classified as which category?", "options": ["Category 1", "Category 2", "Category 3", "Category 0"], "answer": "B", "topic": "Water Categories"},
        # NFIP / FEMA Knowledge
        {"q": "What is the maximum structure coverage for residential NFIP?", "options": ["$100,000", "$250,000", "$500,000", "$1,000,000"], "answer": "B", "topic": "NFIP"},
        {"q": "How long is the NFIP waiting period before a new policy takes effect?", "options": ["24 hours", "7 days", "30 days", "90 days"], "answer": "C", "topic": "NFIP"},
        {"q": "What is ICC coverage in an NFIP policy?", "options": ["Interstate Commerce Coverage", "Increased Cost of Compliance", "Insurance Claim Compensation", "International Claims Coverage"], "answer": "B", "topic": "NFIP"},
        {"q": "How long does a NFIP policyholder have to file a Proof of Loss?", "options": ["30 days", "60 days", "90 days", "1 year"], "answer": "B", "topic": "NFIP"},
        {"q": "What is a Preferred Risk Policy (PRP)?", "options": ["The most expensive flood policy", "A lower-cost policy for moderate-to-low risk zones", "A policy for commercial buildings only", "A temporary policy"], "answer": "B", "topic": "NFIP"},
        # Flood Damage Assessment
        {"q": "Within how many hours can mold start growing after water intrusion?", "options": ["2-4 hours", "6-12 hours", "24-48 hours", "7 days"], "answer": "C", "topic": "Damage Assessment"},
        {"q": "What is the first thing an adjuster should do upon arriving at a flood-damaged property?", "options": ["Start documenting with photos", "Begin water extraction", "Remove drywall", "Set up drying equipment"], "answer": "A", "topic": "Damage Assessment"},
        {"q": "What does 'wicking' refer to in flood damage?", "options": ["Water evaporating from surfaces", "Water being drawn upward into walls and materials", "Water being pumped out of a basement", "Water changing from category to category"], "answer": "B", "topic": "Damage Assessment"},
        {"q": "Which material is MOST likely to be salvageable after Category 1 water damage?", "options": ["Drywall", "Fiberglass insulation", "Concrete block", "Carpet padding"], "answer": "C", "topic": "Damage Assessment"},
        {"q": "What document must be signed by the policyholder to finalize an NFIP claim payment?", "options": ["A contractor estimate", "A Proof of Loss form", "A police report", "A home inspection report"], "answer": "B", "topic": "Damage Assessment"},
        # Adjuster Licensing
        {"q": "Which organization provides adjuster licensing in most states?", "options": ["FEMA", "State Department of Insurance", "NFIP", "Department of Housing"], "answer": "B", "topic": "Licensing"},
        {"q": "What is a WYO company?", "options": ["A company that writes flood insurance policies through NFIP", "A FEMA emergency response team", "A state licensing board", "A restoration contractor association"], "answer": "A", "topic": "Licensing"},
        {"q": "An independent adjuster typically works for:", "options": ["One specific insurance company", "Multiple insurance companies on a contract basis", "FEMA directly", "The state government"], "answer": "B", "topic": "Licensing"},
        # FloodClaims Pro Platform
        {"q": "What AI assistant is built into FloodClaims Pro?", "options": ["FloodBot", "Aquila", "ClaimMaster", "AdjusterAI"], "answer": "B", "topic": "Platform"},
        {"q": "What feature does FloodClaims Pro use to analyze damage photos?", "options": ["Manual sketching", "Photo-to-Claim AI analysis", "Video recording only", "Handwritten notes"], "answer": "B", "topic": "Platform"},
        # Safety & Standards
        {"q": "What PPE should be worn in a Category 3 water damage environment?", "options": ["No special equipment needed", "Gloves only", "Full PPE including respirator, gloves, and waterproof suit", "Hard hat only"], "answer": "C", "topic": "Safety"},
        {"q": "What is the primary purpose of an elevation certificate?", "options": ["To prove ownership", "To determine flood insurance rates and building compliance", "To file a tax deduction", "To apply for a building permit"], "answer": "B", "topic": "Safety"},
        # Claims Process
        {"q": "What is the first step in the insurance claims process after a flood?", "options": ["Hire a contractor", "File the claim with the insurance company", "Begin repairs", "Throw away damaged items"], "answer": "B", "topic": "Claims Process"},
        {"q": "Which zone is considered the highest coastal flood risk?", "options": ["Zone A", "Zone AE", "Zone V", "Zone X"], "answer": "C", "topic": "Claims Process"},
    ]
    # Pick 20 random questions each time
    selected = random.sample(question_pool, min(20, len(question_pool)))
    for i, q in enumerate(selected):
        q['id'] = i + 1
        # Shuffle options but track correct answer
        opts = list(zip(['A', 'B', 'C', 'D'], q['options']))
        random.shuffle(opts)
        q['options'] = [o[1] for o in opts]
        for j, (letter, text) in enumerate(opts):
            if letter == q['answer']:
                q['answer'] = ['A', 'B', 'C', 'D'][j]
                break
    return selected




@bp.route('/become-an-agent', methods=['GET'])
def become_an_agent():
    """Public landing page for becoming a flood adjuster."""
    db = get_db()
    modules = db.execute(
        'SELECT * FROM training_modules WHERE is_active=1 ORDER BY sort_order, module_num'
    ).fetchall()
    return render_template('become_agent.html', modules=modules)




@bp.route('/training/<slug>', methods=['GET'])
def training_module(slug):
    """View a single training module."""
    db = get_db()
    module = db.execute(
        'SELECT * FROM training_modules WHERE slug=? AND is_active=1', (slug,)
    ).fetchone()
    if not module:
        flash('Training module not found.', 'error')
        return redirect(url_for('training.become_an_agent'))
    return render_template('training_module.html', module=module)




@bp.route('/practice-exam', methods=['GET', 'POST'])
def practice_exam():
    """Practice exam — AI generates random questions each time."""
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        if not name or not email:
            flash('Please enter your name and email to start the exam.', 'error')
            return redirect(url_for('training.practice_exam'))
        # Generate exam questions via AI
        questions = _generate_practice_questions(db)
        token = secrets.token_urlsafe(16)
        db.execute(
            'INSERT INTO exam_sessions (candidate_name, candidate_email, session_token, questions_json, total_questions, is_practice) VALUES (?,?,?,?,?,1)',
            (name, email, token, json.dumps(questions), len(questions))
        )
        db.commit()
        return redirect(url_for('practice_exam_take', token=token))
    return render_template('practice_exam_start.html')




@bp.route('/practice-exam/<token>', methods=['GET', 'POST'])
def practice_exam_take(token):
    """Take a practice exam."""
    db = get_db()
    session = db.execute('SELECT * FROM exam_sessions WHERE session_token=? AND is_practice=1', (token,)).fetchone()
    if not session:
        flash('Exam session not found or expired.', 'error')
        return redirect(url_for('training.practice_exam'))
    questions = json.loads(session['questions_json'])
    if request.method == 'POST':
        answers = {}
        for q in questions:
            qid = str(q['id'])
            answers[qid] = request.form.get(qid, '')
        score = 0
        for q in questions:
            if answers.get(str(q['id']), '').lower() == q['answer'].lower():
                score += 1
        pct = int(score / len(questions) * 100) if questions else 0
        db.execute(
            'UPDATE exam_sessions SET answers_json=?, score=?, is_completed=1, completed_at=CURRENT_TIMESTAMP WHERE session_token=?',
            (json.dumps(answers), pct, token)
        )
        db.commit()
        return render_template('practice_exam_results.html', score=pct, total=len(questions), correct=score, questions=questions, answers=answers)
    return render_template('practice_exam_take.html', questions=questions, token=token, name=session['candidate_name'])




@bp.route('/apply-adjuster', methods=['GET', 'POST'])
def apply_adjuster():
    """Public application form for prospective adjusters."""
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        state = request.form.get('state', '').strip()
        licensed = 1 if request.form.get('licensed') else 0
        license_number = request.form.get('license_number', '').strip()
        exam_score = request.form.get('exam_score', '').strip()
        if not name or not email or not state:
            flash('Name, email, and state are required.', 'error')
            return redirect(url_for('training.apply_adjuster'))
        try:
            db.execute(
                'INSERT INTO adjuster_applications_v2 (name, email, phone, state, licensed, license_number, exam_score, status) VALUES (?,?,?,?,?,?,?,?)',
                (name, email, phone, state, licensed, license_number, int(exam_score) if exam_score else None, 'interested')
            )
            db.commit()
            flash('Application submitted! We\'ll be in touch soon.', 'success')
        except Exception as e:
            flash('Error submitting application. Email may already be registered.', 'error')
        return redirect(url_for('training.become_an_agent'))
    return render_template('apply_adjuster.html')


# ═══════════════════════════════════════════════════════════════════════════════
# Admin: Recruitment Management (existing)
# ═══════════════════════════════════════════════════════════════════════════════



@bp.route('/training')
@login_required
def training_classes():
    """Browse all available training classes."""
    db = get_db()
    classes = db.execute('''
        SELECT tc.*,
               COUNT(DISTINCT tl.id) AS num_lessons,
               COUNT(DISTINCT te.id) AS num_enrolled
        FROM training_classes tc
        LEFT JOIN training_lessons tl ON tl.class_id = tc.id
        LEFT JOIN training_enrollments te ON te.class_id = tc.id AND te.payment_status='completed'
        WHERE tc.status = 'active'
        GROUP BY tc.id
        ORDER BY tc.created_at DESC
    ''').fetchall()
    return render_template('training_classes.html', classes=classes)




@bp.route('/training/<int:class_id>/enroll', methods=['POST'])
@login_required
@csrf_required
def enroll_class(class_id):
    """Enroll in a training class — free, no payment required."""
    db = get_db()
    tc = db.execute('SELECT * FROM training_classes WHERE id=? AND status=?', (class_id, 'active')).fetchone()
    if not tc:
        flash('Training class not found.', 'error')
        return redirect(url_for('training.training_classes'))
    # Check if already enrolled
    existing = db.execute('SELECT * FROM training_enrollments WHERE user_id=? AND class_id=?',
                          (session['user_id'], class_id)).fetchone()
    if existing:
        flash('You are already enrolled in this class.', 'info')
        return redirect(url_for('training_learn', enroll_id=existing['id']))
    # Free enrollment — no payment needed
    db.execute('''INSERT OR REPLACE INTO training_enrollments (user_id, class_id, payment_status)
                  VALUES (?,?,?)''',
               (session['user_id'], class_id, 'completed'))
    db.commit()
    flash('🎉 Enrolled! Start learning now.', 'success')
    return redirect(url_for('training_learn', enroll_id=db.execute('SELECT last_insert_rowid()').fetchone()[0]))




@bp.route('/training/<int:enroll_id>/learn')
@login_required
def training_learn(enroll_id):
    """View and progress through enrolled training class."""
    db = get_db()
    enrollment = db.execute('''
        SELECT te.*, tc.title AS class_title, tc.description AS class_desc
        FROM training_enrollments te
        JOIN training_classes tc ON tc.id = te.class_id
        WHERE te.id=? AND te.user_id=?
    ''', (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        flash('Enrollment not found.', 'error')
        return redirect(url_for('training.training_classes'))
    lessons = db.execute('SELECT * FROM training_lessons WHERE class_id=? ORDER BY lesson_order', (enrollment['class_id'],)).fetchall()
    progress = db.execute('SELECT lesson_id, completed FROM training_progress WHERE enrollment_id=?', (enroll_id,)).fetchall()
    completed_ids = {p['lesson_id'] for p in progress if p['completed']}
    has_exam = db.execute('SELECT COUNT(*) FROM training_exam_questions WHERE class_id=?', (enrollment['class_id'],)).fetchone()[0] > 0
    cert = db.execute('SELECT * FROM training_certificates WHERE enrollment_id=?', (enroll_id,)).fetchone()
    return render_template('training_learn.html', enrollment=enrollment, lessons=lessons,
                           completed_ids=completed_ids, has_exam=has_exam, cert=cert)




@bp.route('/training/<int:enroll_id>/lesson/<int:lesson_id>/complete', methods=['POST'])
@login_required
@csrf_required
def complete_lesson(enroll_id, lesson_id):
    """Mark a lesson as completed."""
    db = get_db()
    enrollment = db.execute('SELECT * FROM training_enrollments WHERE id=? AND user_id=?',
                            (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        return jsonify({'error': 'not_enrolled'}), 403
    db.execute('''INSERT INTO training_progress (enrollment_id, lesson_id, completed, completed_at)
                  VALUES (?,?,1,?)
                  ON CONFLICT(enrollment_id,lesson_id) DO UPDATE SET completed=1, completed_at=excluded.completed_at''',
               (enroll_id, lesson_id, datetime.datetime.now().isoformat()))
    # Recalculate progress
    total = db.execute('SELECT COUNT(*) FROM training_lessons WHERE class_id=?', (enrollment['class_id'],)).fetchone()[0]
    done = db.execute('SELECT COUNT(*) FROM training_progress WHERE enrollment_id=? AND completed=1', (enroll_id,)).fetchone()[0]
    pct = int((done / total) * 100) if total else 0
    db.execute('UPDATE training_enrollments SET progress_pct=? WHERE id=?', (pct, enroll_id))
    db.commit()
    return jsonify({'progress': pct, 'completed': True})




@bp.route('/training/<int:enroll_id>/exam')
@login_required
def training_exam(enroll_id):
    """Take the certification exam."""
    db = get_db()
    enrollment = db.execute('''
        SELECT te.*, tc.title AS class_title
        FROM training_enrollments te
        JOIN training_classes tc ON tc.id = te.class_id
        WHERE te.id=? AND te.user_id=? AND te.payment_status='completed'
    ''', (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        flash('Enrollment not found.', 'error')
        return redirect(url_for('training.training_classes'))
    questions = db.execute('SELECT * FROM training_exam_questions WHERE class_id=? ORDER BY RANDOM() LIMIT 20',
                           (enrollment['class_id'],)).fetchall()
    import random
    for q in questions:
        options = [q['option_a'], q['option_b'], q['option_c'], q['option_d']]
        random.shuffle(options)
    return render_template('training_exam.html', enrollment=enrollment, questions=questions)




@bp.route('/training/<int:enroll_id>/exam/submit', methods=['POST'])
@login_required
@csrf_required
def submit_exam(enroll_id):
    """Submit exam answers and calculate score."""
    db = get_db()
    enrollment = db.execute('SELECT * FROM training_enrollments WHERE id=? AND user_id=?',
                            (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        return jsonify({'error': 'not_enrolled'}), 403
    questions = db.execute('SELECT * FROM training_exam_questions WHERE class_id=?', (enrollment['class_id'],)).fetchall()
    score = 0
    total = len(questions) or 1
    for q in questions:
        submitted = request.form.get(f'q_{q["id"]}', '')
        if submitted.lower() == q['correct_answer'].lower():
            score += 1
    pct = int((score / total) * 100)
    passed = pct >= 80
    if passed:
        cert_id = secrets.token_hex(16)
        db.execute('''INSERT OR REPLACE INTO training_certificates (user_id, class_id, enrollment_id, score, certificate_id, issued_at)
                      VALUES (?,?,?,?,?,?)''',
                   (session['user_id'], enrollment['class_id'], enroll_id, pct, cert_id, datetime.datetime.now().isoformat()))
        db.execute('UPDATE training_enrollments SET completed_at=?, progress_pct=100 WHERE id=?',
                   (datetime.datetime.now().isoformat(), enroll_id))
        db.commit()
        return jsonify({'passed': True, 'score': pct, 'certificate_id': cert_id})
    db.commit()
    return jsonify({'passed': False, 'score': pct, 'message': 'You need 80% to pass. Review the material and try again.'})




@bp.route('/training/certificate/<cert_id>')
@login_required
def view_certificate(cert_id):
    """View a training certificate."""
    db = get_db()
    cert = db.execute('''
        SELECT tc.*, tcl.title AS class_title, u.name AS student_name, u.email AS student_email
        FROM training_certificates tc
        JOIN training_classes tcl ON tcl.id = tc.class_id
        JOIN users u ON u.id = tc.user_id
        WHERE tc.certificate_id=?
    ''', (cert_id,)).fetchone()
    if not cert:
        flash('Certificate not found.', 'error')
        return redirect(url_for('training.training_classes'))
    return render_template('training_certificate.html', cert=cert)


# ── ADMIN: Training Class Management ───────────────────────────────────────



@bp.route('/admin/training')
@login_required
def admin_training():
    """Admin: list all training classes."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    classes = db.execute('SELECT tc.*, COUNT(DISTINCT tl.id) AS num_lessons, COUNT(DISTINCT te.id) AS num_enrolled FROM training_classes tc LEFT JOIN training_lessons tl ON tl.class_id = tc.id LEFT JOIN training_enrollments te ON te.class_id = tc.id GROUP BY tc.id ORDER BY tc.created_at DESC').fetchall()
    return render_template('admin/training.html', classes=classes)




@bp.route('/admin/training/new', methods=['GET', 'POST'])
@login_required
def admin_new_class():
    """Admin: create a new training class."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    if request.method == 'POST':
        db = get_db()
        db.execute('INSERT INTO training_classes (title, description, price_cents, status) VALUES (?,?,?,?)',
                   (request.form['title'], request.form['description'], int(request.form.get('price_cents', 5000)),
                    request.form.get('status', 'active')))
        class_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        _seed_training_questions(db, class_id)
        db.commit()
        flash('✅ Training class created! Default exam questions added.', 'success')
        return redirect(url_for('training.admin_training'))
    return render_template('admin/training_edit.html', tc=None)




@bp.route('/admin/training/<int:class_id>/lessons', methods=['GET', 'POST'])
@login_required
def admin_manage_lessons(class_id):
    """Admin: manage lessons for a class."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    tc = db.execute('SELECT * FROM training_classes WHERE id=?', (class_id,)).fetchone()
    if not tc:
        abort(404)
    if request.method == 'POST':
        order = int(request.form.get('lesson_order', 0))
        db.execute('INSERT INTO training_lessons (class_id, title, content, lesson_order, video_url) VALUES (?,?,?,?,?)',
                   (class_id, request.form['title'], request.form.get('content',''), order, request.form.get('video_url','')))
        db.commit()
        flash('Lesson added.', 'success')
        return redirect(url_for('admin_manage_lessons', class_id=class_id))
    lessons = db.execute('SELECT * FROM training_lessons WHERE class_id=? ORDER BY lesson_order', (class_id,)).fetchall()
    return render_template('admin/training_lessons.html', tc=tc, lessons=lessons)




@bp.route('/admin/training/<int:class_id>/lesson/<int:lesson_id>/delete', methods=['POST'])
@login_required
def admin_delete_lesson(class_id, lesson_id):
    """Admin: delete a lesson."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    db.execute('DELETE FROM training_lessons WHERE id=? AND class_id=?', (lesson_id, class_id))
    db.commit()
    flash('Lesson deleted.', 'success')
    return redirect(url_for('admin_manage_lessons', class_id=class_id))



