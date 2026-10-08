# How Faculty Twin works: architecture diagrams

*Written by Claude Code (Claude Opus 5.5) from the code on `main` and [SPEC.md](SPEC.md), October 8, 2026. Where this page and the spec disagree, the spec wins.*

Seven diagrams, each with a short explanation. They render on GitHub (Mermaid).

**How Ben's hand-written code is marked.** Four pieces of the app are written by Ben by hand (see [AGENTS.md](../AGENTS.md), "Code Ben writes by hand"): `rank()` and `select_segments()` in `app/retrieval.py`, the not-covered threshold `NOT_COVERED_THRESHOLD = 0.52` in the same file, and `onClipEnded()` in `public/app.js`. In flowcharts they are **yellow boxes with a thick gold border** and the words "Ben's code". In the sequence diagram they sit inside **yellow shaded bands** with a "Ben's code" note.

Contents:

1. [System context](#1-system-context)
2. [One question, step by step](#2-one-question-step-by-step)
3. [How a question is routed](#3-how-a-question-is-routed)
4. [The content pipeline](#4-the-content-pipeline)
5. [Privacy and trust boundaries](#5-privacy-and-trust-boundaries)
6. [The Settings page](#6-the-settings-page)
7. [The eval harness](#7-the-eval-harness)

The data diagrams (every Postgres table and column, the Storage bucket's folders with who writes and reads each, the settings and counter keys, and what stays on the local build machine) are in **[DATABASE.md](DATABASE.md)**.

## 1. System context

```mermaid
flowchart LR
    classDef ben fill:#fff3c4,stroke:#b8860b,stroke-width:3px,color:#3a2e00
    classDef store fill:#eef4f8,stroke:#2c5f73,color:#10303c
    classDef private fill:#fbe9e7,stroke:#a33a2a,color:#4a140c

    subgraph People["People"]
        SB["Student browser<br/>index.html + app.js<br/>no keys"]
        PLAY["onClipEnded() in app.js<br/>player advance<br/>Ben's code"]
        AB["Ben's browser<br/>admin.html (Settings)<br/>admin passcode"]
    end

    subgraph Vercel["Vercel"]
        CDN["Static files<br/>public/ on the CDN"]
        FN["FastAPI function<br/>app/main.py and app/*.py<br/>holds every key"]
        RET["rank(), select_segments(),<br/>NOT_COVERED_THRESHOLD 0.52<br/>app/retrieval.py<br/>Ben's code"]
    end

    subgraph Supa["Supabase"]
        BUCKET[("Private bucket twin-content<br/>index, slides, clips, audio,<br/>Canvas info index, inbox,<br/>evals, prompt history")]
        PG[("Postgres<br/>settings, counters, question_log,<br/>courses, sessions, sources")]
    end

    subgraph Prov["Providers"]
        LLM["Anthropic, OpenAI or OpenRouter<br/>narration, course info,<br/>logistics check, eval judges"]
        VOY["Voyage AI<br/>embeddings"]
        ELV["ElevenLabs<br/>voice clone and stock voices"]
        EDGE["Microsoft edge-tts<br/>free voices, no key"]
    end

    subgraph Src["Where course material comes from"]
        CANVAS["Canvas<br/>pages, syllabus, assignments<br/>read-only token"]
        DRIVE["Google Drive<br/>slide decks, linked docs"]
        ZOOM["Zoom cloud recordings<br/>class video, VTT captions"]
    end

    subgraph Local["Local build machine"]
        ARCH[("Private Lecture Archive<br/>video, raw transcripts, rosters")]
        IDX["indexer/ pipeline<br/>and indexer/worker.py"]
    end

    SB -- "loads the page" --> CDN
    AB -- "loads the page" --> CDN
    SB --- PLAY
    SB -- "/api/* with ft_session cookie" --> FN
    AB -- "/api/admin/* with ft_admin cookie" --> FN
    SB -- "signed links: slides, clips, stored audio" --> BUCKET
    AB -- "signed upload URL" --> BUCKET
    FN --- RET
    FN -- "load index, sign links,<br/>eval and prompt files" --> BUCKET
    FN -- "settings, limits, log" --> PG
    FN -- "de-identified text" --> LLM
    FN -- "the question" --> VOY
    FN -- "signed narration only" --> ELV
    FN -- "signed narration only" --> EDGE
    DRIVE --> ARCH
    ZOOM --> ARCH
    CANVAS -- "student-facing items" --> IDX
    ARCH --> IDX
    IDX -- "slide and info chunk text" --> VOY
    IDX -- "allowlist upload after the leak check" --> BUCKET
    IDX -- "poll and update sources rows" --> PG
    BUCKET -- "inbox files from Settings" --> IDX

    class PLAY,RET ben
    class BUCKET,PG store
    class ARCH private
```

Faculty Twin is one Vercel project: the static page (`public/`) on Vercel's CDN and one FastAPI function (`app/`) that holds every key. The browser never talks to a model or voice provider; it calls `/api/*` with a signed cookie and fetches slide images, clips and stored audio straight from the private Supabase bucket through links the function signed for an hour. Postgres holds the settings, rate-limit counters, the question log and the course catalog. Everything the function serves was built ahead of time on the local build machine, which holds the private Lecture Archive (class video, raw transcripts, rosters) and runs the `indexer/` pipeline and the upload worker. The two yellow boxes are Ben's hand-written code: retrieval on the server and the player's advance logic in the browser.

## 2. One question, step by step

```mermaid
sequenceDiagram
    autonumber
    actor S as Student
    participant UI as Browser (app.js)
    participant API as Vercel function (/api/ask)
    participant PG as Supabase Postgres
    participant V as Voyage AI
    participant M as Model (Claude, OpenAI or OpenRouter)
    participant B as Supabase Storage
    participant T as ElevenLabs or edge-tts

    S->>UI: types a question or taps a chip
    UI->>API: POST /api/ask with question, course filter, source and the ft_session cookie
    API->>API: check the cookie, reject empty or over 300 characters
    API->>PG: rate limits per visitor and per address (ft_increment)
    alt same words as a suggested question
        API-->>UI: stored playlist with fresh signed links (no model call)
    else matches Ben's course FAQ (app/faq.py)
        API-->>UI: kind faq, Ben's written answer, Calendly button, TA card
    else search
        API->>V: embed the question once (input type query)
        V-->>API: question vector
        rect rgb(255, 243, 196)
            Note over API: Ben's code: rank() scores every visible slide and every Canvas chunk with the same vector
        end
        alt best Canvas chunk at least 0.55 and above the best slide
            API->>M: answer only from the top 3 Canvas chunks (app/course_info.py)
            M-->>API: answer JSON, checked, else first sentences of the top chunk
            API-->>UI: kind course_info, the From Canvas card with links
        else slides
            rect rgb(255, 243, 196)
                Note over API: Ben's code: select_segments() keeps slides at or above the threshold 0.52, fills gaps, at most 5, deck order
            end
            alt nothing selected
                API-->>UI: covered false, the not-covered reply
            else slides chosen
                API->>M: logistics check, only if the keyword pre-check did not decide
                alt logistics
                    API-->>UI: kind logistics, "That one is for me directly" with the Calendly button
                else course content
                    API->>M: narration JSON for the chosen slides (grounding prompt)
                    M-->>API: one segment per slide
                    API->>API: validate ids, 110 words, grounding, PG, no names, no access codes, retry once, else speaker notes
                    API->>B: sign image and clip links in one batch (1 hour)
                    API->>API: HMAC-sign each narration into an /api/audio link
                    API-->>UI: playlist with segments, sources, follow-ups
                end
            end
        end
    end
    API->>PG: one question_log row (kind, scores, model, tokens, no names)
    UI->>B: GET the slide image (signed link)
    UI->>API: GET /api/audio with text, voice tag and signature
    API->>API: check the signature, the voice tag and today's character cap
    API->>T: speak the signed text
    T-->>API: mp3 bytes
    API-->>UI: mp3 stream (nothing saved on the server)
    rect rgb(255, 243, 196)
        Note over UI: Ben's code: onClipEnded() runs when a narration clip ends: show the next segment, play it, preload the one after, finish after the last
    end
    S->>UI: taps "Watch me explain this in class"
    UI->>B: GET the class clip (signed link), the walkthrough pauses
```

A question starts in the browser and goes to `/api/ask` with the 7-day `ft_session` cookie. The function checks the cookie, the length and the rate limits, then tries the cheap answers first: a suggested question replays its stored playlist, and Ben's course FAQ answers with his own words, neither calling a model. Otherwise the question is embedded once, and Ben's `rank()` scores both the slides and the Canvas chunks with that one vector; a strong Canvas match becomes a short "From Canvas" answer, and otherwise Ben's `select_segments()` picks the slides against the 0.52 threshold. Chosen slides pass a logistics check and then one narration call, whose JSON is validated in code before anything is signed for the voice. The page shows each slide from a signed link, plays each narration through `/api/audio` (which speaks only text the server signed), and Ben's `onClipEnded()` moves the walkthrough along when each narration ends.

## 3. How a question is routed

```mermaid
flowchart TD
    classDef ben fill:#fff3c4,stroke:#b8860b,stroke-width:3px,color:#3a2e00
    classDef out fill:#eef4f8,stroke:#2c5f73,color:#10303c
    classDef stop fill:#fbe9e7,stroke:#a33a2a,color:#4a140c

    Q["Question arrives at /api/ask<br/>cookie, length and rate limits pass"] --> T{"Same words as a<br/>suggested question?"}
    T -- yes --> TOPIC["Stored walkthrough<br/>kind stored_topic<br/>no search, no model"]
    T -- no --> F{"Matches Ben's course FAQ?<br/>app/faq.py keyword patterns"}
    F -- yes --> FAQ["Ben's written answer, word for word<br/>Calendly button, TA card<br/>kind faq, no model"]
    F -- no --> E["Embed once with Voyage"]
    E --> R["rank() slides and Canvas chunks<br/>Ben's code"]
    R --> C{"Best Canvas chunk at least 0.55<br/>and above the best slide?"}
    C -- yes --> INFO["From Canvas card<br/>short answer from the chunks + links<br/>kind course_info"]
    C -- no --> SEL["select_segments() with threshold 0.52<br/>Ben's code"]
    SEL --> COV{"Any slide selected?"}
    COV -- no --> NC["I don't have course material on that<br/>kind not_covered"]
    COV -- yes --> L{"Logistics?<br/>keyword pre-check, then one small model call"}
    L -- yes --> LOG["That one is for me directly<br/>Calendly button<br/>kind logistics"]
    L -- "no, or the check failed" --> N["Narration call, validators,<br/>signed audio links<br/>kind course_content"]

    class R,SEL ben
    class TOPIC,FAQ,INFO,N out
    class NC,LOG stop
```

Every question takes exactly one path, and the path is saved with it in the question log as its **kind** (the badge in Settings > Activity). The order is cheapest first: stored topics and the FAQ need no embedding and no model. After one embedding, the Canvas course-info index wins only when its best chunk clears its own threshold (0.55 by default) and beats every slide, so a concept question still gets slides. Ben's `select_segments()` decides between slides and "not covered" using the slide threshold (0.52, Ben's value, overridable in Settings > Answer thresholds). The logistics check exists because meeting, absence and grade emails scored just above 0.52 in the October 7 eval; if that check fails for any reason the question is treated as course content, so it can never block a real answer. More detail: [TESTING_AND_SCORES.md](TESTING_AND_SCORES.md#how-a-question-is-answered).

## 4. The content pipeline

```mermaid
flowchart TD
    classDef private fill:#fbe9e7,stroke:#a33a2a,color:#4a140c
    classDef upload fill:#e8f5e9,stroke:#2e7d32,color:#0f3d13
    classDef gate fill:#fff8e1,stroke:#8d6e00,stroke-width:2px,color:#3a2e00

    subgraph In["Private inputs on the local build machine"]
        PDF["slides.pdf / slides.pptx<br/>per session"]
        VTT["Zoom VTT or cleaned<br/>transcript, per session"]
        MP4["Class video<br/>video.mp4"]
        NB["Notebooks .ipynb"]
        ROS["Rosters<br/>_private/rosters/"]
        CVS["Canvas API<br/>read-only token"]
    end

    S1["1. indexer/slides.py<br/>render WebP, text, notes, OCR, flags"]
    S2["2. indexer/deidentify.py + pg_filter.py<br/>names to [student] or [person],<br/>student turns marked, PG swaps"]
    S3["3. indexer/align.py<br/>frame matching, then text fallback"]
    S4["4. indexer/clips.py<br/>instructor-only clips, 15 to 90 s"]
    CI["indexer/canvas_import.py<br/>student-facing items, de-identified,<br/>access codes removed"]
    S5["5. indexer/build_index.py<br/>records + Voyage embeddings<br/>assessment_filter.py"]
    S5B["indexer/build_info_index.py<br/>Canvas chunks + embeddings"]
    PRE["indexer/pregenerate.py<br/>suggested questions + mp3s"]
    LEAK{"indexer/leakcheck.py<br/>full roster scan<br/>one hit stops everything"}
    S6["6. indexer/upload.py<br/>allowlist, skip unchanged,<br/>bump index_version"]
    WK["indexer/worker.py<br/>polls Settings uploads every 30 s,<br/>runs the stages for that session"]

    subgraph Keep["Stays local (_build/)"]
        TR["transcripts/, align/, review/,<br/>slides.json, canvas items.json,<br/>clips/rejected.json"]
    end

    subgraph Up["Uploaded to the private bucket"]
        OUT["content/index.json + embeddings.npy<br/>content/info_index.json + info_embeddings.npy<br/>slides/*.webp, clips/*.mp4 + manifest,<br/>topics/topics.json, audio/*.mp3"]
    end

    PDF --> S1
    NB --> S1
    ROS --> S1
    VTT --> S2
    ROS --> S2
    S1 --> S2
    S2 --> S3
    MP4 --> S3
    S1 --> S3
    S3 --> S4
    MP4 --> S4
    S2 --> S4
    S1 --> S5
    S3 --> S5
    S4 --> S5
    CVS --> CI
    ROS --> CI
    CI --> S5B
    S5 --> PRE
    S5 --> LEAK
    S5B --> LEAK
    PRE --> LEAK
    LEAK -- "zero hits" --> S6
    S6 --> OUT
    S2 -.-> TR
    S3 -.-> TR
    CI -.-> TR
    WK -. "runs stages 1 to 6" .-> S1

    class PDF,VTT,MP4,NB,ROS,CVS,TR private
    class OUT upload
    class LEAK gate
```

Each stage is its own script, run on the local build machine with `uv run`, reading the private archive and writing to `~/Lecture Archive/_build/`; a stage skips work whose inputs have not changed. Slides are rendered and their text, notes and OCR text extracted first; transcripts are then de-identified against the rosters (every person except Ben becomes `[student]` or `[person]`, student turns are dropped, cursing is smoothed), aligned to slides by matching video frames to slide images, and cut into clips only where the rules all pass (instructor only, no masked names, no PG swaps, no student presentations, nothing from the Tesla vs Waymo case). `build_index.py` folds everything into one record per slide and code cell and embeds it with Voyage; `canvas_import.py` and `build_info_index.py` do the same for Canvas course information. The roster leak check is the gate: a single hit stops the upload, and `upload.py` then sends only an allowlist of files and bumps `settings.index_version`, so warm functions reload within a minute. The red boxes never leave the machine. There is no Ben-written code in the pipeline. Commands are in the README under "Rebuild the content".

## 5. Privacy and trust boundaries

```mermaid
flowchart LR
    classDef private fill:#fbe9e7,stroke:#a33a2a,color:#4a140c
    classDef deid fill:#fff8e1,stroke:#8d6e00,color:#3a2e00
    classDef pub fill:#e8f5e9,stroke:#2e7d32,color:#0f3d13

    subgraph L["Never leaves the local build machine"]
        R1["Rosters"]
        R2["Raw Zoom transcripts"]
        R3["Full class video"]
        R4["review/ files, alignment,<br/>de-identified full transcripts"]
        R5["Real eval questions<br/>evals/private/ (git-ignored)"]
    end

    GATE{"De-identify, PG filter,<br/>access-code filter,<br/>roster leak check"}

    subgraph SB["Supabase, private (service role key only)"]
        C1["Slide images, index with<br/>de-identified instructor passages,<br/>clips that passed every rule"]
        C2["question_log: scrubbed question text<br/>and scores, no names, no cookies, no IPs"]
        C3["evals/questions.jsonl<br/>admin routes only"]
    end

    subgraph PV["What providers see"]
        P1["Model: slide text, notes, de-identified<br/>instructor passages, code, the question"]
        P2["Voyage: the question, and<br/>de-identified record text at build time"]
        P3["Voice: the signed narration only"]
    end

    subgraph ST["What a student sees (after the passcode)"]
        U1["Slides, source cards, captions,<br/>AI voice labeled as AI"]
        U2["Class clips labeled<br/>my real voice, not the AI voice"]
        U3["FAQ, Canvas and referral cards"]
    end

    R1 --> GATE
    R2 --> GATE
    R3 --> GATE
    GATE --> C1
    R5 -- "rewritten, checked by evals/dataset.py" --> C3
    C1 -- "signed links, 1 hour" --> U1
    C1 -- "signed links, 1 hour" --> U2
    C1 --> P1
    C1 --> P2
    P1 --> P3
    P3 --> U1

    class R1,R2,R3,R4,R5 private
    class GATE,C1,C2,C3 deid
    class U1,U2,U3 pub
```

Rosters, raw transcripts, the full class video and the review files stay on the local build machine; nothing reads them anywhere else, and they are never committed. What crosses the boundary has been through de-identification, the PG filter, the access-code filter and the roster leak check, and goes only to the private bucket, which the browser reaches through one-hour signed links after the passcode. Model and embedding providers see de-identified slide text, notes, instructor speech, code and the student's question, never a roster; the voice provider sees only narration the server wrote and signed. The question log keeps scrubbed question text and scores with no names, accounts, cookies or addresses (`app/privacy.py`). Students see slides and captions with an AI-voice label that matches the voice actually speaking, and class clips labeled as Ben's real voice. The threat model behind this is in [SECURITY.md](SECURITY.md).

## 6. The Settings page

```mermaid
flowchart LR
    classDef sec fill:#eef4f8,stroke:#2c5f73,color:#10303c
    classDef store fill:#f3eefb,stroke:#5b3a8f,color:#24123f
    classDef ben fill:#fff3c4,stroke:#b8860b,stroke-width:3px,color:#3a2e00

    ADM["admin.html<br/>admin passcode, ft_admin cookie (12 h)<br/>not linked from the student page"]

    M["Model<br/>provider, model id, Test this model"]
    V["Voice<br/>Ben's clone, ElevenLabs, free Microsoft,<br/>captions only, fallback"]
    C["Courses and source material<br/>sessions, visibility, uploads"]
    LA["Limits and access<br/>voice caps, passcode rotation"]
    TH["Answer thresholds<br/>slide 0.52, course info 0.55"]
    P["Prompts<br/>edit, diff, test, history, restore"]
    EV["Evals<br/>question set, calibration, runs, report card"]
    AN["Analytics<br/>spend, tokens, topics, engagement"]
    AC["Activity<br/>today's counters, last 50 questions"]

    SET[("settings table<br/>provider, model, voice_id, caps,<br/>passcode hash, prompt:name,<br/>thresholds, pricing")]
    SRC[("sources table + bucket inbox/")]
    LOGT[("question_log + counters")]
    EVB[("bucket evals/ and prompts/history/")]
    WRK["indexer/worker.py<br/>on the local build machine"]
    NT["NOT_COVERED_THRESHOLD<br/>in app/retrieval.py<br/>Ben's code (the default)"]

    ADM --> M & V & C & LA & P & EV & AN & AC
    LA --> TH
    M --> SET
    V --> SET
    LA --> SET
    TH --> SET
    NT -. "used unless overridden" .-> TH
    P --> SET
    P --> EVB
    C --> SRC
    SRC --> WRK
    EV --> EVB
    AN --> LOGT
    AN --> EVB
    AC --> LOGT

    class M,V,C,LA,TH,P,EV,AN,AC sec
    class SET,SRC,LOGT,EVB store
    class NT ben
```

The Settings page (`public/admin.html`, with `admin.js`, `admin-analytics.js` and `admin-evals.js`) is Ben's control panel, behind a separate admin passcode and a 12-hour cookie. Model, Voice, Limits and access, Answer thresholds and Prompts all write rows in the `settings` table, which every warm function re-reads through a 30-second cache, so a change reaches students within half a minute without a deploy. Courses and source material create `sources` rows and upload files straight from the browser into the bucket's `inbox/`, which the local worker picks up. Prompts and Evals keep their history and results as JSON files in the private bucket; Analytics and Activity read the question log and usage counters. The slide threshold's default is Ben's hand-written `NOT_COVERED_THRESHOLD`; Settings can only override it at run time, and "Reset to default" goes back to his value. See the screenshots in [screenshots/README.md](screenshots/README.md).

Added Oct 8: **Student alerts** (`admin-alerts.js`, `app/admin_alerts.py`). When a student reports a broken quiz, a broken submission, or an API key out of credits, the first step of `answer()` (`app/alerts.py`) works out the course and the Canvas item and texts Ben's cell through Twilio's Messages API, with a scrubbed quote, a 2 hour dedupe, a daily cap, and one alert per visitor per day. Every alert is also stored in the bucket at `alerts/<UTC>.json`, so the Settings panel shows it even when Twilio is not set up. See "Instructor alerts" in [SPEC.md](SPEC.md).

## 7. The eval harness

```mermaid
flowchart TD
    classDef private fill:#fbe9e7,stroke:#a33a2a,color:#4a140c
    classDef share fill:#e8f5e9,stroke:#2e7d32,color:#0f3d13
    classDef ben fill:#fff3c4,stroke:#b8860b,stroke-width:3px,color:#3a2e00

    EM["Questions from Ben's email"] --> RW["Rewritten so no student can be identified:<br/>no names, ids, dates or places, kept PG"]
    RW --> QP["evals/private/questions.jsonl<br/>git-ignored"]
    QP --> DS{"evals/dataset.py checks<br/>emails, URLs, numbers, keys, names"}
    DS --> UP["scripts/upload_eval_questions.py"]
    UP --> QB["bucket evals/questions.jsonl<br/>admin routes only"]

    CAL["8 invented calibration cases<br/>evals/calibration.jsonl"] --> CJ["Calibrate each judge first<br/>evals/calibrate.py or Settings"]

    DS --> CLI["Command line: evals/run.py<br/>--top N, round-robin by category"]
    QB --> UI["Settings > Evals<br/>estimate, confirm, one step per request"]

    CLI --> ANS["The real answer() path<br/>(in-process, http, or a no-course baseline)"]
    UI --> ANS
    ANS --> RS["rank() + select_segments()<br/>Ben's code"]
    RS --> J["Up to 3 judges from different companies<br/>six scores 1 to 5 + pass or fail<br/>evals/rubric.py, app/eval_core.py"]
    CJ -.-> J
    J --> RES["results.jsonl, report.md<br/>question text: private"]
    J --> SUM["summary.json, summary.md<br/>counts and scores only: shareable"]
    SUM --> RC["Report card and run cards<br/>Settings > Evals, Analytics"]

    class EM,RW,QP,QB,RES private
    class SUM,RC share
    class RS ben
```

The eval harness asks how well the twin answers the questions students really send. Questions come from Ben's email, are rewritten so no student can be identified, live only in the git-ignored `evals/private/`, and are checked again by `evals/dataset.py` before any model sees them; a checked copy can be uploaded to the private bucket for Settings > Evals. A run sends each question through the same `answer()` path students use (so Ben's retrieval decides what is covered), then up to three judges from different companies score six dimensions and give a verdict, after each judge has passed the eight invented calibration cases. Only the summary (counts and scores, no question text) is shareable; it feeds the report card in Settings. The command line and Settings share one implementation (`app/eval_core.py`), so their numbers agree. Details: [evals/README.md](../evals/README.md) and [TESTING_AND_SCORES.md](TESTING_AND_SCORES.md#run-evals-from-settings).
