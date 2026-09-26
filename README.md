# UFO Files Tracker

A pipeline and web app that tracks the U.S. government's declassified UFO / UAP
file releases. It checks for new releases, downloads the files, extracts their
text (OCR for scanned pages), classifies what each record is about, looks for
connections across records (waves of reports, recurring details, clusters of
similar cases), and shows it all in a visual site anyone can browse.

The main source is **PURSUE** (Presidential Unsealing and Reporting System for
UAP Encounters) at [war.gov/UFO](https://www.war.gov/UFO/). The Department of
War has published six tranches there since May 8, 2026 (447 records as of the
Sept 18, 2026 release), and more are expected.

```
 ┌───────────┐  ┌────────────┐  ┌────────────────┐  ┌──────────────┐  ┌────────────────┐  ┌─────────┐
 │ discover  │─▶│  download  │─▶│ extract text   │─▶│  classify    │─▶│  connections   │─▶│ web app │
 │ CSV index │  │ direct, or │  │ PyMuPDF / page │  │ rules always │  │ details, waves │  │ FastAPI │
 │ diff vs DB│  │ Wayback,   │  │ + Tesseract    │  │ + Claude opt.│  │ places, links, │  │ + JSON  │
 │           │  │ zip bundle │  │   OCR          │  │              │  │ clusters       │  │         │
 └───────────┘  └────────────┘  └────────────────┘  └──────────────┘  └────────────────┘  └─────────┘
```

## How it works

**Discovery** (`ufo/sources/pursue.py`): the PURSUE page is built from one CSV
index (`/Portals/1/Interactive/2026/UFO/uap-data.csv`) with a row per record:
ID, title, type (PDF / video / image / audio), agency, incident date and
location, description, and file link. Each run reads the current CSV URL off the
landing page (it gets a new cache-busting query string with each release),
parses it, and diffs it against the database:

* new records are queued and processed;
* records whose published metadata changed are re-classified (and re-downloaded
  if the file link changed);
* new release dates become new `Release` rows, numbered in order.

**Fetching** (`ufo/fetch.py`): government sites sit behind Akamai, which often
blocks cloud and datacenter IP ranges. Every request tries the official URL
first and falls back to the Internet Archive's byte-identical copy
(`web.archive.org/web/<ts>id_/<url>`). Files that are in neither place are
marked `unavailable` and retried on every run. As a last resort, the pipeline
downloads that release's official zip bundle (linked from the PURSUE page) and
extracts the missing files by name. It also asks the Archive to capture
anything missing, so those files usually turn up on a later run.

**Extraction** (`ufo/extract.py`): PDFs are read page by page with PyMuPDF. A
page with (almost) no embedded text, or a page that is mostly a scanned image
under a thin text layer, is rendered at 300 DPI and OCR'd with Tesseract. Each
page stores the method used (`text` / `ocr` / `empty`) and the OCR confidence,
and the web app shows both. `OCR_MODE=always` OCRs every page and keeps
whichever version reads better. Some released files carry a poor government OCR
layer, and this setting fixes those.

**Classification** (`ufo/classify/`): every record gets labels in a fixed
taxonomy (`ufo/classify/taxonomy.py`):

| Facet | Examples |
|---|---|
| Document type | mission report, investigation file, transcript, diplomatic cable, scientific study… |
| Topic | military encounter, nuclear, space & astronauts, maritime/USO, crash/retrieval, advanced tech… |
| Reported shape | orb, disc, cigar, triangle, tic-tac, lights, formation… |
| Domain | air, space, sea, undersea, land |
| Witness | military pilot, law enforcement, civilian, astronaut, scientist… |
| Evidence / sensor | visual, radar, infrared, video, photo, satellite, sonar |
| Official assessment | unresolved, or explained as balloon / aircraft / birds / satellite / astronomical / artifact, hoax |
| Program | Project Blue Book, Sign/Grudge, Robertson Panel, Condon, AAWSAP/AATIP, UAP Task Force, Apollo/Gemini/Mercury |
| Region / era | derived from incident location and date |

* The **keyword rules** classifier always runs and needs no API key. It uses the
  publisher's title and description (high weight) and the extracted text (the
  number of matches required grows with document length).
* When an LLM key is set, the model classifies each record from its metadata
  and extracted text using structured outputs. That adds a plain-language
  summary, key points, a 1–5 significance score, and the places, people and
  organizations named. If a call fails, the record keeps its rules-based
  result.
  * Provider: whichever key is present. `ANTHROPIC_API_KEY` selects **Claude**
    (`claude-opus-5`, with the API's server-side refusal fallback).
    `OPENAI_API_KEY` selects **OpenAI** (`gpt-6-luna`, reasoning effort `none`). With both set, Claude
    is used unless `LLM_PROVIDER=openai`.
  * When a key is added or the model changes, records classified by the rules
    or another model are re-classified on the next start.
* The same model **tags sighting accounts**. The keyword rules pick candidate
  paragraphs; the model reads each one and returns the details it actually
  reports, with the words each came from. It drops blank forms,
  instructions, names and explanations. Results are cached by text and model,
  so each paragraph is sent once. Paragraphs whose call fails keep their
  rule-based tags and are retried on the next run.

**Connections** (`ufo/analyze/`): after each run that brings in new or
changed records, the pipeline re-analyses the whole collection (about 30
seconds for the current ~450 records and 10,000 pages):

* **Sighting pages.** Only pages that read like sighting reports are analysed
  (enough words such as *observed, object, sighted, altitude*), plus each
  record's own description. Contracts and research papers only count where a
  page reads like a report.
* **Recurring details** (`features.py`). Pages are searched for concrete
  observables: halo or glow, flashing, coloured lights, metallic surface,
  humming, silence, smells, hovering, extreme speed, right-angle turns,
  splitting objects, entering water, no wings or exhaust, trails, formations,
  electrical or engine interference, instrument anomalies, effects on witnesses,
  ground traces, radar, and infrared. Each hit stores the sentence it came
  from. Two kinds of false match are dropped: negations ("no sound", "no
  exhaust trail") and empty form fields ("Odor detected: None").
* **Dates and places** (`dates.py`, `places.py`). Dates are found in OCR'd
  forms ("July 7, 1947", "07JUL47", "7/7/47"). Places are U.S. states (with
  well-known places such as Roswell, Mount Rainier, Wright-Patterson) and
  countries. Bare "Washington" is treated as the FBI letterhead, not a
  sighting place.
* **Waves.** Sighting pages are counted per year from the dates they mention.
  A wave is a year with at least 2.5× the median of the surrounding two
  decades. For each wave the analysis lists the peak month, the details that
  were more common than usual, and the places. Waves found so far: summer 1947
  (13× the usual level), July 1952, June 1954, March 1966 (Michigan; radar and
  humming stand out), 2019–23, and 2025.
* **Reported together.** Pairs of details that appear on the same page more
  often than chance ("lift"), e.g. *no wings + silent*.
* **Sighting accounts** (`events.py`). Each sighting page is cut into
  accounts: paragraphs, or one report form. Each account is tagged with what
  was observed, in eleven dimensions: shape, colour, light (glow, halo,
  flashing, beams), sound (silent, hum, whoosh, roar, whistle, bang), movement
  (hovering, sudden acceleration, abrupt turns, wobbling, spinning, vertical
  climb, landing, pacing a vehicle, vanishing), structure (no wings, trail,
  dome, windows, rim), effects (engine failure, instruments, witnesses,
  ground traces, odour, heat, animals), number of objects, duration, time of
  day, and how it was observed. The tagger skips negations ("no sound"),
  printed form labels and questions ("b. Roar, whistle, whoosh.", "Did the
  object hover?"), option lists ending in "etc.", blank questionnaires, and
  shapes named only in an explanation ("probably a meteor"). An account needs
  at least two details of the phenomenon to be kept.
* **Sighting types, matches and links** (`signatures.py`). This step uses
  only the event tags. Agency, archive series, place, decade and wording play
  no part. Each tag is weighted by its rarity and its dimension (movement,
  sound and effects count most; time of day and duration least).
  * Two accounts match when they share at least two details of the phenomenon
    itself, one of them about its light, sound, movement, structure or effects.
  * A *sighting type* is a combination of two or three details that recurs in
    at least three records far more often than chance ("Flashing · Hovering ·
    Red/orange"). Every account of a type has all of its details.
  * The map places every account by tag similarity (t-SNE).
  * A record's similar records, and the cross-links between records from
    different agencies or at least 15 years apart, come from their
    best-matching accounts. They need at least three shared details, or a copy
    of the same report. Files from the same published series don't count.

Run it by hand with `python -m ufo analyze`.

## Web app

`uvicorn ufo.web.app:app` serves:

* **Overview** (`/`): totals, the latest release's most notable records,
  records per release, incident decades, and breakdowns by topic, type,
  assessment, shape, witness, sensor, region, location, agency and program.
  Every bar links to the matching filtered list.
* **Releases** (`/releases`): a strip plot of each release's incident years
  (one dot per record), each release's share by agency, and a release × topic
  heatmap. Each tranche is then listed with its main topics, agencies and
  highlights.
* **Patterns** (`/patterns`):
  * a timeline of reports with the waves shaded, and a card per wave;
  * recurring details with decade sparklines;
  * a decade × detail heatmap and the pairs of details reported together;
  * rare details with their quotes;
  * a U.S. tile map and a world breakdown;
  * sighting types on a map of sighting accounts, highlighted on hover, each
    with its own page listing every account (`/patterns/types/{id}`);
  * connections across agencies and decades.

  Every mark links to `/patterns/evidence?feature=&year=&place=`, which lists
  the matching sighting pages with quotes.
* **Connections** (`/patterns/links`): every cross-link, with its
  best-matching pair of accounts. `/patterns/compare?a=&b=` compares any two
  records account by account. Each matching pair shows:
  * the details both describe, highlighted in the text;
  * the details only one of them mentions;
  * whether the two are copies of the same report;
  * dates and places both pages mention.

  Archive metadata is kept in a separate, collapsed section and is not used
  for matching.
* **Browse** (`/documents`): full-text search across titles, summaries and
  OCR'd text, with filters by release, agency, media type and any label.
* **Document** (`/documents/{id}`): metadata, summary, labels, related records,
  the details found in the record (with quotes), its tagged sighting accounts,
  similar records elsewhere and
  the sighting types it contains, a link to the original, and the extracted text page by
  page, with OCR pages and their confidence marked.
* **Pipeline** (`/pipeline`): processing status and recent runs with logs.
* **JSON API**: `/api/stats`, `/api/patterns`, `/api/documents?q=&release=&agency=&media=&tag=facet:value`,
  `/api/documents/{id}`. `POST /api/pipeline/run` with
  `Authorization: Bearer $ADMIN_TOKEN` triggers a run.

The web process runs the pipeline in a background thread on startup and then
every `PIPELINE_INTERVAL_HOURS` (once a day by default), counted from the last finished run so redeploys
don't trigger extra runs. Runs are guarded so they never overlap.

## Deploying to Railway

1. Create a project from this repo. Railway builds the `Dockerfile`, which
   installs Tesseract, and uses `railway.json`, which sets the `/healthz`
   healthcheck.
2. Add a **Volume** mounted at `/data`, which is where the SQLite database and
   downloaded files live. Alternatively, add a **Postgres** service and set
   `DATABASE_URL=${{Postgres.DATABASE_URL}}`. Downloaded files still go to
   `/data` (set `KEEP_FILES=false` if you don't want to keep them).
3. Optional variables:
   * `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`: enables LLM summaries,
     classification and event tagging, using whichever key is set.
   * `ADMIN_TOKEN`: enables `POST /api/pipeline/run` (full run) and
     `POST /api/analysis/run` (patterns only).
   * `PIPELINE_INTERVAL_HOURS` (default `24`).
4. Deploy. On first boot the app loads the bundled snapshot
   (`data/seed/ufo-seed.json.gz`) into an empty database so the site has data
   straight away, then the pipeline picks up anything newer.

Keep one replica. The scheduler runs inside the web process.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `data` (`/data` in Docker) | SQLite DB + downloaded files |
| `DATABASE_URL` | SQLite in `DATA_DIR` | Postgres URL (Railway style `postgres://` is accepted) |
| `FETCH_MODE` | `auto` | `auto` (direct, then Wayback), `direct`, or `wayback` |
| `REQUEST_ARCHIVE` | `true` | ask the Internet Archive to capture files we cannot reach |
| `DOWNLOAD_WORKERS` | `3` | parallel downloads (the Internet Archive resets connections under heavier load) |
| `KEEP_FILES` | `true` | keep PDFs after extraction (needed for `reprocess extract`) |
| `OCR_ENABLED` | `true` | OCR scanned pages |
| `OCR_MODE` | `auto` | `auto` = only pages without usable text; `always` = OCR every page, keep the better text |
| `OCR_DPI` / `OCR_LANG` / `OCR_WORKERS` | `300` / `eng` / `2` | Tesseract settings |
| `OCR_MIN_CHARS` | `80` | pages with fewer embedded characters are OCR'd |
| `ANTHROPIC_API_KEY` | — | enables Claude for classification and event tagging |
| `OPENAI_API_KEY` | — | enables OpenAI for classification and event tagging |
| `LLM_PROVIDER` | `auto` | `auto` (the key that is set; Claude if both), `anthropic` or `openai` |
| `LLM_MODEL` | `claude-opus-5` / `gpt-6-luna` | model for the active provider |
| `OPENAI_REASONING_EFFORT` | `none` | thinking level for OpenAI reasoning models (`none`, `low`, `medium`, `high`, …) |
| `LLM_TAGGING` | `true` | let the LLM tag sighting accounts (otherwise keyword rules) |
| `LLM_WORKERS` | `4` | concurrent tagging requests |
| `LLM_MAX_CHARS` | `300000` | longer texts are sent as head + tail, and the record is flagged `llm_input_truncated` |
| `SCHEDULER_ENABLED` | `true` | run the pipeline from the web process |
| `RUN_PIPELINE_ON_STARTUP` | `true` | run once at boot |
| `PIPELINE_INTERVAL_HOURS` | `24` | how often to check for new releases |
| `ADMIN_TOKEN` | — | bearer token for `POST /api/pipeline/run` and `POST /api/analysis/run` |

## Running locally

```bash
sudo apt-get install tesseract-ocr     # or: brew install tesseract
pip install -r requirements-dev.txt

python -m ufo run                # check sources, download, extract, classify
python -m ufo run --limit 10     # only process 10 pending records
python -m ufo status             # counts by processing status
python -m ufo reprocess classify # re-label everything (e.g. after adding an API key)
python -m ufo reprocess extract DOW-UAP-D102   # re-extract one record
python -m ufo extract-file some.pdf --force-ocr
python -m ufo analyze            # recompute waves, recurring details, clusters and links
python -m ufo export-seed        # refresh data/seed/ufo-seed.json.gz

uvicorn ufo.web.app:app --reload
pytest
```

## Adding another source

Subclass `ufo.sources.base.Source`, implement `list_records()` to return a
`RecordInfo` for everything the source currently publishes, and register it in
`ufo/sources/__init__.py`. Diffing, downloading, OCR, classification and the web
app work unchanged. Candidates include AARO case-resolution reports
(aaro.mil), the NARA UAP Records Collection (RG 615), and the FBI Vault.

## Caveats

* Summaries and labels are generated automatically. Keyword rules are coarse,
  and OCR of old or redacted scans is noisy. Every record links back to the
  original file.
* "Official assessment" reports what the record itself concludes, not a
  judgment by this project.
* Patterns are statistical. A wave means more *released pages* mention a
  period, which depends on what the government chose to release, and not
  necessarily on more sightings. A link means two records are described in
  similar terms. The evidence pages exist so every pattern can be checked
  against the source text.
