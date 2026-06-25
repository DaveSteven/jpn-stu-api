import json
from pathlib import Path

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import ensure_schema
from app.models import (
    VocabularyCollocation,
    VocabularyEntry,
    VocabularyExample,
    VocabularyRelation,
)


DATA_FILE = Path(__file__).resolve().parents[1] / "import_data" / "vocabulary.json"


def main() -> None:
    rows = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        ensure_schema(db)
        for row in rows:
            entry = db.scalar(
                select(VocabularyEntry).where(VocabularyEntry.source_key == row["source_key"])
            )
            if entry is None:
                entry = VocabularyEntry(source_key=row["source_key"])

            entry.lesson = row["lesson"]
            entry.frequency_tier = row["frequency_tier"]
            entry.word = row["word"]
            entry.reading_hiragana = row["reading_hiragana"]
            entry.reading_katakana = row["reading_katakana"]
            entry.part_of_speech = row["part_of_speech"]
            entry.transitivity = row.get("transitivity")
            entry.meaning_zh = row["meaning_zh"]
            entry.source_page = row.get("source_page")
            entry.relations = [
                VocabularyRelation(
                    relation_type=item["type"],
                    term=item["term"],
                    term_reading_katakana=item.get("term_reading_katakana", ""),
                )
                for item in row.get("relations", [])
            ]
            entry.collocations = [
                VocabularyCollocation(text=value) for value in row.get("collocations", [])
            ]
            entry.examples = [
                VocabularyExample(
                    sentence=value["sentence"] if isinstance(value, dict) else value,
                    reading_katakana="",
                    reading_hiragana=value.get("reading_hiragana", "") if isinstance(value, dict) else "",
                    phonetic_guide_json=json.dumps(value.get("phonetic_guide", []), ensure_ascii=False) if isinstance(value, dict) else "[]",
                    source=value.get("source", "") if isinstance(value, dict) else "",
                )
                for value in row.get("examples", [])
            ]
            db.add(entry)

        db.commit()
        print(f"Imported {len(rows)} vocabulary entries")


if __name__ == "__main__":
    main()
