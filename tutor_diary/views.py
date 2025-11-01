from __future__ import annotations

from datetime import datetime
from functools import wraps

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import db
from .models import Payment, Session, Student, Teacher


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

    return render_template(
        "student_dashboard.html",
        student=student,
        upcoming_sessions=upcoming_sessions,
        past_sessions=past_sessions,
        payments=payments,
    )
