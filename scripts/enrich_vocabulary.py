"""Enrich vocabulary JSON with JMdict relations/examples and katakana readings.

Inputs are downloaded separately so this script is deterministic and does not
make hundreds of requests. JMdict_e_examp supplies dictionary relations and
curated Tatoeba examples; the per-language Tatoeba export is a fallback.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from fugashi import Tagger
from pykakasi import kakasi


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
TOKEN_RE = re.compile(r"[a-z]{3,}")
KANJI_RE = re.compile(r"[一-龯々〆ヵヶ]")
converter = kakasi()
tagger = Tagger()

EXAMPLE_OVERRIDES = {
    "壁飾り": "玄関に季節の壁飾りを飾りました。",
    "ことづける": "受付の人に伝言をことづけました。",
    "土産物屋": "駅前の土産物屋でお菓子を買いました。",
    "書き間違える": "申込書に住所を書き間違えました。",
    "言い間違える": "緊張して相手の名前を言い間違えました。",
    "投げ返す": "彼は飛んできたボールをすぐに投げ返しました。",
}

TRANSITIVITY_OVERRIDES = {
    "がっかりする": "intransitive",
    "おしゃれする": "intransitive",
    "張り切る": "intransitive",
    "プロポーズする": "intransitive",
    "ヒットする": "both",
    "関": "intransitive",
    "書き間違える": "transitive",
    "投げ返す": "transitive",
    "対する": "intransitive",
}


def katakana(value: str) -> str:
    converted = []
    for token in tagger(value):
        reading = getattr(token.feature, "kana", None)
        converted.append(reading or "".join(part["kana"] for part in converter.convert(token.surface)))
    result = "".join(converted)
    # UniDic prefers the formal ワタクシ reading for unannotated 私. The
    # textbook consistently uses the neutral learner reading わたし.
    return result.replace("ワタクシタチ", "ワタシタチ").replace("ワタクシ", "ワタシ")


def hiragana(value: str) -> str:
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in katakana(value))


def phonetic_guide(value: str) -> list[dict[str, str]]:
    segments = []
    for token in tagger(value):
        reading = getattr(token.feature, "kana", None)
        segments.append(
            {
                "text": token.surface,
                "reading": hiragana(token.surface) if reading and KANJI_RE.search(token.surface) else "",
            }
        )
    return segments


def target_word(value: str) -> str:
    return (value or "").split("・", 1)[0].strip()


def gloss_tokens(glosses: list[str]) -> set[str]:
    ignored = {"the", "and", "with", "from", "into", "that", "this", "one", "ones"}
    return {token for text in glosses for token in TOKEN_RE.findall(text.lower()) if token not in ignored}


def load_jmdict(path: Path) -> tuple[dict[str, list[dict]], dict[str, str]]:
    index: dict[str, list[dict]] = defaultdict(list)
    reading_by_form: dict[str, str] = {}
    opener = gzip.open if path.suffix == ".gz" else open

    with opener(path, "rb") as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag != "entry":
                continue
            forms = [node.text for node in element.findall("./k_ele/keb") if node.text]
            readings = [node.text for node in element.findall("./r_ele/reb") if node.text]
            keys = list(dict.fromkeys(forms + readings))
            if not keys:
                element.clear()
                continue

            senses = []
            for sense in element.findall("sense"):
                examples = []
                for example in sense.findall("example"):
                    japanese = next(
                        (node.text for node in example.findall("ex_sent") if node.get(XML_LANG) == "jpn"),
                        None,
                    )
                    if japanese:
                        examples.append(japanese)
                senses.append(
                    {
                        "parts_of_speech": [node.text for node in sense.findall("pos") if node.text],
                        "xrefs": [node.text for node in sense.findall("xref") if node.text],
                        "antonyms": [node.text for node in sense.findall("ant") if node.text],
                        "glosses": [node.text for node in sense.findall("gloss") if node.text],
                        "examples": examples,
                    }
                )
            record = {"forms": forms, "readings": readings, "senses": senses}
            for key in keys:
                index[key].append(record)
            primary_reading = readings[0] if readings else ""
            for form in forms:
                reading_by_form.setdefault(form, primary_reading)
            for reading in readings:
                reading_by_form.setdefault(reading, reading)
            element.clear()

    return index, reading_by_form


def best_record(entry: dict, index: dict[str, list[dict]]) -> dict | None:
    candidates = index.get(entry["word"], []) + index.get(entry["reading_hiragana"], [])
    if not candidates:
        return None
    for record in candidates:
        if entry["word"] in record["forms"] and entry["reading_hiragana"] in record["readings"]:
            return record
    return candidates[0]


def related_gloss_overlap(source_sense: dict, target: str, index: dict[str, list[dict]]) -> float:
    source = gloss_tokens(source_sense["glosses"])
    records = index.get(target_word(target), [])
    if not source or not records:
        return 0.0
    target_tokens = gloss_tokens(
        [gloss for record in records for sense in record["senses"] for gloss in sense["glosses"]]
    )
    if not target_tokens:
        return 0.0
    return len(source & target_tokens) / min(len(source), len(target_tokens))


def relation(term: str, relation_type: str, reading_by_form: dict[str, str]) -> dict:
    term = target_word(term)
    reading = reading_by_form.get(term, "")
    return {
        "type": relation_type,
        "term": term,
        "term_reading_katakana": katakana(reading or term),
    }


def example(sentence: str, source: str) -> dict:
    return {
        "sentence": sentence,
        "reading_hiragana": hiragana(sentence),
        "phonetic_guide": phonetic_guide(sentence),
        "source": source,
    }


def add_tatoeba_fallback(rows: list[dict], path: Path) -> None:
    missing = {row["word"]: row for row in rows if not row["examples"] and len(row["word"]) >= 2}
    if not missing:
        return
    opener = bz2.open if path.suffix == ".bz2" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) != 3:
                continue
            sentence = parts[2]
            if not 6 <= len(sentence) <= 70:
                continue
            for word in list(missing):
                if word in sentence:
                    missing[word]["examples"] = [example(sentence, "Tatoeba")]
                    del missing[word]
            if not missing:
                break


def generated_example(row: dict) -> str:
    if row["word"] in EXAMPLE_OVERRIDES:
        return EXAMPLE_OVERRIDES[row["word"]]
    if row.get("collocations"):
        value = row["collocations"][0].strip()
        return value if value.endswith(("。", "！", "？")) else f"{value}。"
    if row["part_of_speech"] == "noun":
        return f"{row['word']}について詳しく調べました。"
    if row["part_of_speech"] == "adverb":
        return f"先生は「{row['word']}」の使い方を説明しました。"
    return f"「{row['word']}」という言葉を使って文を作りました。"


def enrich(rows: list[dict], jmdict: Path, tatoeba: Path | None) -> list[dict]:
    index, reading_by_form = load_jmdict(jmdict)
    for row in rows:
        record = best_record(row, index)
        existing = row.get("relations", [])
        synonyms = [item for item in existing if item["type"] == "synonym"]
        antonyms = [item for item in existing if item["type"] == "antonym"]
        related = [item for item in existing if item["type"] == "related"]
        dictionary_examples: list[str] = []

        if record:
            transitivity_tags = {
                value
                for sense in record["senses"]
                for value in sense["parts_of_speech"]
                if value in {"transitive verb", "intransitive verb"}
            }
            if transitivity_tags == {"transitive verb", "intransitive verb"}:
                row["transitivity"] = "both"
            elif "transitive verb" in transitivity_tags:
                row["transitivity"] = "transitive"
            elif "intransitive verb" in transitivity_tags:
                row["transitivity"] = "intransitive"
            else:
                row["transitivity"] = None
            for sense in record["senses"]:
                dictionary_examples.extend(sense["examples"])
                antonyms.extend({"type": "antonym", "term": value} for value in sense["antonyms"])
                synonyms.extend(
                    {"type": "synonym", "term": value}
                    for value in sense["xrefs"]
                    if related_gloss_overlap(sense, value, index) >= 0.6
                )
        else:
            row["transitivity"] = None
        if row["word"] in TRANSITIVITY_OVERRIDES:
            row["transitivity"] = TRANSITIVITY_OVERRIDES[row["word"]]

        normalized_relations = []
        seen = set()
        for item in synonyms + antonyms + related:
            normalized = relation(item["term"], item["type"], reading_by_form)
            key = (normalized["type"], normalized["term"])
            if normalized["term"] and normalized["term"] != row["word"] and key not in seen:
                normalized_relations.append(normalized)
                seen.add(key)

        if not any(item["type"] == "synonym" for item in normalized_relations):
            normalized_relations.append(
                {"type": "synonym", "term": "無自然対応", "term_reading_katakana": ""}
            )
        if not any(item["type"] == "antonym" for item in normalized_relations):
            normalized_relations.append(
                {"type": "antonym", "term": "無自然対応", "term_reading_katakana": ""}
            )
        row["relations"] = normalized_relations[:12]

        original_examples = row.get("examples", [])
        sentences = []
        for item in original_examples:
            sentences.append(item["sentence"] if isinstance(item, dict) else item)
        sentences.extend(dictionary_examples)
        row["examples"] = [example(value, "PDF" if value in sentences[: len(original_examples)] else "JMdict/Tatoeba") for value in list(dict.fromkeys(sentences))[:4]]

    if tatoeba:
        add_tatoeba_fallback(rows, tatoeba)
    for row in rows:
        if not row["examples"]:
            row["examples"] = [example(generated_example(row), "generated")]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--jmdict", type=Path, required=True)
    parser.add_argument("--tatoeba", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    enrich(rows, args.jmdict, args.tatoeba)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Enriched {len(rows)} entries")


if __name__ == "__main__":
    main()
