from __future__ import annotations

from calendar import Calendar, monthrange
from datetime import date, datetime, timedelta
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
from .models import (
    Assignment,
    ChatMessage,
    LibraryMaterial,
    Material,
    Payment,
    Session,
    Student,
    SubjectSetting,
    Teacher,
)


bp = Blueprint("diary", __name__)

TEACHER_ROLE = "teacher"
STUDENT_ROLE = "student"

MONTH_NAMES = {
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

    today = datetime.utcnow().date()
    student_count = Student.query.count()
    session_count = Session.query.count()
    payment_total = db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0)).scalar()
    assignments_open = Assignment.query.filter(Assignment.status != "completed").count()
    upcoming_sessions = (
        Session.query.filter(Session.date >= today)
        .order_by(Session.date.asc(), Session.start_time.asc())
        .limit(5)
        .all()
    )

    target_year = request.args.get("year", type=int) or today.year
    target_month = request.args.get("month", type=int) or today.month
    if target_month < 1:
        target_month = 12
        target_year -= 1
    elif target_month > 12:
        target_month = 1
        target_year += 1

    month_label = MONTH_NAMES[target_month]
    prev_month = 12 if target_month == 1 else target_month - 1
    prev_year = target_year - 1 if target_month == 1 else target_year
    next_month = 1 if target_month == 12 else target_month + 1
    next_year = target_year + 1 if target_month == 12 else target_year

    calendar_builder = Calendar(firstweekday=0)
    calendar_month: list[list[date | None]] = []
    for week in calendar_builder.monthdatescalendar(target_year, target_month):
        calendar_month.append([day if day.month == target_month else None for day in week])

    first_day = date(target_year, target_month, 1)
    last_day = date(target_year, target_month, monthrange(target_year, target_month)[1])
    calendar_sessions = (
        Session.query.filter(Session.date >= first_day, Session.date <= last_day)
        .order_by(Session.date.asc(), Session.start_time.asc())
        .all()
    )
    sessions_by_day: dict[date, list[Session]] = {}
    for lesson in calendar_sessions:
        sessions_by_day.setdefault(lesson.date, []).append(lesson)

    assignments_due_soon = (
        Assignment.query.filter(
            Assignment.status != "completed",
            Assignment.due_date.isnot(None),
            Assignment.due_date >= today,
            Assignment.due_date <= today + timedelta(days=7),
        )
        .order_by(Assignment.due_date.asc(), Assignment.title.asc())
        .all()
    )

    thirty_days_ago = today - timedelta(days=30)
    students_needing_attention: list[dict[str, object]] = []
    for student in Student.query.order_by(Student.full_name.asc()).all():
        last_payment = (
            Payment.query.filter_by(student_id=student.id)
            .order_by(Payment.paid_on.desc())
            .first()
        )
        if not last_payment or last_payment.paid_on < thirty_days_ago:
            days_since = (today - last_payment.paid_on).days if last_payment else None
            students_needing_attention.append(
                {
                    "student": student,
                    "last_payment": last_payment,
                    "days_since": days_since,
                }
            )

    students_needing_attention = students_needing_attention[:5]
    sessions_today = (
        Session.query.filter(Session.date == today)
        .order_by(Session.start_time.asc())
        .all()
    )

    subject_counts = dict(
        db.session.query(Student.subject, db.func.count(Student.id))
        .filter(Student.subject.isnot(None))
        .group_by(Student.subject)
        .all()
    )
    session_counts = dict(
        db.session.query(Student.subject, db.func.count(Session.id))
        .join(Student, Session.student_id == Student.id)
        .filter(Student.subject.isnot(None))
        .group_by(Student.subject)
        .all()
    )
    configured_subjects = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()
    configured_names = {subject.name for subject in configured_subjects}
    subject_overview = [
        {
            "setting": subject,
            "students": subject_counts.get(subject.name, 0),
            "sessions": session_counts.get(subject.name, 0),
        }
        for subject in configured_subjects
    ]
    unmanaged_subjects = [
        {"name": name, "students": count}
        for name, count in subject_counts.items()
        if name and name not in configured_names
    ]
    library_spotlight = (
        LibraryMaterial.query.order_by(LibraryMaterial.created_at.desc()).limit(3).all()
    )

    return render_template(
        "index.html",
        student_count=student_count,
        session_count=session_count,
        payment_total=payment_total,
        upcoming_sessions=upcoming_sessions,
        assignments_open=assignments_open,
        calendar_month=calendar_month,
        month_label=month_label,
        target_year=target_year,
        target_month=target_month,
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
        sessions_by_day=sessions_by_day,
        assignments_due_soon=assignments_due_soon,
        students_needing_attention=students_needing_attention,
        sessions_today=sessions_today,
        subject_overview=subject_overview,
        unmanaged_subjects=unmanaged_subjects,
        library_spotlight=library_spotlight,
    )


@bp.route("/students", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_students():
    subject_settings = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        subject_choice = request.form.get("subject_choice")
        subject_custom = request.form.get("subject_custom", "").strip() or None
        subject = None
        if subject_choice and subject_choice != "__custom__":
            subject = subject_choice
        elif subject_choice == "__custom__":
            subject = subject_custom
        elif subject_custom:
            subject = subject_custom
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
    subject_palette = {subject.name: subject.color for subject in subject_settings}
    subject_defaults = {subject.name: subject.default_duration for subject in subject_settings}

    return render_template(
        "students.html",
        students=students,
        subject_settings=subject_settings,
        subject_palette=subject_palette,
        subject_defaults=subject_defaults,
    )


@bp.route("/subjects", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_subjects():
    subjects = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            name = request.form.get("name", "").strip()
            color = request.form.get("color", "").strip() or "#6366f1"
            default_duration_str = request.form.get("default_duration", "").strip()
            description = request.form.get("description", "").strip() or None
            if not name:
                flash("Введите название предмета.", "danger")
            elif SubjectSetting.query.filter_by(name=name).first():
                flash("Такой предмет уже добавлен.", "danger")
            else:
                try:
                    default_duration = int(default_duration_str or 60)
                except ValueError:
                    default_duration = 60
                subject = SubjectSetting(
                    name=name,
                    color=color,
                    default_duration=default_duration,
                    description=description,
                )
                db.session.add(subject)
                db.session.commit()
                flash("Настройки предмета сохранены.", "success")
        elif action == "delete":
            subject_id_raw = request.form.get("subject_id")
            subject = (
                SubjectSetting.query.get(int(subject_id_raw))
                if subject_id_raw and subject_id_raw.isdigit()
                else None
            )
            if subject:
                db.session.delete(subject)
                db.session.commit()
                flash("Предмет удалён.", "info")
        elif action == "update":
            subject_id_raw = request.form.get("subject_id")
            subject = (
                SubjectSetting.query.get(int(subject_id_raw))
                if subject_id_raw and subject_id_raw.isdigit()
                else None
            )
            if subject:
                subject.color = request.form.get("color", subject.color)
                description = request.form.get("description", "").strip() or None
                subject.description = description
                duration_str = request.form.get("default_duration", "").strip()
                if duration_str:
                    try:
                        subject.default_duration = int(duration_str)
                    except ValueError:
                        pass
                db.session.commit()
                flash("Настройки обновлены.", "success")
        return redirect(url_for("diary.manage_subjects"))

    student_counts = dict(
        db.session.query(Student.subject, db.func.count(Student.id))
        .filter(Student.subject.isnot(None))
        .group_by(Student.subject)
        .all()
    )

    return render_template(
        "subjects.html",
        subjects=subjects,
        student_counts=student_counts,
    )


@bp.route("/library", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_library():
    subjects = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            title = request.form.get("title", "").strip()
            url_value = request.form.get("url", "").strip() or None
            description = request.form.get("description", "").strip() or None
            tags = request.form.get("tags", "").strip() or None
            subject_id_raw = request.form.get("subject_id")
            subject_id = int(subject_id_raw) if subject_id_raw and subject_id_raw.isdigit() else None
            if not title:
                flash("Укажите название материала.", "danger")
            else:
                item = LibraryMaterial(
                    title=title,
                    url=url_value,
                    description=description,
                    tags=tags,
                    subject_id=subject_id,
                )
                db.session.add(item)
                db.session.commit()
                flash("Материал добавлен в библиотеку.", "success")
        elif action == "delete":
            item_id_raw = request.form.get("item_id")
            item = (
                LibraryMaterial.query.get(int(item_id_raw))
                if item_id_raw and item_id_raw.isdigit()
                else None
            )
            if item:
                db.session.delete(item)
                db.session.commit()
                flash("Материал удалён из библиотеки.", "info")
        elif action == "update":
            item_id_raw = request.form.get("item_id")
            item = (
                LibraryMaterial.query.get(int(item_id_raw))
                if item_id_raw and item_id_raw.isdigit()
                else None
            )
            if item:
                item.title = request.form.get("title", item.title).strip() or item.title
                item.url = request.form.get("url", "").strip() or item.url
                item.description = request.form.get("description", "").strip() or None
                item.tags = request.form.get("tags", "").strip() or None
                subject_id_raw = request.form.get("subject_id")
                item.subject_id = (
                    int(subject_id_raw)
                    if subject_id_raw and subject_id_raw.isdigit()
                    else None
                )
                db.session.commit()
                flash("Материал обновлён.", "success")
        return redirect(url_for("diary.manage_library"))

    subject_filter = request.args.get("subject")
    query = LibraryMaterial.query.order_by(LibraryMaterial.created_at.desc())
    if subject_filter == "unassigned":
        query = query.filter(LibraryMaterial.subject_id.is_(None))
    elif subject_filter:
        query = query.join(SubjectSetting).filter(SubjectSetting.name == subject_filter)
    library_items = query.all()

    return render_template(
        "library.html",
        subjects=subjects,
        library_items=library_items,
        subject_filter=subject_filter,
    )


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
        elif action == "attach_library_material":
            library_material_id = request.form.get("library_material_id")
            if library_material_id and library_material_id.isdigit():
                library_item = LibraryMaterial.query.get(int(library_material_id))
                if library_item:
                    material = Material(
                        student_id=student.id,
                        title=library_item.title,
                        url=library_item.url,
                        notes=library_item.description,
                    )
                    db.session.add(material)
                    db.session.commit()
                    flash("Материал добавлен из библиотеки.", "success")
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

    subject_profile = (
        SubjectSetting.query.filter_by(name=student.subject).first() if student.subject else None
    )
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

    suggested_library_items = []
    if student.subject:
        suggested_library_items = (
            LibraryMaterial.query.join(
                SubjectSetting, LibraryMaterial.subject_setting, isouter=True
            )
            .filter(SubjectSetting.name == student.subject)
            .order_by(LibraryMaterial.created_at.desc())
            .limit(5)
            .all()
        )
    if not suggested_library_items:
        suggested_library_items = (
            LibraryMaterial.query.order_by(LibraryMaterial.created_at.desc()).limit(5).all()
        )

    return render_template(
        "student_detail.html",
        student=student,
        assignments=assignments,
        materials=materials,
        messages=messages,
        sessions=sessions,
        payments=payments,
        subject_profile=subject_profile,
        library_items=suggested_library_items,
    )


@bp.route("/sessions", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_sessions():
    students = Student.query.order_by(Student.full_name.asc()).all()
    subject_defaults = {
        subject.name: subject.default_duration
        for subject in SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()
    }
    subject_focus = request.args.get("subject")

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
    return render_template(
        "sessions.html",
        sessions=sessions,
        students=students,
        subject_defaults=subject_defaults,
        subject_focus=subject_focus,
    )


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
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        teacher = Teacher.query.filter_by(username=username).first()
        if teacher and teacher.check_password(password):
            session.clear()
            session["role"] = TEACHER_ROLE
            session["teacher_id"] = teacher.id
            flash("Добро пожаловать, преподаватель!", "success")
            return redirect(url_for("diary.index"))

        student = Student.query.filter_by(username=username).first()
        if student and student.check_password(password):
            session.clear()
            session["role"] = STUDENT_ROLE
            session["student_id"] = student.id
            flash("Добро пожаловать в личный кабинет!", "success")
            return redirect(url_for("diary.student_board"))

        flash("Не удалось войти: проверьте логин и пароль.", "danger")

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
    if target_month < 1:
        target_month = 12
        target_year -= 1
    elif target_month > 12:
        target_month = 1
        target_year += 1
    month_label = MONTH_NAMES[target_month]
    prev_month = 12 if target_month == 1 else target_month - 1
    prev_year = target_year - 1 if target_month == 1 else target_year
    next_month = 1 if target_month == 12 else target_month + 1
    next_year = target_year + 1 if target_month == 12 else target_year
    calendar_builder = Calendar(firstweekday=0)
    calendar_month: list[list[date | None]] = []
    for week in calendar_builder.monthdatescalendar(target_year, target_month):
        calendar_month.append([day if day.month == target_month else None for day in week])

    first_day = date(target_year, target_month, 1)
    last_day = date(target_year, target_month, monthrange(target_year, target_month)[1])
    sessions_by_day: dict[date, list[Session]] = {}
    for session_entry in student.sessions:
        if first_day <= session_entry.date <= last_day:
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
    subject_profile = (
        SubjectSetting.query.filter_by(name=student.subject).first() if student.subject else None
    )
    library_preview = []
    if student.subject:
        library_preview = (
            LibraryMaterial.query.join(
                SubjectSetting, LibraryMaterial.subject_setting, isouter=True
            )
            .filter(SubjectSetting.name == student.subject)
            .order_by(LibraryMaterial.created_at.desc())
            .limit(3)
            .all()
        )
    if not library_preview:
        library_preview = (
            LibraryMaterial.query.order_by(LibraryMaterial.created_at.desc()).limit(3).all()
        )

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
        subject_profile=subject_profile,
        library_preview=library_preview,
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
    library_preview = []
    if student.subject:
        library_preview = (
            LibraryMaterial.query.join(
                SubjectSetting, LibraryMaterial.subject_setting, isouter=True
            )
            .filter(SubjectSetting.name == student.subject)
            .order_by(LibraryMaterial.created_at.desc())
            .limit(4)
            .all()
        )
    if not library_preview:
        library_preview = (
            LibraryMaterial.query.order_by(LibraryMaterial.created_at.desc()).limit(4).all()
        )
    return render_template(
        "student_materials.html",
        student=student,
        materials=materials,
        library_preview=library_preview,
    )


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
