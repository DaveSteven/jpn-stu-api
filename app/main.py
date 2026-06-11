import os
import random
import hashlib
import hmac
import secrets
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .database import Base, engine, get_db
from .models import (
    Attempt,
    AttemptAnswer,
    Book,
    Exam,
    ExamAttempt,
    ExamAttemptAnswer,
    ExamLayer,
    ExamOption,
    ExamQuestion,
    Option,
    Question,
    User,
    UserSession,
    WrongQuestion,
)
from .schemas import (
    AttemptAnswerOut,
    CheckAnswerIn,
    CheckAnswerOut,
    AttemptIn,
    AttemptOut,
    BookOut,
    ExamAttemptIn,
    ExamAttemptOut,
    ExamAttemptAnswerOut,
    ExamDetailOut,
    ExamLayerOut,
    ExamOptionOut,
    ExamOut,
    ExamQuestionOut,
    LoginIn,
    QuizOptionOut,
    QuizOut,
    QuizQuestionOut,
    SessionOut,
    UserCreate,
    UserOut,
    UserUpdate,
    WrongQuestionOut,
)


DEFAULT_USER_ID = 1
DEFAULT_DAVID_PASSWORD = os.getenv("DEFAULT_DAVID_PASSWORD", "214423")
SESSION_DAYS = 30
JLPT_LEVELS = {"N5", "N4", "N3", "N2", "N1"}
ASSETS_DIR = Path(
    os.getenv(
        "EXAM_ASSETS_DIR",
        Path(__file__).resolve().parents[2] / "mojitest_spider" / "data" / "assets",
    )
)

app = FastAPI(title="Japanese Study API")

origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        ensure_schema(db)
        ensure_default_user(db)
        sync_postgres_sequences(db)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt, digest = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    expected = hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(expected, digest)


def ensure_schema(db: Session) -> None:
    if engine.url.drivername.startswith("postgresql"):
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_level VARCHAR(16) NOT NULL DEFAULT 'N5'"))
        db.execute(text("ALTER TABLE exam_questions ADD COLUMN IF NOT EXISTS passage TEXT NOT NULL DEFAULT ''"))
        db.commit()
        return

    if not engine.url.drivername.startswith("sqlite"):
        return

    columns = {
        row[1] for row in db.connection().exec_driver_sql("PRAGMA table_info(users)").all()
    }
    if "password_hash" not in columns:
        db.connection().exec_driver_sql(
            "ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NOT NULL DEFAULT ''"
        )
        db.commit()
    if "current_level" not in columns:
        db.connection().exec_driver_sql(
            "ALTER TABLE users ADD COLUMN current_level VARCHAR(16) NOT NULL DEFAULT 'N5'"
        )
        db.commit()

    question_columns = {
        row[1] for row in db.connection().exec_driver_sql("PRAGMA table_info(exam_questions)").all()
    }
    if "passage" not in question_columns:
        db.connection().exec_driver_sql(
            "ALTER TABLE exam_questions ADD COLUMN passage TEXT NOT NULL DEFAULT ''"
        )
        db.commit()


def ensure_default_user(db: Session) -> User:
    user = db.get(User, DEFAULT_USER_ID)
    if user:
        if not user.password_hash:
            user.password_hash = hash_password(DEFAULT_DAVID_PASSWORD)
            db.commit()
            db.refresh(user)
        if not user.current_level:
            user.current_level = "N5"
            db.commit()
            db.refresh(user)
        return user

    user = User(
        id=DEFAULT_USER_ID,
        username="david",
        display_name="David",
        password_hash=hash_password(DEFAULT_DAVID_PASSWORD),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def sync_postgres_sequences(db: Session) -> None:
    if not engine.url.drivername.startswith("postgresql"):
        return

    db.execute(
        text(
            """
            SELECT setval(
                pg_get_serial_sequence('users', 'id'),
                GREATEST((SELECT COALESCE(MAX(id), 1) FROM users), 1),
                (SELECT COUNT(*) > 0 FROM users)
            )
            """
        )
    )
    db.commit()


def get_current_user(
    authorization: str | None = Header(default=None),
    x_user_id: int | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        session = db.scalar(select(UserSession).where(UserSession.token == token))
        if not session or session.expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        user = db.get(User, session.user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Session user not found")
        return user

    if x_user_id is None:
        return ensure_default_user(db)

    user = db.get(User, x_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def create_session_for_user(db: Session, user: User) -> SessionOut:
    session = UserSession(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return SessionOut(token=session.token, expires_at=session.expires_at, user=user)


def question_to_quiz_out(question: Question) -> QuizQuestionOut:
    options = list(question.options)
    random.shuffle(options)
    return QuizQuestionOut(
        id=question.id,
        book_id=question.book_id,
        book_name=question.book.name,
        stem=question.stem,
        question_text=question.question_text,
        explanation=question.explanation or "",
        options=[QuizOptionOut(id=option.id, text=option.text) for option in options],
    )


def build_quiz(
    *,
    mode: str,
    title: str,
    level: str,
    subject: str,
    subject_label: str,
    questions: list[Question],
    book_id: int | None = None,
) -> QuizOut:
    return QuizOut(
        mode=mode,
        title=title,
        level=level,
        subject=subject,
        subject_label=subject_label,
        book_id=book_id,
        questions=[question_to_quiz_out(question) for question in questions],
    )


def asset_url(path: str) -> str:
    if not path:
        return ""
    try:
        relative = Path(path).resolve().relative_to(ASSETS_DIR.resolve())
    except ValueError:
        relative = Path(path)
    return "/assets/" + quote(str(relative).replace("\\", "/"))


def exam_to_out(exam: Exam) -> ExamOut:
    return ExamOut(
        id=exam.id,
        external_id=exam.external_id,
        level=exam.level,
        title=exam.title,
        exam_date=exam.exam_date,
        media_url=asset_url(exam.media_path),
        question_count=exam.question_count,
        layer_count=exam.layer_count,
        vocabulary_num=exam.vocabulary_num,
        grammar_num=exam.grammar_num,
        reading_num=exam.reading_num,
        listening_num=exam.listening_num,
    )


def exam_question_to_out(question: ExamQuestion) -> ExamQuestionOut:
    return ExamQuestionOut(
        id=question.id,
        external_id=question.external_id,
        layer_id=question.layer_id,
        layer_external_id=question.layer.external_id,
        order_index=question.order_index,
        question_type=question.question_type,
        data_type=question.data_type,
        title=question.title,
        passage=question.passage,
        analysis=question.analysis,
        translation=question.translation,
        subtitle=question.subtitle,
        image_url=asset_url(question.image_path),
        media_url=asset_url(question.media_path),
        options=[
            ExamOptionOut(id=option.id, text=option.text, source_order=option.source_order)
            for option in sorted(question.options, key=lambda item: item.source_order)
        ],
    )


def exam_attempt_to_out(
    attempt: ExamAttempt,
    answers: list[ExamAttemptAnswerOut] | None = None,
) -> ExamAttemptOut:
    return ExamAttemptOut(
        id=attempt.id,
        exam_id=attempt.exam_id,
        level=attempt.level,
        title=attempt.title,
        correct=attempt.correct,
        total=attempt.total,
        created_at=attempt.created_at,
        answers=answers or [],
    )


def exam_attempt_answers_to_out(db: Session, attempt: ExamAttempt) -> list[ExamAttemptAnswerOut]:
    rows = list(
        db.scalars(
            select(ExamAttemptAnswer).where(ExamAttemptAnswer.attempt_id == attempt.id)
        )
    )
    question_ids = [row.question_id for row in rows]
    option_ids = {
        option_id
        for row in rows
        for option_id in (row.chosen_option_id, row.correct_option_id)
        if option_id
    }
    questions = {
        question.id: question
        for question in db.scalars(
            select(ExamQuestion).where(ExamQuestion.id.in_(question_ids))
        )
    }
    options = {
        option.id: option
        for option in db.scalars(select(ExamOption).where(ExamOption.id.in_(option_ids)))
    }

    answers: list[ExamAttemptAnswerOut] = []
    for row in sorted(rows, key=lambda item: questions[item.question_id].order_index):
        question = questions[row.question_id]
        chosen_option = options.get(row.chosen_option_id) if row.chosen_option_id else None
        correct_option = options[row.correct_option_id]
        answers.append(
            ExamAttemptAnswerOut(
                question_id=question.id,
                title=question.title,
                chosen_option_id=chosen_option.id if chosen_option else None,
                chosen_text=chosen_option.text if chosen_option else None,
                correct_option_id=correct_option.id,
                answer_text=correct_option.text,
                analysis=question.analysis,
                is_correct=bool(row.is_correct),
            )
        )
    return answers


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@app.patch("/api/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if payload.current_level is not None:
        current_level = payload.current_level.strip().upper()
        if current_level not in JLPT_LEVELS:
            raise HTTPException(status_code=400, detail="Invalid JLPT level")
        user.current_level = current_level

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/me/level", response_model=UserOut)
def update_my_level(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return update_me(payload, user, db)


def create_user(payload: UserCreate, db: Session) -> User:
    username = payload.username.strip()
    display_name = payload.display_name.strip()
    password = payload.password.strip()
    if not username or not display_name or not password:
        raise HTTPException(status_code=400, detail="Username, display name, and password are required")

    existing = db.scalar(select(User).where(User.username == username))
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(username=username, display_name=display_name, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/auth/register", response_model=SessionOut)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> SessionOut:
    user = create_user(payload, db)
    return create_session_for_user(db, user)


@app.post("/api/auth/login", response_model=SessionOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> SessionOut:
    ensure_default_user(db)
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return create_session_for_user(db, user)


@app.post("/api/auth/logout")
def logout(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        session = db.scalar(select(UserSession).where(UserSession.token == token))
        if session:
            db.delete(session)
            db.commit()
    return {"status": "ok"}


@app.get("/api/books", response_model=list[BookOut])
def books(
    level: str = Query(...),
    subject: str = Query(...),
    db: Session = Depends(get_db),
) -> list[Book]:
    return list(
        db.scalars(
            select(Book)
            .where(Book.level == level, Book.subject == subject)
            .order_by(Book.task_order, Book.id)
        )
    )


@app.get("/api/books/{book_id}/quiz", response_model=QuizOut)
def book_quiz(
    book_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> QuizOut:
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    questions = list(
        db.scalars(
            select(Question)
            .where(Question.book_id == book.id)
            .options(selectinload(Question.options), selectinload(Question.book))
        )
    )
    random.shuffle(questions)

    return build_quiz(
        mode="book",
        title=book.name,
        level=book.level,
        subject=book.subject,
        subject_label=book.subject_label,
        book_id=book.id,
        questions=questions[:limit],
    )


@app.get("/api/exams", response_model=list[ExamOut])
def exams(
    level: str = Query(...),
    db: Session = Depends(get_db),
) -> list[ExamOut]:
    rows = list(
        db.scalars(
            select(Exam)
            .where(Exam.level == level)
            .order_by(Exam.exam_date.desc().nullslast(), Exam.title.desc())
        )
    )
    return [exam_to_out(row) for row in rows]


@app.get("/api/exams/{exam_id}", response_model=ExamDetailOut)
def exam_detail(exam_id: int, db: Session = Depends(get_db)) -> ExamDetailOut:
    exam = db.scalar(
        select(Exam)
        .where(Exam.id == exam_id)
        .options(selectinload(Exam.layers))
    )
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    base = exam_to_out(exam).model_dump()
    base["layers"] = [
        ExamLayerOut.model_validate(layer)
        for layer in sorted(exam.layers, key=lambda item: item.order_index)
    ]
    return ExamDetailOut(**base)


@app.get("/api/exams/{exam_id}/questions", response_model=list[ExamQuestionOut])
def exam_questions(exam_id: int, db: Session = Depends(get_db)) -> list[ExamQuestionOut]:
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    questions = list(
        db.scalars(
            select(ExamQuestion)
            .where(ExamQuestion.exam_id == exam_id)
            .options(selectinload(ExamQuestion.options), selectinload(ExamQuestion.layer))
            .order_by(ExamQuestion.order_index)
        )
    )
    return [exam_question_to_out(question) for question in questions]


@app.post("/api/exam-attempts", response_model=ExamAttemptOut)
def create_exam_attempt(
    payload: ExamAttemptIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ExamAttemptOut:
    exam = db.get(Exam, payload.exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    question_ids = [answer.question_id for answer in payload.answers]
    questions = {
        question.id: question
        for question in db.scalars(
            select(ExamQuestion)
            .where(ExamQuestion.id.in_(question_ids), ExamQuestion.exam_id == exam.id)
            .options(selectinload(ExamQuestion.options))
        )
    }
    if len(questions) != len(set(question_ids)):
        raise HTTPException(status_code=400, detail="Unknown question in answers")

    created_at = datetime.utcnow()
    correct_count = 0
    evaluated: list[ExamAttemptAnswerOut] = []
    attempt = ExamAttempt(
        user_id=user.id,
        exam_id=exam.id,
        level=exam.level,
        title=exam.title,
        correct=0,
        total=len(payload.answers),
        created_at=created_at,
    )
    db.add(attempt)
    db.flush()

    for answer in payload.answers:
        question = questions[answer.question_id]
        correct_option = next(option for option in question.options if option.is_correct)
        chosen_option = (
            db.get(ExamOption, answer.chosen_option_id) if answer.chosen_option_id else None
        )
        if chosen_option and chosen_option.question_id != question.id:
            raise HTTPException(status_code=400, detail="Chosen option does not belong to question")

        is_correct = bool(chosen_option and chosen_option.id == correct_option.id)
        correct_count += 1 if is_correct else 0
        db.add(
            ExamAttemptAnswer(
                attempt_id=attempt.id,
                user_id=user.id,
                question_id=question.id,
                chosen_option_id=chosen_option.id if chosen_option else None,
                correct_option_id=correct_option.id,
                is_correct=1 if is_correct else 0,
            )
        )
        evaluated.append(
            ExamAttemptAnswerOut(
                question_id=question.id,
                title=question.title,
                chosen_option_id=chosen_option.id if chosen_option else None,
                chosen_text=chosen_option.text if chosen_option else None,
                correct_option_id=correct_option.id,
                answer_text=correct_option.text,
                analysis=question.analysis,
                is_correct=is_correct,
            )
        )

    attempt.correct = correct_count
    db.commit()
    db.refresh(attempt)
    return exam_attempt_to_out(attempt, evaluated)


@app.get("/api/exam-attempts", response_model=list[ExamAttemptOut])
def exam_attempts(
    level: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ExamAttemptOut]:
    stmt = (
        select(ExamAttempt)
        .where(ExamAttempt.user_id == user.id)
        .order_by(ExamAttempt.created_at.desc())
    )
    if level:
        stmt = stmt.where(ExamAttempt.level == level)
    stmt = stmt.limit(limit)
    return [exam_attempt_to_out(row) for row in db.scalars(stmt)]


@app.get("/api/exam-attempts/{attempt_id}", response_model=ExamAttemptOut)
def exam_attempt_detail(
    attempt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ExamAttemptOut:
    attempt = db.get(ExamAttempt, attempt_id)
    if not attempt or attempt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Exam attempt not found")
    return exam_attempt_to_out(attempt, exam_attempt_answers_to_out(db, attempt))


@app.get("/api/quiz/mixed", response_model=QuizOut)
def mixed_quiz(
    level: str = Query(...),
    subject: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> QuizOut:
    questions = list(
        db.scalars(
            select(Question)
            .where(Question.level == level, Question.subject == subject)
            .options(selectinload(Question.options), selectinload(Question.book))
        )
    )
    if not questions:
        raise HTTPException(status_code=404, detail="No questions found")

    random.shuffle(questions)
    subject_label = questions[0].book.subject_label
    return build_quiz(
        mode="mixed",
        title=f"{level} {subject_label} 综合测试",
        level=level,
        subject=subject,
        subject_label=subject_label,
        questions=questions[:limit],
    )


@app.post("/api/attempts", response_model=AttemptOut)
def create_attempt(
    payload: AttemptIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AttemptOut:

    question_ids = [answer.question_id for answer in payload.answers]
    questions = {
        question.id: question
        for question in db.scalars(
            select(Question)
            .where(Question.id.in_(question_ids))
            .options(selectinload(Question.options), selectinload(Question.book))
        )
    }
    if len(questions) != len(set(question_ids)):
        raise HTTPException(status_code=400, detail="Unknown question in answers")

    created_at = datetime.utcnow()
    evaluated: list[AttemptAnswerOut] = []
    correct_count = 0

    attempt = Attempt(
        user_id=user.id,
        mode=payload.mode,
        level=payload.level,
        subject=payload.subject,
        subject_label=payload.subject_label,
        book_id=payload.book_id,
        title=payload.title,
        correct=0,
        total=len(payload.answers),
        created_at=created_at,
    )
    db.add(attempt)
    db.flush()

    for answer in payload.answers:
        question = questions[answer.question_id]
        correct_option = next(option for option in question.options if option.is_correct)
        chosen_option = (
            db.get(Option, answer.chosen_option_id) if answer.chosen_option_id else None
        )
        is_correct = bool(chosen_option and chosen_option.id == correct_option.id)
        correct_count += 1 if is_correct else 0

        db.add(
            AttemptAnswer(
                attempt_id=attempt.id,
                user_id=user.id,
                question_id=question.id,
                chosen_option_id=chosen_option.id if chosen_option else None,
                correct_option_id=correct_option.id,
                is_correct=1 if is_correct else 0,
            )
        )

        wrong = db.scalar(
            select(WrongQuestion).where(
                WrongQuestion.user_id == user.id,
                WrongQuestion.question_id == question.id,
            )
        )

        if payload.mode == "wrong_review":
            if is_correct and wrong:
                db.delete(wrong)
            elif not is_correct and wrong:
                wrong.last_chosen_option_id = chosen_option.id if chosen_option else None
                wrong.last_wrong_at = created_at
        elif not is_correct:
            if wrong:
                wrong.wrong_count += 1
                wrong.last_chosen_option_id = chosen_option.id if chosen_option else None
                wrong.last_wrong_at = created_at
            else:
                db.add(
                    WrongQuestion(
                        user_id=user.id,
                        question_id=question.id,
                        wrong_count=1,
                        last_chosen_option_id=chosen_option.id if chosen_option else None,
                        last_wrong_at=created_at,
                    )
                )

        evaluated.append(
            AttemptAnswerOut(
                question_id=question.id,
                stem=question.stem,
                chosen_option_id=chosen_option.id if chosen_option else None,
                chosen_text=chosen_option.text if chosen_option else None,
                correct_option_id=correct_option.id,
                answer_text=correct_option.text,
                is_correct=is_correct,
                options=[
                    QuizOptionOut(id=option.id, text=option.text)
                    for option in sorted(question.options, key=lambda item: item.source_order)
                ],
            )
        )

    attempt.correct = correct_count
    db.commit()
    db.refresh(attempt)
    return AttemptOut(
        id=attempt.id,
        mode=attempt.mode,
        level=attempt.level,
        subject=attempt.subject,
        subject_label=attempt.subject_label,
        book_id=attempt.book_id,
        title=attempt.title,
        correct=attempt.correct,
        total=attempt.total,
        created_at=attempt.created_at,
        answers=evaluated,
    )


@app.post("/api/check-answer", response_model=CheckAnswerOut)
def check_answer(payload: CheckAnswerIn, db: Session = Depends(get_db)) -> CheckAnswerOut:
    question = db.scalar(
        select(Question)
        .where(Question.id == payload.question_id)
        .options(selectinload(Question.options))
    )
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    correct_option = next(option for option in question.options if option.is_correct)
    chosen_option = (
        db.get(Option, payload.chosen_option_id) if payload.chosen_option_id else None
    )
    return CheckAnswerOut(
        question_id=question.id,
        chosen_option_id=chosen_option.id if chosen_option else None,
        correct_option_id=correct_option.id,
        answer_text=correct_option.text,
        is_correct=bool(chosen_option and chosen_option.id == correct_option.id),
    )


@app.get("/api/wrong-questions/quiz", response_model=QuizOut)
def wrong_questions_quiz(
    level: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QuizOut:
    questions = list(
        db.scalars(
            select(Question)
            .join(WrongQuestion, WrongQuestion.question_id == Question.id)
            .where(WrongQuestion.user_id == user.id, Question.level == level)
            .options(selectinload(Question.options), selectinload(Question.book))
        )
    )
    if not questions:
        raise HTTPException(status_code=404, detail="No wrong questions found")

    random.shuffle(questions)
    return build_quiz(
        mode="wrong_review",
        title=f"{level} 错题复习",
        level=level,
        subject="wrong_review",
        subject_label="错题",
        questions=questions[:limit],
    )


@app.get("/api/attempts", response_model=list[AttemptOut])
def attempts(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AttemptOut]:
    rows = list(
        db.scalars(
            select(Attempt)
            .where(Attempt.user_id == user.id)
            .order_by(Attempt.created_at.desc())
            .limit(limit)
        )
    )
    return [
        AttemptOut(
            id=row.id,
            mode=row.mode,
            level=row.level,
            subject=row.subject,
            subject_label=row.subject_label,
            book_id=row.book_id,
            title=row.title,
            correct=row.correct,
            total=row.total,
            created_at=row.created_at,
            answers=[],
        )
        for row in rows
    ]


@app.get("/api/wrong-questions", response_model=list[WrongQuestionOut])
def wrong_questions(
    level: str | None = None,
    subject: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[WrongQuestionOut]:
    stmt = (
        select(WrongQuestion, Question, Book, Option)
        .join(Question, WrongQuestion.question_id == Question.id)
        .join(Book, Question.book_id == Book.id)
        .outerjoin(Option, WrongQuestion.last_chosen_option_id == Option.id)
        .where(WrongQuestion.user_id == user.id)
        .order_by(WrongQuestion.last_wrong_at.desc())
    )
    if level:
        stmt = stmt.where(Question.level == level)
    if subject:
        stmt = stmt.where(Question.subject == subject)

    output = []
    for wrong, question, book, chosen in db.execute(stmt).all():
        output.append(
            WrongQuestionOut(
                question_id=question.id,
                stem=question.stem,
                level=question.level,
                subject=question.subject,
                subject_label=book.subject_label,
                book_id=book.id,
                book_name=book.name,
                answer_text=question.answer_text,
                wrong_count=wrong.wrong_count,
                last_chosen_text=chosen.text if chosen else None,
                last_wrong_at=wrong.last_wrong_at,
            )
        )
    return output
