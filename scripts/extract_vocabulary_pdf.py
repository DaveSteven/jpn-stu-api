"""Extract the three-circle vocabulary tier from the supplied course PDF.

The output is a normalized JSON file consumed by import_vocabulary.py.  Keeping
PDF parsing out of the deployed app makes the production image small and makes
the extracted data reviewable before import.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pdfplumber


ENTRY_RE = re.compile(
    r"^(?P<reading>[ぁ-んァ-ヶーA-Za-z・]+)"
    r"(?:（(?P<written>[^）]+)）)?"
    r"[⓪①②③④⑤⑥⑦⑧⑨⑩0-9]*"
    r"[【［](?P<pos>名|动[123]|動[123]|副|副詞)[】］]"
    r"(?P<meaning>.*)$"
)
LESSON_RE = re.compile(r"第\s*(\d+)\s*课")

POS_MAP = {
    "名": "noun",
    "动1": "verb",
    "动2": "verb",
    "动3": "verb",
    "動1": "verb",
    "動2": "verb",
    "動3": "verb",
    "副": "adverb",
    "副詞": "adverb",
}

# Conservative, high-confidence pairs. Not every word naturally has both.
SYNONYMS = {
    "予測": ["予想"],
    "予想": ["予測"],
    "ついに": ["とうとう", "ようやく"],
    "とうとう": ["ついに"],
    "僅か": ["ほんの"],
    "非常に": ["とても"],
    "直ちに": ["すぐに"],
    "勤める": ["働く"],
    "隠す": ["秘める"],
    "助ける": ["手伝う"],
}

ANTONYMS = {
    "上る": ["下る"],
    "下る": ["上る"],
    "上回る": ["下回る"],
    "下回る": ["上回る"],
    "増える": ["減る"],
    "減る": ["増える"],
    "増加": ["減少"],
    "減少": ["増加"],
    "隠す": ["表す"],
    "始まる": ["終わる"],
    "始める": ["終える"],
    "賛成": ["反対"],
    "成功": ["失敗"],
    "収入": ["支出"],
}


def to_katakana(value: str) -> str:
    return "".join(chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char for char in value)


def clean(value: str) -> str:
    value = re.sub(r"[!\"#$%&'()*+,./0-9:;<=>?@A-Z\\^_`a-z{|}~]+$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def display_word(entry: dict) -> str:
    return entry["word"].replace("～", "").strip()


def finish_entry(entry: dict | None, rows: list[dict]) -> None:
    if not entry:
        return

    notes = entry.pop("_notes")
    examples: list[str] = []
    collocations: list[str] = []
    related: list[dict[str, str]] = []

    for note in notes:
        value = clean(note.lstrip("・→ "))
        if not value or len(value) > 150:
            continue
        if note.startswith("・"):
            examples.append(value)
            if "。" not in value and "！" not in value and "？" not in value:
                parts = [clean(part) for part in re.split(r"\s*/\s*|・", value) if clean(part)]
                word = display_word(entry)
                if len(parts) > 1:
                    last_without_reading = re.sub(r"[（(][^）)]*[）)]", "", parts[-1])
                    shared = re.search(rf"([がをにとへでの].*{re.escape(word)})$", last_without_reading)
                    if shared:
                        parts = [part + shared.group(1) for part in parts[:-1]] + [parts[-1]]
                collocations.extend(parts)
        elif note.startswith("→"):
            term = clean(re.split(r"[:：・/]", value, maxsplit=1)[0])
            if term and len(term) <= 30 and re.search(r"[ぁ-んァ-ヶ一-龯]", term):
                word = display_word(entry)
                if word in term and re.search(r"[がをにとへでの]", term):
                    collocations.append(term)
                else:
                    related.append({"type": "related", "term": term})

    word = display_word(entry)
    related.extend({"type": "synonym", "term": term} for term in SYNONYMS.get(word, []))
    related.extend({"type": "antonym", "term": term} for term in ANTONYMS.get(word, []))

    entry["examples"] = list(dict.fromkeys(examples))[:4]
    entry["collocations"] = list(dict.fromkeys(collocations))[:4]
    entry["relations"] = list({(item["type"], item["term"]): item for item in related}.values())[:8]
    rows.append(entry)


def extract(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    lesson = 0
    tier = 0
    waiting_for_tier = False
    current: dict | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for raw_line in (page.extract_text() or "").splitlines():
                line = raw_line.strip()
                lesson_match = LESSON_RE.search(line)
                if lesson_match:
                    lesson = int(lesson_match.group(1))
                if line.startswith("【") and "课" in line:
                    tier = line.count("⭕")
                    waiting_for_tier = tier == 0
                    continue
                if waiting_for_tier and "⭕" in line:
                    tier = line.count("⭕")
                    waiting_for_tier = False
                    continue
                if tier != 3:
                    continue

                match = ENTRY_RE.match(line)
                if match:
                    finish_entry(current, rows)
                    reading = match.group("reading")
                    current = {
                        "source_key": f"chuukyu-1-{lesson}-{page_number}-{len(rows) + 1}",
                        "lesson": lesson,
                        "frequency_tier": tier,
                        "word": (match.group("written") or reading).replace("～", ""),
                        "reading_hiragana": reading,
                        "reading_katakana": to_katakana(reading),
                        "part_of_speech": POS_MAP[match.group("pos")],
                        "meaning_zh": clean(match.group("meaning")),
                        "source_page": page_number,
                        "_notes": [],
                    }
                elif current and (line.startswith("・") or line.startswith("→")):
                    current["_notes"].append(line)

    finish_entry(current, rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = extract(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Extracted {len(rows)} high-frequency noun/verb/adverb entries to {args.out}")


if __name__ == "__main__":
    main()
