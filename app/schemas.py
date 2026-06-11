from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    current_level: str = "N5"

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    username: str
    display_name: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class UserUpdate(BaseModel):
    current_level: str | None = None


class SessionOut(BaseModel):
    token: str
    expires_at: datetime
    user: UserOut


class BookOut(BaseModel):
    id: int
    external_id: int
    name: str
    level: str
    subject: str
    subject_label: str
    question_count: int

    model_config = ConfigDict(from_attributes=True)


class QuizOptionOut(BaseModel):
    id: int
    text: str


class QuizQuestionOut(BaseModel):
    id: int
    book_id: int
    book_name: str
    stem: str
    question_text: str
    explanation: str
    options: list[QuizOptionOut]


class QuizOut(BaseModel):
    mode: str
    title: str
    level: str
    subject: str
    subject_label: str
    book_id: int | None = None
    questions: list[QuizQuestionOut]


class AttemptAnswerIn(BaseModel):
    question_id: int
    chosen_option_id: int | None = None


class CheckAnswerIn(BaseModel):
    question_id: int
    chosen_option_id: int | None = None


class CheckAnswerOut(BaseModel):
    question_id: int
    chosen_option_id: int | None
    correct_option_id: int
    answer_text: str
    is_correct: bool


class AttemptIn(BaseModel):
    mode: str
    level: str
    subject: str
    subject_label: str
    book_id: int | None = None
    title: str
    answers: list[AttemptAnswerIn]


class AttemptAnswerOut(BaseModel):
    question_id: int
    stem: str
    chosen_option_id: int | None
    chosen_text: str | None
    correct_option_id: int
    answer_text: str
    is_correct: bool


class AttemptOut(BaseModel):
    id: int
    mode: str
    level: str
    subject: str
    subject_label: str
    book_id: int | None
    title: str
    correct: int
    total: int
    created_at: datetime
    answers: list[AttemptAnswerOut] = []


class WrongQuestionOut(BaseModel):
    question_id: int
    stem: str
    level: str
    subject: str
    subject_label: str
    book_id: int
    book_name: str
    answer_text: str
    wrong_count: int
    last_chosen_text: str | None
    last_wrong_at: datetime
