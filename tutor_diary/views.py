from __future__ import annotations

from calendar import Calendar
from datetime import date, datetime
from functools import wraps

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import db
from .models import Assignment, ChatMessage, Material, Payment, Session, Student, Teacher


bp = Blueprint("diary", __name__)

TEACHER_ROLE = "teacher"
STUDENT_ROLE = "student"


def login_required(role: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("role") != role:
                flash("Пожалуйста, войдите в систему для продолжения.", "warning")
                return redirect(url_for("diary.login"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


@bp.route("/")
def index():
    role = session.get("role")
    if role == STUDENT_ROLE:
        return redirect(url_for("diary.student_board"))
    if role != TEACHER_ROLE:
        return redirect(url_for("diary.login"))

    student_count = Student.query.count()
    session_count = Session.query.count()
    payment_total = db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0)).scalar()
    assignments_open = Assignment.query.filter(Assignment.status != "completed").count()
    upcoming_sessions = (
        Session.query.filter(Session.date >= datetime.utcnow().date())
        .order_by(Session.date.asc())
        .limit(5)
        .all()
    )

    return render_template(
        "index.html",
        student_count=student_count,
        session_count=session_count,
        payment_total=payment_total,
        upcoming_sessions=upcoming_sessions,
        assignments_open=assignments_open,
    )


@bp.route("/students", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_students():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        subject = request.form.get("subject", "").strip() or None
        contact_info = request.form.get("contact_info", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        if Student.query.count() >= 10:
            flash("Достигнут лимит: можно создать не более 10 учеников.", "danger")
            return redirect(url_for("diary.manage_students"))

        if not all([full_name, username, password]):
            flash("Укажите ФИО, логин и пароль ученика.", "danger")
        elif Student.query.filter_by(username=username).first():
            flash("Ученик с таким логином уже существует.", "danger")
        else:
            student = Student(
                full_name=full_name,
                username=username,
                subject=subject,
                contact_info=contact_info,
                notes=notes,
            )
            student.set_password(password)
            db.session.add(student)
            db.session.commit()
            flash("Ученик успешно добавлен.", "success")
        return redirect(url_for("diary.manage_students"))

    students = Student.query.order_by(Student.created_at.desc()).all()
    return render_template("students.html", students=students)


@bp.route("/students/<int:student_id>", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def student_detail(student_id: int):
    student = Student.query.get_or_404(student_id)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_assignment":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip() or None
            due_date_str = request.form.get("due_date")
            if title:
                assignment = Assignment(
                    student_id=student.id,
                    title=title,
                    description=description,
                    due_date=datetime.strptime(due_date_str, "%Y-%m-%d").date()
                    if due_date_str
                    else None,
                )
                db.session.add(assignment)
                db.session.commit()
                flash("Домашнее задание добавлено.", "success")
        elif action == "add_material":
            title = request.form.get("title", "").strip()
            url = request.form.get("url", "").strip() or None
            notes = request.form.get("notes", "").strip() or None
            if title:
                material = Material(student_id=student.id, title=title, url=url, notes=notes)
                db.session.add(material)
                db.session.commit()
                flash("Материал сохранён.", "success")
        elif action == "send_message":
            content = request.form.get("content", "").strip()
            if content:
                message = ChatMessage(student_id=student.id, sender=TEACHER_ROLE, content=content)
                db.session.add(message)
                db.session.commit()
                flash("Сообщение отправлено ученику.", "success")
        elif action == "update_assignment_status":
            assignment_id = request.form.get("assignment_id")
            status = request.form.get("status")
            assignment = Assignment.query.filter_by(
                id=assignment_id, student_id=student.id
            ).first()
            if assignment and status:
                assignment.status = status
                db.session.commit()
                flash("Статус задания обновлён.", "success")
        return redirect(url_for("diary.student_detail", student_id=student.id))

    assignments = Assignment.query.filter_by(student_id=student.id).order_by(Assignment.created_at.desc()).all()
    materials = Material.query.filter_by(student_id=student.id).order_by(Material.created_at.desc()).all()
    messages = (
        ChatMessage.query.filter_by(student_id=student.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    sessions = (
        Session.query.filter_by(student_id=student.id)
        .order_by(Session.date.desc(), Session.start_time.desc())
        .all()
    )
    payments = Payment.query.filter_by(student_id=student.id).order_by(Payment.paid_on.desc()).all()

    return render_template(
        "student_detail.html",
        student=student,
        assignments=assignments,
        materials=materials,
        messages=messages,
        sessions=sessions,
        payments=payments,
    )


@bp.route("/sessions", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_sessions():
    students = Student.query.order_by(Student.full_name.asc()).all()

    if request.method == "POST":
        student_id = request.form.get("student_id")
        date_str = request.form.get("date")
        start_time_str = request.form.get("start_time")
        duration = request.form.get("duration_minutes")
        topic = request.form.get("topic", "").strip()
        homework = request.form.get("homework", "").strip() or None
        status = request.form.get("status", "scheduled")

        if student_id and date_str and start_time_str and duration and topic:
            session = Session(
                student_id=int(student_id),
                date=datetime.strptime(date_str, "%Y-%m-%d").date(),
                start_time=datetime.strptime(start_time_str, "%H:%M").time(),
                duration_minutes=int(duration),
                topic=topic,
                homework=homework,
                status=status,
            )
            db.session.add(session)
            db.session.commit()
            flash("Занятие сохранено.", "success")
        return redirect(url_for("diary.manage_sessions"))

    sessions = Session.query.order_by(Session.date.desc(), Session.start_time.desc()).all()
    return render_template("sessions.html", sessions=sessions, students=students)


@bp.route("/payments", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_payments():
    students = Student.query.order_by(Student.full_name.asc()).all()

    if request.method == "POST":
        student_id = request.form.get("student_id")
        amount = request.form.get("amount")
        paid_on_str = request.form.get("paid_on")
        method = request.form.get("method", "").strip()
        notes = request.form.get("notes", "").strip() or None

        if student_id and amount and paid_on_str and method:
            payment = Payment(
                student_id=int(student_id),
                amount=float(amount),
                paid_on=datetime.strptime(paid_on_str, "%Y-%m-%d").date(),
                method=method,
                notes=notes,
            )
            db.session.add(payment)
            db.session.commit()
            flash("Оплата сохранена.", "success")
        return redirect(url_for("diary.manage_payments"))

    payments = Payment.query.order_by(Payment.paid_on.desc()).all()
    return render_template("payments.html", payments=payments, students=students)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if role == TEACHER_ROLE:
            teacher = Teacher.query.filter_by(username=username).first()
            if teacher and teacher.check_password(password):
                session.clear()
                session["role"] = TEACHER_ROLE
                session["teacher_id"] = teacher.id
                flash("Добро пожаловать, преподаватель!", "success")
                return redirect(url_for("diary.index"))
            flash("Неверный логин или пароль преподавателя.", "danger")
        elif role == STUDENT_ROLE:
            student = Student.query.filter_by(username=username).first()
            if student and student.check_password(password):
                session.clear()
                session["role"] = STUDENT_ROLE
                session["student_id"] = student.id
                flash("Добро пожаловать в личный кабинет!", "success")
                return redirect(url_for("diary.student_board"))
            flash("Неверные данные ученика.", "danger")
        else:
            flash("Выберите тип пользователя.", "warning")

    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из системы.", "info")
    return redirect(url_for("diary.login"))


@bp.route("/student/board")
@login_required(STUDENT_ROLE)
def student_board():
    student_id = session.get("student_id")
    student = Student.query.get_or_404(student_id)

    today = datetime.utcnow().date()
    upcoming_sessions = (
        Session.query.filter(Session.student_id == student.id, Session.date >= today)
        .order_by(Session.date.asc(), Session.start_time.asc())
        .all()
    )
    past_sessions = (
        Session.query.filter(Session.student_id == student.id, Session.date < today)
        .order_by(Session.date.desc(), Session.start_time.desc())
        .all()
    )
    payments = (
        Payment.query.filter(Payment.student_id == student.id)
        .order_by(Payment.paid_on.desc())
        .all()
    )

    target_year = request.args.get("year", type=int) or today.year
    target_month = request.args.get("month", type=int) or today.month
    month_names = {
        1: "Январь",
        2: "Февраль",
        3: "Март",
        4: "Апрель",
        5: "Май",
        6: "Июнь",
        7: "Июль",
        8: "Август",
        9: "Сентябрь",
        10: "Октябрь",
        11: "Ноябрь",
        12: "Декабрь",
    }

    if target_month < 1:
        target_month = 12
        target_year -= 1
    elif target_month > 12:
        target_month = 1
        target_year += 1
    month_label = month_names[target_month]
    prev_month = 12 if target_month == 1 else target_month - 1
    prev_year = target_year - 1 if target_month == 1 else target_year
    next_month = 1 if target_month == 12 else target_month + 1
    next_year = target_year + 1 if target_month == 12 else target_year
    calendar_builder = Calendar(firstweekday=0)
    calendar_month: list[list[date | None]] = []
    for week in calendar_builder.monthdatescalendar(target_year, target_month):
        calendar_month.append([day if day.month == target_month else None for day in week])

    sessions_by_day: dict[date, list[Session]] = {}
    for session_entry in student.sessions:
        sessions_by_day.setdefault(session_entry.date, []).append(session_entry)

    assignments = (
        Assignment.query.filter_by(student_id=student.id)
        .order_by(Assignment.due_date.asc().nullslast(), Assignment.created_at.desc())
        .all()
    )
    materials = (
        Material.query.filter_by(student_id=student.id)
        .order_by(Material.created_at.desc())
        .limit(5)
        .all()
    )
    first_name = student.full_name.split()[0] if student.full_name else "ученик"

    return render_template(
        "student_dashboard.html",
        student=student,
        student_first_name=first_name,
        upcoming_sessions=upcoming_sessions,
        past_sessions=past_sessions,
        payments=payments,
        calendar_month=calendar_month,
        target_month=target_month,
        target_year=target_year,
        sessions_by_day=sessions_by_day,
        assignments=assignments,
        materials=materials,
        month_label=month_label,
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
    )


@bp.route("/student/chat", methods=["GET", "POST"])
@login_required(STUDENT_ROLE)
def student_chat():
    student_id = session.get("student_id")
    student = Student.query.get_or_404(student_id)

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            message = ChatMessage(student_id=student.id, sender=STUDENT_ROLE, content=content)
            db.session.add(message)
            db.session.commit()
            flash("Сообщение отправлено преподавателю.", "success")
        return redirect(url_for("diary.student_chat"))

    messages = (
        ChatMessage.query.filter_by(student_id=student.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return render_template("student_chat.html", student=student, messages=messages)


@bp.route("/student/homework", methods=["GET", "POST"])
@login_required(STUDENT_ROLE)
def student_homework():
    student_id = session.get("student_id")
    student = Student.query.get_or_404(student_id)

    if request.method == "POST":
        assignment_id = request.form.get("assignment_id")
        assignment = Assignment.query.filter_by(id=assignment_id, student_id=student.id).first()
        if assignment:
            assignment.status = "completed" if assignment.status != "completed" else "assigned"
            db.session.commit()
            flash("Статус задания обновлён.", "success")
        return redirect(url_for("diary.student_homework"))

    assignments = (
        Assignment.query.filter_by(student_id=student.id)
        .order_by(Assignment.due_date.asc().nullslast(), Assignment.created_at.desc())
        .all()
    )
    return render_template("student_homework.html", student=student, assignments=assignments)


@bp.route("/student/materials")
@login_required(STUDENT_ROLE)
def student_materials():
    student_id = session.get("student_id")
    student = Student.query.get_or_404(student_id)

    materials = (
        Material.query.filter_by(student_id=student.id)
        .order_by(Material.created_at.desc())
        .all()
    )
    return render_template("student_materials.html", student=student, materials=materials)


@bp.route("/api/student/<int:student_id>/calendar")
@login_required(TEACHER_ROLE)
def api_student_calendar(student_id: int):
    student = Student.query.get_or_404(student_id)
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if not year or not month:
        today = datetime.utcnow().date()
        year = today.year
        month = today.month

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    sessions = (
        Session.query.filter(
            Session.student_id == student.id,
            Session.date >= start,
            Session.date < end,
        )
        .order_by(Session.date.asc())
        .all()
    )

    return jsonify(
        {
            "student": student.full_name,
            "year": year,
            "month": month,
            "sessions": [
                {
                    "id": s.id,
                    "date": s.date.isoformat(),
                    "start_time": s.start_time.strftime("%H:%M"),
                    "duration": s.duration_minutes,
                    "topic": s.topic,
                    "status": s.status,
                }
                for s in sessions
            ],
        }
    )
