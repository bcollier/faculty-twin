# indexer/: the content pipeline and the upload worker

*AI-generated documentation (Claude Code, Claude Opus 5.5, October 8, 2026). The source of truth is [docs/SPEC.md](../docs/SPEC.md), "Preprocessing pipeline".*

Turns the private lecture archive (slide decks, Zoom captions, class video, notebooks, Canvas) into what the backend serves: slide images, class clips, the search index and the Canvas course-info index, all uploaded to the private Supabase bucket. Everything here runs on the **local build machine** (any computer that holds the private archive), because it needs the rosters and the full video, which never leave that machine. Nothing it reads or writes is committed; outputs go to `~/Lecture Archive/_build/`. The flowchart is in [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#4-the-content-pipeline).

## Files

| File | Stage | What it does |
| --- | --- | --- |
| `slides.py` | 1 | Renders each slide PDF page to WebP (LibreOffice first for a pptx-only deck), extracts text, speaker notes and OCR text (`ocr_vision.swift`, Apple Vision), sets caution flags, extracts notebook cells |
| `deidentify.py` | 2 | Parses the transcripts, replaces every person named except Ben with `[student]` or `[person]`, marks student turns, writes the review files |
| `pg_filter.py` | 2 | Swaps cursing for a mild word in transcripts, slide text and Canvas text; marks changed cues `pg: true` |
| `roster.py` | 2 | One loader for every roster format (course roster, Canvas group export, gradebook) |
| `align.py` | 3 | Matches video frames to slide images to find when each slide was on screen; TF-IDF text matching where frames do not match |
| `clips.py` | 4 | Cuts one 15 to 90 second clip per slide where every rule passes: frame-matched, instructor only, no masked names, no PG swaps, no student presentations, nothing from the Tesla vs Waymo case |
| `canvas_import.py` | info | Pulls student-facing Canvas items (pages, syllabus, assignments, announcements, linked docs) with a read-only token; de-identified, PG, access codes removed |
| `assessment_filter.py` | 5 / info | Keeps quiz, survey and attendance access codes out of slides, transcripts, clips and Canvas text |
| `build_index.py` | 5 | One record per slide and code cell, embedded with Voyage (cached), into `content/index.json` and `content/embeddings.npy`, with a manifest per version |
| `build_info_index.py` | info | Chunks the Canvas items and embeds them into `content/info_index.json` and `content/info_embeddings.npy` |
| `code_map.json`, `suggest_code_map.py` | 5 | The hand-checked slide to notebook-cell mapping, and the script that drafts it for review |
| `pregenerate.py` | topics | Suggested questions: stored playlists and mp3s under `topics/` and `audio/<voice tag>/` |
| `leakcheck.py` | gate | Scans every output against the full roster; one hit stops the upload. Reports where, never what |
| `upload.py` | 6 | Uploads an allowlist built from the index to the bucket, skips unchanged files, prunes old ones, bumps `settings.index_version` |
| `worker.py`, `com.collier.facultytwin.worker.plist` | Settings | Polls the `sources` table every 30 seconds for files uploaded in Settings, copies each into the archive, runs the stages it affects, marks it ready or error. The plist starts it at login |
| `common.py` | all | Paths, `.env` loading, hashing, atomic writes, a small Supabase client |

## How it connects

- Inputs: `~/Lecture Archive/<course>/2026 Fall/<NN> <date> <title>/` (slides, captions, video, notebooks), `~/Lecture Archive/_private/` (rosters, Canvas token, leak allowlist), Canvas.
- Outputs: `~/Lecture Archive/_build/` locally, and through `upload.py` the private bucket `twin-content`, which `app/storage.py` loads.
- Keys: only `build_index.py`, `build_info_index.py` and `pregenerate.py` call Voyage (and the model and voice for `pregenerate.py`); `upload.py` and `worker.py` use the Supabase service role key. All from the git-ignored `.env`.

## Commands (in order, from the repo root)

The pipeline's own packages (pillow, scipy, scikit-learn, python-pptx, pypdf, nbformat, rapidfuzz,
nicknames) are pinned in `indexer/requirements.txt`, never in the root `requirements.txt` (Vercel
bundles that one). `indexer/worker.py` runs every stage with both files and checks at startup that
they install and import. `IX` below is shorthand for those two flags.

```bash
IX="--with-requirements requirements.txt --with-requirements indexer/requirements.txt"
# 1. Slides (all sessions, or --course 70445 --session 6)
uv run --no-project $IX python indexer/slides.py
# 2. De-identify the transcripts
uv run --no-project $IX python indexer/deidentify.py run
# 3. Align slides to the class video
uv run --no-project $IX python -m indexer.align --all
# 4. Cut class clips
uv run --no-project $IX python -m indexer.clips --all
# Canvas course info
uv run --no-project $IX python -m indexer.canvas_import
uv run --no-project --with-requirements requirements.txt python -m indexer.build_info_index
# 5. Build and embed the index, then the suggested questions
uv run --no-project --with-requirements requirements.txt python -m indexer.build_index
uv run --no-project --with-requirements requirements.txt python -m indexer.pregenerate
# 6. Check, then upload (the leak check runs first and stops on any hit)
uv run --no-project --with-requirements requirements.txt python -m indexer.upload --dry-run
uv run --no-project --with-requirements requirements.txt python -m indexer.upload

# The worker for Settings uploads (--once polls a single time)
uv run --no-project --with-requirements requirements.txt python -m indexer.worker
```

Each stage skips work whose inputs have not changed. `build_index` without `VOYAGE_API_KEY` writes everything except the embeddings and exits 3; run it again once the key is in `.env`. The spec mentions a one-shot `indexer.run`; it is not built, so run the stages above in order.
