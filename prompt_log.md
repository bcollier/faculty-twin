# Prompt log: Faculty Twin (15-113 Project 2)

## Planning session, Monday October 5, 2026

**Tool:** Claude in the claude.ai app (chat with web search and document tools). Configured model: `claude-fable-5-1`; the app can switch the serving model mid-session.

**What this tool was used for:** choosing the project idea, architecture and hosting decisions, and writing `SPEC.md`. No code was written in this session.

**Earlier brainstorming** (Sept 30 to Oct 4: project ideas, portfolio review, handyman app, Desert Fathers idea) is recorded separately in the Google Doc [15-113 Project 2 brainstorm: conversation handoff](https://docs.google.com/document/d/1LDGRhQspsMy-iV90bvNxpVPu0inkm1S-s5CfHfK5feo/edit).

Prompts below are verbatim, typos included. Responses are summarized.

---

### Prompt 1 (5:03 PM)

> what about creating a digital twin, but it uses my voice and has an avatar? Or I would like a faculty twin where it talks with audio, you ask about "clustering methods" and the chatbot goes off to the side and my slides and code that is relevant appears, and I talk verbally through what is on their screen. could we do this? It would be cool if it is in my voice, or cool if it had a digital avatar video component walk me through

**Response summary.** Yes, buildable, and the "faculty twin" version is the stronger idea because the hard part is retrieval and sequencing, which is explainable code.

- Proposed pipeline: a one-time indexing script turns a deck and notebook into slide images plus embeddings. At question time the backend embeds the question, finds the best four to six slides and related code cells, has a language model write a short narration per slide grounded only in the slide text, notes, and transcripts, sends each narration to text-to-speech in a cloned voice, and returns a playlist.
- Screen design: chat docks to a side panel, the main stage shows slide, code, and captions, and advances when each audio clip ends.
- Voice: ElevenLabs voice cloning from existing lecture audio. Plan tier and cost not verified.
- Avatar: possible through a live avatar service, but recommended as a stretch goal. Cheaper alternative: a short pre-recorded clip of me in the corner during loading.
- Scope for the deadline: one topic, one deck, one notebook first; then audio; then the clip; live avatar cut.
- Suggested pre-generating answers for eight to ten common questions.
- Risks raised: a public page speaking in my voice (lock narration to my materials, label as AI), cost per question (rate limit, daily cap), and publishing slide content.

### Prompt 2 (5:09 PM)

> could I easily embed this into my current page https://ben.collier.phd/

**Response summary.** Yes. The site is static on GitHub Pages, so the interface goes on the page and a separate server holds the keys, the same split as Rink Rivals and Ignatius at Home.

- Three embed options: its own page built through `scripts/build.py`, an "Ask Ben" button on every page, or an iframe around a standalone app.
- Recommendation: build it as a standalone app in its own repo, then frame or link it from the portfolio and add an entry to `data/portfolio.json`.
- Warnings: the site's rebuild rule, cross-origin settings on the backend, browsers blocking audio until a click, and cold starts on the backend host.

### Prompt 3 (5:18 PM)

> ok, I have accounts at Render, Vercel, and now cloudflare so hosting with any should be fine. Create the detailed spec and build guide to getting this project done. do it in its own repo first, then we can integrate it with my portfolio page later. ask me questions about how to make this better

**Response summary.** Wrote "Faculty Twin: Spec and Build Guide" as a document with eleven sections: what we are building, scope tiers, user experience, architecture (with a diagram), data formats, API endpoints, safety and cost rules, an eight-block build guide with a check per block, code I write by hand, the deliverables checklist, and open decisions.

- Defaults it assumed: FastAPI on Render as one service, a JSON file for the index instead of a vector database, ElevenLabs for voice, plain HTML/CSS/JS frontend.
- Hand-written code it assigned to me: cosine similarity and ranking, segment selection, the not-covered threshold, and the player's advance logic, plus three retrieval tests.
- Asked twelve questions. The four it said mattered most: which deck and notebook, whether the session has speaker notes or a transcript, cloned or stock voice, and open access or a passcode.

### Prompt 4 (5:19 PM)

> i don't like the delay from render what other options do I have

**Response summary.** Searched current docs. Render's delay comes from the free plan, which spins services down after about 15 minutes idle.

- Four options compared: paid Render instance (no delay, no spec change), Vercel (small cold start, FastAPI runs as a function), Cloudflare Python Workers (fast, but newest and with package limits), and a keep-warm ping on free Render (unsupported).
- Pointed out a flaw this exposed in its own spec: saving generated audio to local disk would not work on Vercel, Cloudflare, or free Render. Fix: commit pre-generated clips, stream or externally store live ones.
- Recommendation: try Vercel in Block 0 with a 30-minute limit, fall back to paid Render.

### Prompt 5 (5:23 PM)

> could I build it so that you hand it a Canvas URL and it finds all of the Zoom Links and slide decks, downloads all the transcripts from Zoom and then builds the twin based on that?

**Response summary.** Possible through official APIs, and a good version two, but not for the Wednesday build.

- Canvas has a REST API for modules, pages, and files using a personal access token. Zoom's API lists cloud recordings with transcript files (VTT) and needs an OAuth app.
- Obstacles: CMU may restrict Canvas tokens or Zoom apps (not checked), Zoom share links are not recording IDs, student speech in transcripts must be removed with human review, posted decks are often PDFs without notes, and aligning transcript passages to slides is the hardest part.
- Design advice: make this week's indexer read from a plain folder of decks, notebooks, and transcripts, so a later importer only has to fill that folder.

### Prompt 6 (5:27 PM)

> I want it to run for free, would it fit in the free cloudflare budget?

**Response summary.** Hosting yes, the whole project almost. The cloned voice is the one part that will not be free.

- Cloudflare free plan: 100,000 requests per day, 10 ms CPU per request, and 10,000 Workers AI "neurons" per day covering embeddings and text models.
- Tight spots: the 10 ms CPU limit pushes toward a JavaScript backend, the daily AI allowance has no overage on the free plan, and the free models are smaller.
- ElevenLabs free plan is about 10 minutes of speech per month, and sources disagreed on whether it includes cloning. The roughly $5 to $6 tier clearly does.
- Zero-ongoing-cost design: pay for one month to pre-generate the suggested answers in my voice, commit the audio, serve it as static files; live questions get captions only or a stock voice.
- Advised against switching platform and language two days before the deadline unless free mattered more than familiarity.

### Prompt 7 (5:30 PM)

> ok vercel is fine update the plan if that's what you recommend

**Response summary.** Read Vercel's FastAPI guide and updated the spec.

- One Vercel project: FastAPI as a single function in `app/main.py`, frontend and slide images and pre-generated audio in `public/` served from the CDN.
- Nothing saved on the server. Live audio is streamed back; suggested-question audio is generated on my laptop and committed.
- The "only speak text the backend wrote" rule is now enforced with a signed link, since a function cannot remember what it issued.
- Rate-limit and daily-cap counters moved to a Supabase table, which also covers the database requirement.
- Block 0 rewritten for Vercel with a paid Render instance as fallback.
- Flagged as unverified: cold start time on Vercel's free plan with numpy loaded.

### Prompt 8 (5:47 PM)

> give me .md file for spec

**Response summary.** Exported the spec as `SPEC.md`. The diagram became a Mermaid block so GitHub renders it. Also fixed a leftover in Block 4 that still named the old audio route `/api/audio/{hash}`; it is now `/api/audio`.

### Prompt 9 (5:57 PM)

> how do do spec driven developmnet on claude code

**Response summary.** Looked up Anthropic's Claude Code docs and gave a seven-step loop.

1. Commit `SPEC.md` to the repo.
2. Run `/init` and trim `CLAUDE.md` to a few standing rules (spec is the source of truth, one block at a time, do not write my hand-written files, run each block's check, never commit keys).
3. Plan mode (`Shift+Tab`) for one block at a time, referencing `@SPEC.md`.
4. Implement, then run the block's check and show the output.
5. Commit, then `/clear` before the next block.
6. Have a subagent review the diff against the spec.
7. Change the spec before changing the code.

Also suggested having Claude write only the signatures and failing tests for `app/retrieval.py`, so I write the function bodies myself.

### Prompt 10 (5:59 PM)

> copy all the prompts and summary of responses to a prompt_log.md starting with this question

**Response summary.** Produced this file.

---

## Places the AI got it wrong in this session

Candidates for the assignment's "one place AI got it wrong" paragraph. These are facts from the session; the write-up should be in my own words.

1. **Recommended Render without mentioning the cold start.** The first spec defaulted to a single Render service and only listed idle behavior under "not verified." The free plan's spin-down after about 15 minutes surfaced only when I pushed back (Prompt 4).
2. **Designed an audio cache that could not work.** The first spec saved generated audio to the server's disk. Free Render has no persistent disk and Vercel functions keep nothing between requests. The AI caught this itself while answering Prompt 4 and redesigned it in Prompt 7.
3. **Left a stale route in the spec after the redesign.** Block 4 still said `/api/audio/{hash}` after the route changed to a signed-link `/api/audio`. Fixed during the export (Prompt 8).

## Still to add as the build proceeds

- Claude Code prompts for each block (plan, implement, review), verbatim.
- Which tool I used for which job, and why.
- Which code I wrote or substantially changed by hand.

---

## Repo setup session, Monday October 5, 2026

**Tool:** Claude Code (CLI agent in the terminal, running locally in the project folder). Model: Claude Opus 5.5.

**What this tool was used for:** creating the git repo, writing a first README from `SPEC.md`, and pushing to GitHub. No application code was written in this session.

Prompts below are verbatim. Responses are summarized.

### Prompt 1 (5:56 PM)

> create a new repo for faculty_twin folder and push to github main. use contents from SPEC to build a basic readme . put the spec in a docs folder

**Response summary.** Ran `git init` on branch `main`, moved `SPEC.md` to `docs/SPEC.md`, and wrote `README.md` from the spec.

- README sections: what it does, the AI-generated voice notice, demo scenario, scope tiers, architecture and API routes, safety and cost rules, a placeholder for running locally, and a link to the spec.
- Added `.gitignore` with `.env` from the first commit, as the spec requires.
- Left `gpt-digital-twin-guide.pdf` (Stan Waddell's CMU guide, not my work) out of the public repo through `.gitignore`.
- Searched the files for key-like strings before committing; none found.
- Made the initial commit locally. Creating the public GitHub repo was blocked by Claude Code's permission check, so it gave me the `gh repo create` command to run myself.

### Prompt 2 (6:01 PM)

> I added prompt_log.md. log all the prompts from here appended to this, and push all this to github

**Response summary.** Appended this section to `docs/prompt_log.md`, committed it, created the public `bcollier/faculty-twin` repo on GitHub, and pushed `main`.

### Prompt 3 (6:03 PM)

> move it for me . write agents.md based on the same for all the othe repos I've worked on. note that this one is not from gaim so it doesn't use the same gaim specific rules

**Response summary.** On a branch, moved `prompt_log.md` from `docs/` to the repo root, where the assignment requires it, and wrote `AGENTS.md`.

- Based on the portfolio repo's `AGENTS.md` and the shared workspace rules: branch and pull request for every change, squash merge, no em dashes in visitor-facing copy, and steps only I can do called out in the PR body.
- Says plainly that this is not a GAIM repo and overrides `gaim_claude_dev/AGENTS.md`, dropping the design-docs repo, CI minute budget, `needs Ben` label, and platform trigger rules.
- Added project rules from the spec: the spec is the source of truth, one build block at a time, the four pieces of code I write by hand, the prompt log at the repo root, secrets handling, the voice safety rules, and content and privacy limits.
- Merged through a pull request.
