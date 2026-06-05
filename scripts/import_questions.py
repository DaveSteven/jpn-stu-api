import json
import os
from pathlib import Path

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import ensure_default_user
from app.models import Attempt, Book, Option, Question


IMPORT_DATA_DIR = Path(os.getenv("IMPORT_DATA_DIR", "/app/import_data"))

SUBJECTS = {
    "GRAMMAR": ("grammar", "文法"),
    "MEANING": ("meaning", "語彙"),
    "KANJI": ("kanji", "漢字"),
}


def dataset_config(path: Path) -> dict | None:
    raw_level = path.parent.name
    parts = raw_level.split("_", 1)
    level = parts[0]
    subject_key = parts[1] if len(parts) > 1 else "GRAMMAR"
    subject = SUBJECTS.get(subject_key)
    if not subject:
        print(f"skip unknown subject dataset {raw_level}")
        return None

    subject_id, subject_label = subject
    return {
        "path": path,
        "level": level,
        "subject": subject_id,
        "subject_label": subject_label,
    }


def discover_imports(import_data_dir: Path) -> list[dict]:
    configs = [
        config
        for config in (
            dataset_config(path)
            for path in sorted(import_data_dir.glob("*/books.json"))
        )
        if config is not None
    ]
    if not configs:
        raise RuntimeError(f"no books.json files found under {import_data_dir}")
    return configs


def load_books(path: Path) -> list[dict]:
    if not path.exists():
        print(f"skip missing {path}")
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def lesson_name(index: int) -> str:
    return f"第 {index:02d} 课"


def main() -> None:
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        ensure_default_user(db)

        book_total = 0
        question_total = 0
        option_total = 0
        created_books = 0
        created_questions = 0
        created_options = 0

        for config in discover_imports(IMPORT_DATA_DIR):
            for lesson_index, source_book in enumerate(load_books(config["path"]), start=1):
                book = db.scalar(
                    select(Book).where(Book.external_id == source_book["book_id"])
                )
                if not book:
                    book = Book(external_id=source_book["book_id"])
                    db.add(book)
                    created_books += 1
                book.name = lesson_name(lesson_index)
                book.level = config["level"]
                book.subject = config["subject"]
                book.subject_label = config["subject_label"]
                book.question_count = source_book.get("question_count", 0)
                book.task_order = lesson_index
                db.flush()
                book_total += 1

                for source_question in source_book.get("questions", []):
                    options = source_question.get("options") or []
                    answer_text = source_question.get("answer_text") or ""
                    if not options or not answer_text:
                        continue

                    question = db.scalar(
                        select(Question).where(
                            Question.external_id == source_question["id"]
                        )
                    )
                    if not question:
                        question = Question(external_id=source_question["id"])
                        db.add(question)
                        created_questions += 1
                    question.book_id = book.id
                    question.level = config["level"]
                    question.subject = config["subject"]
                    question.stem = source_question.get("stem") or ""
                    question.question_text = source_question.get("question_text") or ""
                    question.answer_text = answer_text
                    question.explanation = source_question.get("explanation") or ""
                    question.source_index = source_question.get("index")
                    db.flush()
                    question_total += 1

                    for index, option_text in enumerate(options):
                        is_correct = 1 if option_text == answer_text else 0
                        option = db.scalar(
                            select(Option).where(
                                Option.question_id == question.id,
                                Option.source_order == index,
                            )
                        )
                        if not option:
                            option = Option(
                                question_id=question.id,
                                source_order=index,
                            )
                            db.add(option)
                            created_options += 1
                        option.text = option_text
                        option.is_correct = is_correct
                        option_total += 1

        for attempt, title in db.execute(
            select(Attempt, Book.name).join(Book, Attempt.book_id == Book.id)
        ):
            attempt.title = title

        db.commit()

    print(
        f"imported {book_total} books, {question_total} questions, "
        f"{option_total} options "
        f"({created_books} new books, {created_questions} new questions, "
        f"{created_options} new options)"
    )


if __name__ == "__main__":
    main()
