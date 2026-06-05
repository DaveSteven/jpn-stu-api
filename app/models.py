from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    subject_label: Mapped[str] = mapped_column(String(32), nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    task_order: Mapped[int | None] = mapped_column(Integer)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="")
    source_index: Mapped[int | None] = mapped_column(Integer)

    book: Mapped[Book] = relationship(back_populates="questions")
    options: Mapped[list["Option"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class Option(Base):
    __tablename__ = "options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id"), index=True, nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)

    question: Mapped[Question] = relationship(back_populates="options")


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    level: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    subject_label: Mapped[str] = mapped_column(String(32), nullable=False)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    correct: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    answers: Mapped[list["AttemptAnswer"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempts.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id"), index=True, nullable=False
    )
    chosen_option_id: Mapped[int | None] = mapped_column(ForeignKey("options.id"))
    correct_option_id: Mapped[int] = mapped_column(ForeignKey("options.id"), nullable=False)
    is_correct: Mapped[int] = mapped_column(Integer, nullable=False)

    attempt: Mapped[Attempt] = relationship(back_populates="answers")


class WrongQuestion(Base):
    __tablename__ = "wrong_questions"
    __table_args__ = (UniqueConstraint("user_id", "question_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id"), index=True, nullable=False
    )
    wrong_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_chosen_option_id: Mapped[int | None] = mapped_column(ForeignKey("options.id"))
    last_wrong_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
