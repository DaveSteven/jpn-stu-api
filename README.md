# Japanese Study API

FastAPI backend for the local Japanese study app.

## Run

```bash
docker compose up --build
```

API docs:

```text
http://127.0.0.1:8000/docs
```

The local Docker setup runs PostgreSQL and stores it in the `postgres_data`
Docker volume.

## Import Questions

Copy normalized `books.json` files into `import_data/`, then run:

```bash
docker compose run --rm api python scripts/import_questions.py
```

## Import Vocabulary

The bundled `import_data/vocabulary.json` contains the high-frequency
(three-circle) nouns, verbs, and adverbs extracted from the supplied course PDF.

```bash
docker compose run --rm api python scripts/import_vocabulary.py
```

To regenerate the JSON after replacing the source PDF, run the extraction
script from the workspace root with `pdfplumber` installed:

```bash
python japanese_study_api/scripts/extract_vocabulary_pdf.py SOURCE.pdf \
  --out japanese_study_api/import_data/vocabulary.json
```

To add dictionary relations, example sentences, and katakana readings, install
the enrichment-only dependencies and download the current official datasets:

```bash
pip install -r japanese_study_api/requirements-enrichment.txt
curl -o /tmp/JMdict_e_examp.gz https://www.edrdg.org/pub/Nihongo/JMdict_e_examp.gz
curl -o /tmp/jpn_sentences.tsv.bz2 \
  https://downloads.tatoeba.org/exports/per_language/jpn/jpn_sentences.tsv.bz2
python japanese_study_api/scripts/enrich_vocabulary.py \
  --input /tmp/vocabulary.raw.json \
  --jmdict /tmp/JMdict_e_examp.gz \
  --tatoeba /tmp/jpn_sentences.tsv.bz2 \
  --out japanese_study_api/import_data/vocabulary.json
```

JMdict cross-references are only promoted to synonyms when their dictionary
glosses strongly overlap. Words without a natural synonym or antonym are
explicitly marked as such instead of receiving an invented relation.

## Render Deployment

This project includes a `render.yaml` Blueprint for a Docker web service plus
a Render PostgreSQL database.

1. Push this repository to GitHub, GitLab, or Bitbucket.
2. In Render, create a new Blueprint and select this repository.
3. Review the `japanese-study-api` web service and `japanese-study-db`
   PostgreSQL database.
4. Set these prompted environment variables:

```text
CORS_ORIGINS=https://your-frontend-domain
DEFAULT_DAVID_PASSWORD=replace-with-a-real-password
```

The Blueprint wires `DATABASE_URL` to the Render PostgreSQL internal connection
string. The pre-deploy command imports the bundled `import_data` question data
before the service starts.

If you create the Render web service manually instead of using the Blueprint,
create a Render PostgreSQL database first and add this environment variable to
the web service:

```text
DATABASE_URL=<your Render PostgreSQL internal connection string>
```

Health check:

```text
/api/health
```
