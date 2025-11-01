from __future__ import annotations

from datetime import datetime

from flask import Blueprint, redirect, render_template, request, url_for

from . import db
from .models import Payment, Session, Student


bp = Blueprint("diary", __name__)


@bp.route("/")
def index():
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
def manage_students():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        subject = request.form.get("subject", "").strip() or None
        contact_info = request.form.get("contact_info", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        if full_name:
            student = Student(
                full_name=full_name,
                subject=subject,
                contact_info=contact_info,
                notes=notes,
            )
            db.session.add(student)
            db.session.commit()
        return redirect(url_for("diary.manage_students"))

    students = Student.query.order_by(Student.created_at.desc()).all()
    return render_template("students.html", students=students)


@bp.route("/sessions", methods=["GET", "POST"])
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
        return redirect(url_for("diary.manage_sessions"))

    sessions = Session.query.order_by(Session.date.desc(), Session.start_time.desc()).all()
    return render_template("sessions.html", sessions=sessions, students=students)


@bp.route("/payments", methods=["GET", "POST"])
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
        return redirect(url_for("diary.manage_payments"))

    payments = Payment.query.order_by(Payment.paid_on.desc()).all()
    return render_template("payments.html", payments=payments, students=students)
