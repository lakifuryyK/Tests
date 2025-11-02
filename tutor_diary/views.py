from __future__ import annotations

from calendar import Calendar, monthrange
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from sqlalchemy import or_

from . import db
from .models import (
    Admin,
    Assignment,
    AdminCommunication,
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

ADMIN_ROLE = "admin"
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

PAYMENT_STATUS_CHOICES = {
    "unpaid": "Ожидает оплаты",
    "invoiced": "Счёт отправлен",
    "paid": "Оплачено",
}


def login_required(*roles: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                flash("Пожалуйста, войдите в систему для продолжения.", "warning")
                return redirect(url_for("diary.login"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def current_teacher() -> Teacher | None:
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return None
    return Teacher.query.get(teacher_id)


def current_student() -> Student | None:
    student_id = session.get("student_id")
    if not student_id:
        return None
    return Student.query.get(student_id)


def ensure_teacher_access(student: Student) -> None:
    if session.get("role") != TEACHER_ROLE:
        abort(403)
    teacher = current_teacher()
    if not teacher or student.teacher_id != teacher.id:
        abort(403)


@bp.route("/")
def index():
    role = session.get("role")
    if role == ADMIN_ROLE:
        return redirect(url_for("diary.admin_dashboard"))
    if role == STUDENT_ROLE:
        return redirect(url_for("diary.student_board"))
    if role != TEACHER_ROLE:
        return redirect(url_for("diary.login"))

    teacher = current_teacher()
    if not teacher:
        session.clear()
        flash("Сессия истекла, войдите снова.", "warning")
        return redirect(url_for("diary.login"))

    assigned_subjects = sorted(
        teacher.subjects,
        key=lambda subject: subject.name.lower() if subject and subject.name else "",
    )
    assigned_subject_names = {subject.name for subject in assigned_subjects}

    today = datetime.utcnow().date()
    student_query = Student.query.filter_by(teacher_id=teacher.id)
    students = student_query.order_by(Student.full_name.asc()).all()
    student_count = len(students)
    limit_remaining = max(teacher.max_students - student_count, 0)
    session_query = (
        Session.query.join(Student, Session.student_id == Student.id)
        .filter(Student.teacher_id == teacher.id)
    )
    session_count = session_query.count()
    payment_total = (
        db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0))
        .join(Student, Payment.student_id == Student.id)
        .filter(Student.teacher_id == teacher.id)
        .scalar()
    )
    assignments_open = (
        Assignment.query.join(Student, Assignment.student_id == Student.id)
        .filter(Student.teacher_id == teacher.id, Assignment.status != "completed")
        .count()
    )
    upcoming_sessions = (
        session_query.filter(Session.date >= today)
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
        session_query.filter(Session.date >= first_day, Session.date <= last_day)
        .order_by(Session.date.asc(), Session.start_time.asc())
        .all()
    )
    sessions_by_day: dict[date, list[Session]] = {}
    for lesson in calendar_sessions:
        sessions_by_day.setdefault(lesson.date, []).append(lesson)

    assignments_due_soon = (
        Assignment.query.join(Student, Assignment.student_id == Student.id)
        .filter(
            Student.teacher_id == teacher.id,
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
    for student in students:
        outstanding_lessons = [
            lesson for lesson in student.sessions if lesson.payment_status != "paid"
        ]
        outstanding_total = sum(
            (lesson.fee_amount or Decimal("0")) for lesson in outstanding_lessons
        )
        last_payment = (
            Payment.query.filter_by(student_id=student.id)
            .order_by(Payment.paid_on.desc())
            .first()
        )
        days_since = (today - last_payment.paid_on).days if last_payment else None
        if outstanding_lessons or not last_payment or last_payment.paid_on < thirty_days_ago:
            students_needing_attention.append(
                {
                    "student": student,
                    "last_payment": last_payment,
                    "days_since": days_since,
                    "outstanding_count": len(outstanding_lessons),
                    "outstanding_total": outstanding_total,
                }
            )

    students_needing_attention = students_needing_attention[:5]
    outstanding_students = [
        entry for entry in students_needing_attention if entry["outstanding_count"]
    ]
    total_outstanding_value = sum(
        entry["outstanding_total"] for entry in outstanding_students
    )
    sessions_today = (
        session_query.filter(Session.date == today)
        .order_by(Session.start_time.asc())
        .all()
    )

    sessions_next_week = (
        session_query.filter(
            Session.date >= today, Session.date <= today + timedelta(days=7)
        )
        .order_by(Session.date.asc(), Session.start_time.asc())
        .all()
    )
    sessions_next_week_by_day: dict[date, list[Session]] = {}
    for lesson in sessions_next_week:
        sessions_next_week_by_day.setdefault(lesson.date, []).append(lesson)

    students_with_planned_week = {lesson.student_id for lesson in sessions_next_week}
    students_without_upcoming = [
        student for student in students if student.id not in students_with_planned_week
    ]

    assignments_overdue = (
        Assignment.query.join(Student, Assignment.student_id == Student.id)
        .filter(
            Student.teacher_id == teacher.id,
            Assignment.status != "completed",
            Assignment.due_date.isnot(None),
            Assignment.due_date < today,
        )
        .order_by(Assignment.due_date.asc())
        .all()
    )

    library_query = LibraryMaterial.query.order_by(LibraryMaterial.created_at.desc())
    if assigned_subject_names:
        library_query = (
            library_query.join(SubjectSetting, LibraryMaterial.subject_setting, isouter=True)
            .filter(
                or_(
                    LibraryMaterial.subject_id.is_(None),
                    SubjectSetting.name.in_(assigned_subject_names),
                )
            )
        )
    library_spotlight = library_query.limit(3).all()

    teacher_messages_query = (
        AdminCommunication.query.filter(
            AdminCommunication.target_role == TEACHER_ROLE,
            or_(
                AdminCommunication.is_global.is_(True),
                AdminCommunication.teacher_id == teacher.id,
            ),
        )
        .order_by(AdminCommunication.created_at.desc())
    )
    teacher_messages = teacher_messages_query.limit(6).all()
    message_deadline_window = today + timedelta(days=7)
    teacher_messages_due = [
        message
        for message in teacher_messages
        if message.due_date and today <= message.due_date <= message_deadline_window
    ]

    def format_names(items: list[Student], limit: int = 3) -> str:
        if not items:
            return ""
        names = [item.full_name for item in items[:limit]]
        remaining = len(items) - len(names)
        if remaining > 0:
            names.append(f"+ ещё {remaining}")
        return ", ".join(names)

    def pluralize_lessons(amount: int) -> str:
        if amount % 10 == 1 and amount % 100 != 11:
            return "урок"
        if 2 <= amount % 10 <= 4 and (amount % 100 < 10 or amount % 100 >= 20):
            return "урока"
        return "уроков"

    now = datetime.utcnow()
    current_hour = now.hour
    if 5 <= current_hour < 12:
        greeting = "Доброе утро"
    elif 12 <= current_hour < 18:
        greeting = "Добрый день"
    else:
        greeting = "Добрый вечер"

    focus_tone = "info"
    summary_message = "Неделя выглядит спокойно — продолжайте в выбранном ритме."
    busiest_day = None
    busiest_load = 0
    if sessions_next_week_by_day:
        busiest_day, busiest_sessions = max(
            sessions_next_week_by_day.items(), key=lambda item: len(item[1])
        )
        busiest_load = len(busiest_sessions)
        if busiest_load >= 5:
            focus_tone = "warning"
            summary_message = (
                f"{busiest_day.strftime('%d.%m')} запланировано {busiest_load} {pluralize_lessons(busiest_load)} —"
                " выделите время на отдых и подготовьте материалы заранее."
            )
        elif busiest_load >= 3:
            summary_message = (
                f"{busiest_day.strftime('%d.%m')} насыщенный день с {busiest_load} {pluralize_lessons(busiest_load)}."
                " Сгруппируйте задания по предметам, чтобы оптимизировать подготовку."
            )

    if assignments_overdue:
        focus_tone = "danger"
        overdue_students = format_names([assignment.student for assignment in assignments_overdue])
        summary_message = (
            "Есть просроченные задания — начните с "
            f"{overdue_students.split(',')[0].strip() if overdue_students else 'важных задач'}."
        )
    elif outstanding_students and focus_tone not in {"warning", "danger"}:
        focus_tone = "warning"
        highlighted = outstanding_students[0]["student"].full_name
        amount_hint = (
            f" На кону {total_outstanding_value:,.0f} ₽.".replace(",", " ")
            if total_outstanding_value
            else ""
        )
        summary_message = (
            f"У {highlighted} есть неоплаченные уроки." + amount_hint
            + " Согласуйте оплату до начала следующего занятия."
        )
    elif assignments_due_soon and focus_tone != "warning":
        focus_tone = "info"
        summary_message = (
            f"На этой неделе {len(assignments_due_soon)} дедлайн(а)."
            " Проверьте готовность домашнего задания у ключевых учеников."
        )
    elif students_needing_attention and focus_tone not in {"warning", "danger"}:
        focus_tone = "warning"
        summary_message = (
            "Есть ученики без свежих оплат — напомните им о необходимости продления занятий."
        )
    elif students_without_upcoming and focus_tone not in {"warning", "danger"}:
        summary_message = (
            "Несколько учеников пока без уроков на неделе — поставьте слоты, чтобы сохранить темп."
        )

    focus_summary = {
        "title": f"{greeting}, {teacher.username}!",
        "message": summary_message,
        "tone": focus_tone,
    }

    focus_recommendations: list[dict[str, object]] = []

    busy_days = [
        (day, len(items))
        for day, items in sorted(sessions_next_week_by_day.items())
        if len(items) >= 4
    ]
    if busy_days:
        formatted_days = ", ".join(
            f"{day.strftime('%d.%m')} — {count} {pluralize_lessons(count)}" for day, count in busy_days[:3]
        )
        focus_recommendations.append(
            {
                "icon": "⏱️",
                "title": "Сбалансируйте насыщенные дни",
                "description": (
                    f"{formatted_days}. Продумайте буферное время и материалы,"
                    " чтобы ученики сохраняли концентрацию."
                ),
                "tone": "warning",
                "link": {"url": url_for("diary.manage_sessions"), "label": "Открыть расписание"},
            }
        )

    if students_without_upcoming:
        focus_recommendations.append(
            {
                "icon": "📅",
                "title": "Запланируйте уроки для пауз",
                "description": (
                    "Без слота на ближайшие 7 дней: "
                    f"{format_names(students_without_upcoming)}."
                    " Свяжитесь и предложите время."
                ),
                "tone": "info",
                "link": {"url": url_for("diary.manage_sessions"), "label": "Назначить урок"},
            }
        )

    if assignments_overdue:
        focus_recommendations.append(
            {
                "icon": "📝",
                "title": "Верните задания в график",
                "description": (
                    "Просрочено: "
                    f"{format_names([assignment.student for assignment in assignments_overdue])}."
                    " Обсудите причины и уточните сроки."
                ),
                "tone": "danger",
                "link": {
                    "url": url_for(
                        "diary.student_detail",
                        student_id=assignments_overdue[0].student_id,
                    ),
                    "label": "Открыть карточку ученика",
                },
            }
        )

    if students_needing_attention:
        focus_recommendations.append(
            {
                "icon": "💳",
                "title": "Проконтролируйте оплаты",
                "description": (
                    "Ожидают напоминания: "
                    f"{format_names([entry['student'] for entry in students_needing_attention])}."
                    + (
                        f" Неоплачено {total_outstanding_value:,.0f} ₽.".replace(",", " ")
                        if total_outstanding_value
                        else ""
                    )
                    + " Обсудите с родителями график оплат."
                ),
                "tone": "danger" if total_outstanding_value else "warning",
                "link": {
                    "url": url_for("diary.manage_payments"),
                    "label": "Перейти к оплатам",
                },
            }
        )

    if limit_remaining > 0:
        focus_recommendations.append(
            {
                "icon": "🚀",
                "title": "Можно принять новых учеников",
                "description": (
                    f"Доступно {limit_remaining} мест(а)."
                    " Подготовьте приветственные материалы и чек-листы."
                ),
                "tone": "success",
                "link": {
                    "url": url_for("diary.manage_students"),
                    "label": "Добавить ученика",
                },
            }
        )

    if library_spotlight:
        focus_recommendations.append(
            {
                "icon": "📚",
                "title": "Поделитесь материалами недели",
                "description": (
                    "В библиотеке появились свежие ресурсы —"
                    " рекомендованный контент уже ждёт учеников."
                ),
                "tone": "info",
                "link": {
                    "url": url_for("diary.manage_library"),
                    "label": "Открыть библиотеку",
                },
            }
        )


    subject_counts = dict(
        db.session.query(Student.subject, db.func.count(Student.id))
        .filter(Student.teacher_id == teacher.id, Student.subject.isnot(None))
        .group_by(Student.subject)
        .all()
    )
    session_counts = dict(
        db.session.query(Student.subject, db.func.count(Session.id))
        .join(Student, Session.student_id == Student.id)
        .filter(Student.teacher_id == teacher.id, Student.subject.isnot(None))
        .group_by(Student.subject)
        .all()
    )
    configured_subjects = assigned_subjects
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
        focus_summary=focus_summary,
        focus_recommendations=focus_recommendations,
        subject_overview=subject_overview,
        unmanaged_subjects=unmanaged_subjects,
        library_spotlight=library_spotlight,
        student_limit=teacher.max_students,
        limit_remaining=limit_remaining,
        teacher_messages=teacher_messages,
        teacher_messages_due=teacher_messages_due,
        assigned_subjects=assigned_subjects,
    )


@bp.route("/admin/dashboard")
@login_required(ADMIN_ROLE)
def admin_dashboard():
    admin_id = session.get("admin_id")
    admin_user = Admin.query.get(admin_id) if admin_id else None
    if not admin_user:
        session.clear()
        flash("Сессия администратора истекла, войдите снова.", "warning")
        return redirect(url_for("diary.login"))

    today = datetime.utcnow().date()
    teacher_count = Teacher.query.count()
    student_count = Student.query.count()
    session_count = Session.query.count()
    payment_total = db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0)).scalar()

    teachers = Teacher.query.order_by(Teacher.created_at.desc()).all()
    teacher_stats: list[dict[str, object]] = []
    for teacher in teachers:
        assigned_count = len(teacher.students)
        teacher_stats.append(
            {
                "teacher": teacher,
                "student_count": assigned_count,
                "limit_remaining": max(teacher.max_students - assigned_count, 0),
                "over_limit": assigned_count >= teacher.max_students,
            }
        )

    sessions_next_week = (
        Session.query.filter(
            Session.date >= today,
            Session.date <= today + timedelta(days=7),
        )
        .order_by(Session.date.asc(), Session.start_time.asc())
        .limit(10)
        .all()
    )
    unassigned_students = Student.query.filter(Student.teacher_id.is_(None)).all()
    recent_messages = (
        AdminCommunication.query.order_by(AdminCommunication.created_at.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "admin_dashboard.html",
        admin=admin_user,
        teacher_count=teacher_count,
        student_count=student_count,
        session_count=session_count,
        payment_total=payment_total,
        teacher_stats=teacher_stats,
        sessions_next_week=sessions_next_week,
        unassigned_students=unassigned_students,
        recent_messages=recent_messages,
    )


@bp.route("/admin/teachers", methods=["GET", "POST"])
@login_required(ADMIN_ROLE)
def admin_manage_teachers():
    admin_id = session.get("admin_id")
    admin_user = Admin.query.get(admin_id) if admin_id else None
    if not admin_user:
        session.clear()
        flash("Сессия администратора истекла, войдите снова.", "warning")
        return redirect(url_for("diary.login"))

    subject_settings = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            max_students_str = request.form.get("max_students", "").strip()
            subject_id_values = request.form.getlist("subject_ids")
            try:
                max_students = int(max_students_str) if max_students_str else 10
            except ValueError:
                max_students = 10
            if not username or not password:
                flash("Укажите логин и пароль преподавателя.", "danger")
            elif Teacher.query.filter_by(username=username).first():
                flash("Преподаватель с таким логином уже существует.", "danger")
            else:
                subject_ids: list[int] = []
                for value in subject_id_values:
                    if not value.isdigit():
                        continue
                    subject_id = int(value)
                    if subject_id not in subject_ids:
                        subject_ids.append(subject_id)

                if not subject_ids:
                    flash("Выберите хотя бы один предмет из списка.", "danger")
                    return redirect(url_for("diary.admin_manage_teachers"))
                if len(subject_ids) > 3:
                    flash("Можно выбрать не более трёх предметов для нового преподавателя.", "danger")
                    return redirect(url_for("diary.admin_manage_teachers"))

                subjects = (
                    SubjectSetting.query.filter(SubjectSetting.id.in_(subject_ids)).all()
                    if subject_ids
                    else []
                )
                subjects_by_id = {subject.id: subject for subject in subjects if subject and subject.id}
                if len(subjects_by_id) != len(subject_ids):
                    flash("Некоторые выбранные предметы не найдены.", "danger")
                    return redirect(url_for("diary.admin_manage_teachers"))
                teacher = Teacher(
                    username=username,
                    max_students=max(1, max_students),
                    owner_id=admin_user.id,
                )
                teacher.set_password(password)
                teacher.subjects = [subjects_by_id[sid] for sid in subject_ids]
                db.session.add(teacher)
                db.session.commit()
                flash("Преподаватель создан.", "success")
        elif action == "update":
            teacher_id_raw = request.form.get("teacher_id")
            teacher = (
                Teacher.query.get(int(teacher_id_raw))
                if teacher_id_raw and teacher_id_raw.isdigit()
                else None
            )
            if not teacher:
                flash("Преподаватель не найден.", "danger")
            else:
                max_students_str = request.form.get("max_students", "").strip()
                new_password = request.form.get("password", "").strip()
                subject_id_values = request.form.getlist("subject_ids")
                if subject_id_values:
                    subject_ids: list[int] = []
                    for value in subject_id_values:
                        if not value.isdigit():
                            continue
                        subject_id = int(value)
                        if subject_id not in subject_ids:
                            subject_ids.append(subject_id)

                    if not subject_ids:
                        flash(
                            "Нужно оставить хотя бы один предмет у преподавателя.",
                            "danger",
                        )
                        return redirect(url_for("diary.admin_manage_teachers"))
                    if len(subject_ids) > 3:
                        flash(
                            "Можно выбрать не более трёх предметов для преподавателя.",
                            "danger",
                        )
                        return redirect(url_for("diary.admin_manage_teachers"))

                    subjects = (
                        SubjectSetting.query.filter(SubjectSetting.id.in_(subject_ids)).all()
                        if subject_ids
                        else []
                    )
                    subjects_by_id = {subject.id: subject for subject in subjects if subject and subject.id}
                    if len(subjects_by_id) != len(subject_ids):
                        flash(
                            "Некоторые выбранные предметы не найдены.",
                            "danger",
                        )
                        return redirect(url_for("diary.admin_manage_teachers"))
                    teacher.subjects = [subjects_by_id[sid] for sid in subject_ids]
                if max_students_str:
                    try:
                        new_limit = int(max_students_str)
                    except ValueError:
                        new_limit = teacher.max_students
                    else:
                        if new_limit < len(teacher.students):
                            flash(
                                "Нельзя установить лимит меньше текущего количества учеников.",
                                "danger",
                            )
                            return redirect(url_for("diary.admin_manage_teachers"))
                        teacher.max_students = max(1, new_limit)
                if new_password:
                    teacher.set_password(new_password)
                db.session.commit()
                flash("Настройки преподавателя обновлены.", "success")
        elif action == "delete":
            teacher_id_raw = request.form.get("teacher_id")
            teacher = (
                Teacher.query.get(int(teacher_id_raw))
                if teacher_id_raw and teacher_id_raw.isdigit()
                else None
            )
            if not teacher:
                flash("Преподаватель не найден.", "danger")
            else:
                for student in teacher.students:
                    student.teacher_id = None
                db.session.delete(teacher)
                db.session.commit()
                flash("Преподаватель удалён. Его ученики остались в системе без назначения.", "info")
        return redirect(url_for("diary.admin_manage_teachers"))

    teachers = Teacher.query.order_by(Teacher.created_at.desc()).all()
    teacher_stats = [
        {
            "teacher": teacher,
            "student_count": len(teacher.students),
            "limit": teacher.max_students,
            "subjects": sorted(
                teacher.subjects,
                key=lambda subject: subject.name.lower() if subject and subject.name else "",
            ),
        }
        for teacher in teachers
    ]

    return render_template(
        "admin_teachers.html",
        admin=admin_user,
        teacher_stats=teacher_stats,
        subject_settings=subject_settings,
    )


@bp.route("/students", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_students():
    teacher = current_teacher()
    if not teacher:
        flash("Сессия истекла, войдите снова.", "warning")
        return redirect(url_for("diary.login"))

    subject_settings = sorted(
        teacher.subjects,
        key=lambda subject: subject.name.lower() if subject and subject.name else "",
    )
    allowed_subject_names = {subject.name for subject in subject_settings}
    if not subject_settings:
        flash(
            "У преподавателя не настроены предметы. Обратитесь к администратору, чтобы выбрать направления.",
            "warning",
        )

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        subject_choice = (request.form.get("subject_choice") or "").strip()
        subject = subject_choice if subject_choice in allowed_subject_names else None
        contact_info = request.form.get("contact_info", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        current_count = Student.query.filter_by(teacher_id=teacher.id).count()
        if current_count >= teacher.max_students:
            flash(
                f"Вы достигли лимита: максимум {teacher.max_students} учеников.",
                "danger",
            )
            return redirect(url_for("diary.manage_students"))

        if not all([full_name, username, password]):
            flash("Укажите ФИО, логин и пароль ученика.", "danger")
        elif Student.query.filter_by(username=username).first():
            flash("Ученик с таким логином уже существует.", "danger")
        elif subject_choice and subject_choice not in allowed_subject_names:
            flash(
                "Этот предмет недоступен в вашем профиле. Выберите один из трёх назначенных направлений.",
                "danger",
            )
        else:
            student = Student(
                full_name=full_name,
                username=username,
                subject=subject,
                contact_info=contact_info,
                notes=notes,
                teacher_id=teacher.id,
            )
            student.set_password(password)
            db.session.add(student)
            db.session.commit()
            flash("Ученик успешно добавлен.", "success")
        return redirect(url_for("diary.manage_students"))

    students = (
        Student.query.filter_by(teacher_id=teacher.id)
        .order_by(Student.created_at.desc())
        .all()
    )
    subject_palette = {subject.name: subject.color for subject in subject_settings}
    subject_defaults = {subject.name: subject.default_duration for subject in subject_settings}
    current_count = len(students)
    limit_remaining = max(teacher.max_students - current_count, 0)

    return render_template(
        "students.html",
        students=students,
        subject_settings=subject_settings,
        subject_palette=subject_palette,
        subject_defaults=subject_defaults,
        max_students=teacher.max_students,
        teacher_options=[],
        selected_teacher_id=None,
        is_admin=False,
        selected_teacher=teacher,
        current_student_total=current_count,
        student_limit=teacher.max_students,
        limit_remaining=limit_remaining,
    )


@bp.route("/admin/students", methods=["GET", "POST"])
@login_required(ADMIN_ROLE)
def admin_manage_students():
    admin = Admin.query.get(session.get("admin_id"))
    if not admin:
        flash("Администратор не найден.", "danger")
        return redirect(url_for("diary.login"))

    subject_settings = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()
    teacher_options = Teacher.query.order_by(Teacher.username.asc()).all()
    selected_teacher_id = request.args.get("teacher_id", type=int)

    if request.method == "POST":
        action = request.form.get("action", "create")
        redirect_args: dict[str, int] = {}

        if action == "delete":
            student_id_raw = request.form.get("student_id")
            student_obj = (
                Student.query.get(int(student_id_raw))
                if student_id_raw and student_id_raw.isdigit()
                else None
            )
            if not student_obj:
                flash("Ученик не найден.", "danger")
            else:
                teacher_id = student_obj.teacher_id
                db.session.delete(student_obj)
                db.session.commit()
                if teacher_id:
                    redirect_args["teacher_id"] = teacher_id
                flash("Ученик удалён из системы.", "info")
            return redirect(url_for("diary.admin_manage_students", **redirect_args))

        if action == "reassign":
            student_id_raw = request.form.get("student_id")
            new_teacher_id_raw = request.form.get("new_teacher_id")
            student_obj = (
                Student.query.get(int(student_id_raw))
                if student_id_raw and student_id_raw.isdigit()
                else None
            )
            new_teacher = (
                Teacher.query.get(int(new_teacher_id_raw))
                if new_teacher_id_raw and new_teacher_id_raw.isdigit()
                else None
            )
            if not student_obj or not new_teacher:
                flash("Выберите ученика и преподавателя для переназначения.", "danger")
                return redirect(url_for("diary.admin_manage_students"))
            student_count = Student.query.filter_by(teacher_id=new_teacher.id).count()
            if (
                new_teacher.id != student_obj.teacher_id
                and student_count >= new_teacher.max_students
            ):
                flash(
                    "У выбранного преподавателя нет свободных мест.",
                    "danger",
                )
                redirect_args["teacher_id"] = new_teacher.id
                return redirect(url_for("diary.admin_manage_students", **redirect_args))
            student_obj.teacher_id = new_teacher.id
            db.session.commit()
            redirect_args["teacher_id"] = new_teacher.id
            flash("Ученик успешно переназначен.", "success")
            return redirect(url_for("diary.admin_manage_students", **redirect_args))

        # default to creating a new student
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        subject_choice = request.form.get("subject_choice")
        subject_custom = request.form.get("subject_custom", "").strip() or None
        contact_info = request.form.get("contact_info", "").strip() or None
        notes = request.form.get("notes", "").strip() or None
        subject = None
        if subject_choice and subject_choice != "__custom__":
            subject = subject_choice
        elif subject_choice == "__custom__":
            subject = subject_custom
        elif subject_custom:
            subject = subject_custom

        teacher_id_raw = request.form.get("assigned_teacher_id")
        assigned_teacher = (
            Teacher.query.get(int(teacher_id_raw))
            if teacher_id_raw and teacher_id_raw.isdigit()
            else None
        )
        if not assigned_teacher:
            flash("Выберите преподавателя для ученика.", "danger")
            return redirect(url_for("diary.admin_manage_students"))

        current_count = Student.query.filter_by(teacher_id=assigned_teacher.id).count()
        if current_count >= assigned_teacher.max_students:
            flash(
                f"Превышен лимит: преподаватель может вести до {assigned_teacher.max_students} учеников.",
                "danger",
            )
            return redirect(
                url_for("diary.admin_manage_students", teacher_id=assigned_teacher.id)
            )

        if not all([full_name, username, password]):
            flash("Укажите ФИО, логин и пароль ученика.", "danger")
            return redirect(url_for("diary.admin_manage_students"))
        if Student.query.filter_by(username=username).first():
            flash("Ученик с таким логином уже существует.", "danger")
            return redirect(url_for("diary.admin_manage_students"))

        student = Student(
            full_name=full_name,
            username=username,
            subject=subject,
            contact_info=contact_info,
            notes=notes,
            teacher_id=assigned_teacher.id,
        )
        student.set_password(password)
        db.session.add(student)
        db.session.commit()
        flash("Ученик успешно создан.", "success")
        return redirect(url_for("diary.admin_manage_students", teacher_id=assigned_teacher.id))

    students_query = Student.query
    selected_teacher = None
    max_students = None
    if selected_teacher_id:
        students_query = students_query.filter_by(teacher_id=selected_teacher_id)
        selected_teacher = Teacher.query.get(selected_teacher_id)
        if selected_teacher:
            max_students = selected_teacher.max_students

    students = students_query.order_by(Student.created_at.desc()).all()
    subject_palette = {subject.name: subject.color for subject in subject_settings}
    subject_defaults = {subject.name: subject.default_duration for subject in subject_settings}

    return render_template(
        "students.html",
        students=students,
        subject_settings=subject_settings,
        subject_palette=subject_palette,
        subject_defaults=subject_defaults,
        max_students=max_students,
        teacher_options=teacher_options,
        selected_teacher_id=selected_teacher_id,
        is_admin=True,
        selected_teacher=selected_teacher,
        current_student_total=len(students),
        student_limit=None,
        limit_remaining=None,
    )


@bp.route("/admin/messages", methods=["GET", "POST"])
@login_required(ADMIN_ROLE)
def admin_messages():
    admin = Admin.query.get(session.get("admin_id"))
    if not admin:
        flash("Сессия администратора истекла, войдите снова.", "warning")
        return redirect(url_for("diary.login"))

    teachers = Teacher.query.order_by(Teacher.username.asc()).all()
    students = Student.query.order_by(Student.full_name.asc()).all()
    subjects = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()

    if request.method == "POST":
        action = request.form.get("action", "broadcast_teachers")
        subject_line = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        due_date_str = request.form.get("due_date", "").strip()
        due_date_value = None
        if due_date_str:
            try:
                due_date_value = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Дата должна быть в формате ГГГГ-ММ-ДД.", "danger")
                return redirect(url_for("diary.admin_messages"))

        if not subject_line or not body:
            flash("Укажите тему и текст сообщения.", "danger")
            return redirect(url_for("diary.admin_messages"))

        if action == "broadcast_teachers":
            communication = AdminCommunication(
                admin_id=admin.id,
                target_role=TEACHER_ROLE,
                subject=subject_line,
                body=body,
                is_global=True,
                due_date=due_date_value,
            )
            db.session.add(communication)
            db.session.commit()
            flash("Сообщение отправлено всем преподавателям.", "success")
        elif action == "direct_teacher":
            teacher_id_raw = request.form.get("teacher_id")
            teacher = (
                Teacher.query.get(int(teacher_id_raw))
                if teacher_id_raw and teacher_id_raw.isdigit()
                else None
            )
            if not teacher:
                flash("Выберите преподавателя для личного сообщения.", "danger")
            else:
                db.session.add(
                    AdminCommunication(
                        admin_id=admin.id,
                        target_role=TEACHER_ROLE,
                        subject=subject_line,
                        body=body,
                        is_global=False,
                        teacher_id=teacher.id,
                        due_date=due_date_value,
                    )
                )
                db.session.commit()
                flash(f"Сообщение отправлено преподавателю {teacher.username}.", "success")
        elif action == "subject_group":
            subject_id_raw = request.form.get("subject_id")
            subject = (
                SubjectSetting.query.get(int(subject_id_raw))
                if subject_id_raw and subject_id_raw.isdigit()
                else None
            )
            if not subject:
                flash("Выберите предмет для целевой рассылки.", "danger")
            else:
                recipients = [
                    teacher
                    for teacher in teachers
                    if any(item.id == subject.id for item in teacher.subjects)
                ]
                if not recipients:
                    flash("Нет преподавателей, работающих с этим предметом.", "warning")
                else:
                    for teacher in recipients:
                        db.session.add(
                            AdminCommunication(
                                admin_id=admin.id,
                                target_role=TEACHER_ROLE,
                                subject=subject_line,
                                body=body,
                                is_global=False,
                                teacher_id=teacher.id,
                                due_date=due_date_value,
                            )
                        )
                    db.session.commit()
                    flash(
                        f"Сообщение отправлено {len(recipients)} преподавателям предмета {subject.name}.",
                        "success",
                    )
        elif action == "broadcast_students":
            db.session.add(
                AdminCommunication(
                    admin_id=admin.id,
                    target_role=STUDENT_ROLE,
                    subject=subject_line,
                    body=body,
                    is_global=True,
                    due_date=due_date_value,
                )
            )
            db.session.commit()
            flash("Сообщение отправлено всем ученикам.", "success")
        elif action == "direct_student":
            student_id_raw = request.form.get("student_id")
            student = (
                Student.query.get(int(student_id_raw))
                if student_id_raw and student_id_raw.isdigit()
                else None
            )
            if not student:
                flash("Выберите ученика для сообщения.", "danger")
            else:
                db.session.add(
                    AdminCommunication(
                        admin_id=admin.id,
                        target_role=STUDENT_ROLE,
                        subject=subject_line,
                        body=body,
                        is_global=False,
                        student_id=student.id,
                        due_date=due_date_value,
                    )
                )
                db.session.commit()
                flash(f"Сообщение отправлено ученику {student.full_name}.", "success")
        else:
            flash("Неизвестное действие.", "danger")

        return redirect(url_for("diary.admin_messages"))

    communications = (
        AdminCommunication.query.order_by(AdminCommunication.created_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "admin_messages.html",
        admin=admin,
        teachers=teachers,
        students=students,
        subjects=subjects,
        communications=communications,
    )


@bp.route("/subjects", methods=["GET", "POST"])
@login_required(ADMIN_ROLE)
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

    student_counts_query = db.session.query(Student.subject, db.func.count(Student.id))
    student_counts_query = student_counts_query.filter(Student.subject.isnot(None))
    student_counts_query = student_counts_query.group_by(Student.subject)
    student_counts = dict(student_counts_query.all())

    return render_template(
        "subjects.html",
        subjects=subjects,
        student_counts=student_counts,
        is_admin=True,
    )


@bp.route("/library", methods=["GET", "POST"])
@login_required(TEACHER_ROLE, ADMIN_ROLE)
def manage_library():
    role = session.get("role")
    teacher = current_teacher() if role == TEACHER_ROLE else None
    if teacher:
        subjects = sorted(
            teacher.subjects,
            key=lambda subject: subject.name.lower() if subject and subject.name else "",
        )
    else:
        subjects = SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()
    allowed_subject_ids = {subject.id for subject in subjects if subject and subject.id}

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
            elif teacher and subject_id and subject_id not in allowed_subject_ids:
                flash("Вы можете прикреплять материалы только к своим предметам.", "danger")
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
                if teacher and item.subject_id and item.subject_id not in allowed_subject_ids:
                    flash("Этот материал нельзя привязать к неразрешённому предмету.", "danger")
                    item.subject_id = None
                db.session.commit()
                flash("Материал обновлён.", "success")
        return redirect(url_for("diary.manage_library"))

    subject_filter = request.args.get("subject")
    query = LibraryMaterial.query.order_by(LibraryMaterial.created_at.desc())
    if subject_filter == "unassigned":
        query = query.filter(LibraryMaterial.subject_id.is_(None))
    elif subject_filter:
        if teacher and subject_filter not in {subject.name for subject in subjects}:
            subject_filter = None
        else:
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
    ensure_teacher_access(student)

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
        payment_status_choices=PAYMENT_STATUS_CHOICES,
    )


@bp.route("/sessions", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_sessions():
    teacher = current_teacher()
    if not teacher:
        flash("Сессия истекла, войдите снова.", "warning")
        return redirect(url_for("diary.login"))
    subject_settings = sorted(
        teacher.subjects,
        key=lambda subject: subject.name.lower() if subject and subject.name else "",
    )
    subject_defaults = {
        subject.name: subject.default_duration for subject in subject_settings
    }
    subject_focus = request.args.get("subject")
    if subject_focus and subject_focus not in subject_defaults:
        subject_focus = None

    students_query = Student.query.order_by(Student.full_name.asc()).filter(
        Student.teacher_id == teacher.id
    )
    students = students_query.all()

    if request.method == "POST":
        action = request.form.get("action", "create")
        redirect_target = url_for("diary.manage_sessions")

        if action == "update_finance":
            session_id_raw = request.form.get("session_id")
            lesson = (
                Session.query.get(int(session_id_raw))
                if session_id_raw and session_id_raw.isdigit()
                else None
            )
            if not lesson or lesson.student.teacher_id != teacher.id:
                flash("Занятие не найдено или относится к другому преподавателю.", "danger")
                return redirect(redirect_target)

            fee_amount_str = request.form.get("fee_amount", "").strip()
            payment_state = request.form.get("payment_status", "unpaid")
            try:
                lesson.fee_amount = (
                    Decimal(fee_amount_str) if fee_amount_str else None
                )
            except InvalidOperation:
                flash("Введите корректную сумму занятия.", "danger")
                return redirect(redirect_target)

            if payment_state not in PAYMENT_STATUS_CHOICES:
                payment_state = "unpaid"
            lesson.payment_status = payment_state
            if payment_state != "paid":
                lesson.payment_id = None
            db.session.commit()
            flash("Информация об оплате обновлена.", "success")
            return redirect(redirect_target)

        student_id_raw = request.form.get("student_id", "")
        date_str = request.form.get("date")
        start_time_str = request.form.get("start_time")
        duration = request.form.get("duration_minutes")
        topic = request.form.get("topic", "").strip()
        homework = request.form.get("homework", "").strip() or None
        status = request.form.get("status", "scheduled")
        payment_state = request.form.get("payment_status", "unpaid")
        fee_amount_str = request.form.get("fee_amount", "").strip()

        student_obj = (
            Student.query.get(int(student_id_raw))
            if student_id_raw and student_id_raw.isdigit()
            else None
        )
        if not student_obj:
            flash("Выберите ученика.", "danger")
            return redirect(redirect_target)
        if student_obj.teacher_id != teacher.id:
            flash("Нельзя планировать уроки для чужого ученика.", "danger")
            return redirect(redirect_target)

        try:
            fee_amount = Decimal(fee_amount_str) if fee_amount_str else None
        except InvalidOperation:
            flash("Введите корректную стоимость занятия.", "danger")
            return redirect(redirect_target)

        if payment_state not in PAYMENT_STATUS_CHOICES:
            payment_state = "unpaid"

        if date_str and start_time_str and duration and topic:
            session_entry = Session(
                student_id=student_obj.id,
                date=datetime.strptime(date_str, "%Y-%m-%d").date(),
                start_time=datetime.strptime(start_time_str, "%H:%M").time(),
                duration_minutes=int(duration),
                topic=topic,
                homework=homework,
                status=status,
                fee_amount=fee_amount,
                payment_status=payment_state,
            )
            db.session.add(session_entry)
            db.session.commit()
            flash("Занятие сохранено.", "success")
        else:
            flash("Заполните дату, время, длительность и тему занятия.", "danger")
        return redirect(redirect_target)

    sessions = (
        Session.query.join(Student, Session.student_id == Student.id)
        .filter(Student.teacher_id == teacher.id)
        .order_by(Session.date.desc(), Session.start_time.desc())
        .all()
    )

    return render_template(
        "sessions.html",
        sessions=sessions,
        students=students,
        subject_defaults=subject_defaults,
        subject_focus=subject_focus,
        teacher_options=[],
        selected_teacher_id=None,
        payment_status_choices=PAYMENT_STATUS_CHOICES,
    )


@bp.route("/payments", methods=["GET", "POST"])
@login_required(TEACHER_ROLE)
def manage_payments():
    teacher = current_teacher()
    if not teacher:
        flash("Сессия истекла, войдите снова.", "warning")
        return redirect(url_for("diary.login"))

    students = (
        Student.query.filter_by(teacher_id=teacher.id)
        .order_by(Student.full_name.asc())
        .all()
    )

    unpaid_sessions = (
        Session.query.join(Student, Session.student_id == Student.id)
        .filter(
            Student.teacher_id == teacher.id,
            Session.payment_status != "paid",
        )
        .order_by(Session.date.asc(), Session.start_time.asc())
        .all()
    )
    unpaid_map: dict[int, list[Session]] = {}
    for session_entry in unpaid_sessions:
        unpaid_map.setdefault(session_entry.student_id, []).append(session_entry)

    if request.method == "POST":
        student_id_raw = request.form.get("student_id", "")
        amount_str = request.form.get("amount", "").strip()
        paid_on_str = request.form.get("paid_on")
        method = request.form.get("method", "").strip()
        notes = request.form.get("notes", "").strip() or None
        session_ids = request.form.getlist("session_ids")

        student_obj = (
            Student.query.get(int(student_id_raw))
            if student_id_raw and student_id_raw.isdigit()
            else None
        )
        redirect_target = url_for("diary.manage_payments")
        if not student_obj or student_obj.teacher_id != teacher.id:
            flash("Выберите ученика из своего списка.", "danger")
            return redirect(redirect_target)

        selected_sessions: list[Session] = []
        for session_id in session_ids:
            if not session_id.isdigit():
                continue
            lesson = Session.query.get(int(session_id))
            if lesson and lesson.student_id == student_obj.id:
                selected_sessions.append(lesson)

        if not amount_str and selected_sessions:
            total = sum((lesson.fee_amount or Decimal("0")) for lesson in selected_sessions)
            amount_value = total
        else:
            try:
                amount_value = Decimal(amount_str or "0")
            except InvalidOperation:
                amount_value = Decimal("0")

        if amount_value <= 0 or not paid_on_str or not method:
            flash("Укажите сумму, дату и способ оплаты. Сумма может быть рассчитана автоматически.", "danger")
            return redirect(redirect_target)

        payment = Payment(
            student_id=student_obj.id,
            amount=amount_value,
            paid_on=datetime.strptime(paid_on_str, "%Y-%m-%d").date(),
            method=method,
            notes=notes,
        )
        db.session.add(payment)
        db.session.commit()

        if selected_sessions:
            for lesson in selected_sessions:
                lesson.payment_status = "paid"
                lesson.payment_id = payment.id
            db.session.commit()
        flash("Оплата сохранена.", "success")
        return redirect(redirect_target)

    payments = (
        Payment.query.join(Student, Payment.student_id == Student.id)
        .filter(Student.teacher_id == teacher.id)
        .order_by(Payment.paid_on.desc())
        .all()
    )

    outstanding_totals = {
        student_id: float(
            sum((lesson.fee_amount or Decimal("0")) for lesson in lessons)
        )
        for student_id, lessons in unpaid_map.items()
    }

    return render_template(
        "payments.html",
        payments=payments,
        students=students,
        teacher_options=[],
        selected_teacher_id=None,
        unpaid_sessions_map=unpaid_map,
        outstanding_totals=outstanding_totals,
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        admin_user = Admin.query.filter_by(username=username).first()
        if admin_user and admin_user.check_password(password):
            session.clear()
            session["role"] = ADMIN_ROLE
            session["admin_id"] = admin_user.id
            flash("Добро пожаловать, администратор ОбразОна!", "success")
            return redirect(url_for("diary.admin_dashboard"))

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
    student = current_student()
    if not student:
        session.clear()
        flash(
            "Не удалось найти профиль ученика. Пожалуйста, войдите снова.",
            "warning",
        )
        return redirect(url_for("diary.login"))

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

    student_messages_query = (
        AdminCommunication.query.filter(
            AdminCommunication.target_role == STUDENT_ROLE,
            or_(
                AdminCommunication.is_global.is_(True),
                AdminCommunication.student_id == student.id,
            ),
        )
        .order_by(AdminCommunication.created_at.desc())
    )
    student_messages = student_messages_query.limit(5).all()
    student_messages_due = [
        message
        for message in student_messages
        if message.due_date and today <= message.due_date <= today + timedelta(days=5)
    ]

    payment_assistant = {
        "tone": "info",
        "headline": "Все платежи под контролем",
        "message": "Последняя оплата была совсем недавно. Продолжай держать родителей в курсе расписания.",
        "suggestions": [],
    }
    outstanding_sessions = [
        lesson for lesson in upcoming_sessions if lesson.payment_status != "paid"
    ]
    outstanding_total = sum(
        (lesson.fee_amount or Decimal("0")) for lesson in outstanding_sessions
    )
    upcoming_week = [
        lesson
        for lesson in upcoming_sessions
        if 0 <= (lesson.date - today).days <= 7
    ]
    assignments_due_soon = [
        task
        for task in assignments
        if task.due_date
        and task.status != "completed"
        and 0 <= (task.due_date - today).days <= 3
    ]
    latest_material = materials[0] if materials else None

    if outstanding_sessions:
        payment_assistant.update(
            {
                "tone": "warning",
                "headline": "Напомни родителям про оплату",
                "message": (
                    "Есть занятия без подтверждения оплаты."
                    + (
                        f" Всего {outstanding_total:,.0f} ₽.".replace(",", " ")
                        if outstanding_total
                        else ""
                    )
                ),
            }
        )
    elif not payments:
        payment_assistant.update(
            {
                "tone": "warning",
                "headline": "Оплат ещё не было",
                "message": "Сообщи родителям о расписании и попроси заранее подтвердить оплату за занятия.",
            }
        )
    else:
        latest_payment = payments[0]
        days_since_payment = (today - latest_payment.paid_on).days
        payment_assistant["message"] = (
            "Последняя оплата была "
            f"{latest_payment.paid_on.strftime('%d.%m.%Y')} на сумму "
            f"{latest_payment.amount:,.2f}".replace(",", " ")
            + " ₽."
        )

        if outstanding_sessions:
            payment_assistant["tone"] = "warning"
            payment_assistant["headline"] = "Пора уточнить оплату"
            payment_assistant["message"] += " Есть занятия, которые ещё не закрыты по оплате."
        elif days_since_payment > 28:
            payment_assistant["tone"] = "danger"
            payment_assistant["headline"] = "Напомни родителям про оплату"
            payment_assistant["message"] += " Прошёл почти месяц — самое время написать родителям."
        elif days_since_payment > 20:
            payment_assistant["tone"] = "warning"
            payment_assistant["headline"] = "Пора обновить статус оплаты"
            payment_assistant["message"] += " Неделя занятий впереди — предупреди родителей заранее."
        else:
            payment_assistant["headline"] = "Отлично! Оплата свежая"
            payment_assistant["message"] += " Просто напомни родителям перед ближайшими уроками."

    if upcoming_week:
        payment_assistant["suggestions"].append(
            {
                "icon": "📅",
                "title": f"На этой неделе {len(upcoming_week)} урок(ов)",
                "body": "Отправь родителям расписание и уточни, всё ли готово к занятиям.",
                "tone": "info",
            }
        )

    if outstanding_sessions:
        payment_assistant["suggestions"].append(
            {
                "icon": "💰",
                "title": "Попроси родителей подтвердить оплату",
                "body": (
                    f"Ожидает {len(outstanding_sessions)} урок(ов)"
                    + (
                        f" на {outstanding_total:,.0f} ₽.".replace(",", " ")
                        if outstanding_total
                        else "."
                    )
                ),
                "tone": "warning",
            }
        )

    if assignments_due_soon:
        payment_assistant["suggestions"].append(
            {
                "icon": "📝",
                "title": "Есть задания со скорым дедлайном",
                "body": "Расскажи родителям о важном домашнем задании, чтобы вместе спланировать время и оплату.",
                "tone": "warning",
            }
        )
    elif latest_material:
        payment_assistant["suggestions"].append(
            {
                "icon": "📚",
                "title": "Поделись новыми материалами",
                "body": "Покажи родителям свежие материалы от преподавателя — так проще обсудить нужные платежи.",
                "tone": "success",
            }
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
        payment_assistant=payment_assistant,
        student_messages=student_messages,
        student_messages_due=student_messages_due,
    )


@bp.route("/student/chat", methods=["GET", "POST"])
@login_required(STUDENT_ROLE)
def student_chat():
    student = current_student()
    if not student:
        session.clear()
        flash(
            "Диалог недоступен, потому что аккаунт ученика был удалён. Войдите снова.",
            "warning",
        )
        return redirect(url_for("diary.login"))

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
    student = current_student()
    if not student:
        session.clear()
        flash(
            "Домашние задания недоступны: аккаунт ученика не найден. Войдите снова.",
            "warning",
        )
        return redirect(url_for("diary.login"))

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
    student = current_student()
    if not student:
        session.clear()
        flash(
            "Материалы недоступны: аккаунт ученика был удалён. Войдите снова.",
            "warning",
        )
        return redirect(url_for("diary.login"))

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
@login_required(TEACHER_ROLE, ADMIN_ROLE)
def api_student_calendar(student_id: int):
    student = Student.query.get_or_404(student_id)
    ensure_teacher_access(student)
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
