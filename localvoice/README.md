# localvoice: free local voices for pre-generated audio

This is roadmap steps 1 and 2 from [docs/ROADMAP.md](../docs/ROADMAP.md): free, open-source voices that run on the local build machine (the computer that holds the private archive), for the suggested-question clips only. Live answers keep using the hosted voice tiers (`app/voices.py`), because Vercel can't run these models.

| Engine | Package (checked Oct 5, 2026) | Voice | Label shown to students |
| --- | --- | --- | --- |
| Kokoro | `kokoro` 0.9.4, Apache-2.0, Python 3.10 to 3.12 | Stock voices (`am_michael`, `bm_george`, `af_heart`, ...) | "AI voice (a stock voice, not mine)." |
| Chatterbox | `chatterbox-tts` 0.1.7, MIT | Clone from a reference clip of Ben | "AI voice made from my recordings." Only when the clip is Ben's own voice |

## Rules

- **Heavy packages never go into the root `requirements.txt`.** Vercel bundles that file. Torch, Kokoro and Chatterbox are listed only in `localvoice/requirements.txt`, and a test checks this.
- **The reference clip of Ben's voice comes only from instructor-only stretches of class.** Every cue, padded by 5 seconds on each side, must meet the same rules `indexer/clips.py` uses for class clips:
  - labelled `instructor`
  - no `[student]` or `[person]` token
  - not flagged by the PG check
- **Cutting the clip and any render with it happen on the local build machine.** The clip and the renders are private. Keep the clip under `~/Lecture Archive/_private/`. Renders go to `localvoice/out/`, which is git-ignored.
- **Only a clone of Ben's own voice gets the clone label.** Kokoro, and Chatterbox with anyone else's reference clip, get the stock label.

## Steps

1. **Cut a reference clip.** This runs on the local build machine and needs ffmpeg.

   ```bash
   uv run --no-project --with numpy python -m localvoice.sample \
     --transcript "~/Lecture Archive/_build/transcripts/70445/s06.json" --list
   uv run --no-project --with numpy python -m localvoice.sample \
     --transcript "~/Lecture Archive/_build/transcripts/70445/s06.json" \
     --video "~/Lecture Archive/<course folder>/2026 Fall/06 .../video.mp4" \
     --out "~/Lecture Archive/_private/voice/ben_ref.wav" --seconds 45
   ```

   Use a session with a single-part recording. Listen to the whole clip before using it: no other voice may be audible.

2. **Do a blind A/B by ear.**

   ```bash
   uv run --python 3.12 --no-project --with-requirements localvoice/requirements.txt \
     python -m localvoice.ab --narrations segments.json \
     --engine kokoro:am_michael --engine chatterbox \
     --sample "~/Lecture Archive/_private/voice/ben_ref.wav"
   ```

   - `segments.json` is a list of narration strings or a stored playlist.
   - To include the ElevenLabs clone, put its mp3s for the same narrations in a folder as `elevenlabs_<n>.mp3` and pass `--add <folder>` (or drop them into `--out` before running). They are loudness-matched with ffmpeg, renamed, and shuffled in. This tool never calls ElevenLabs.
   - Open the run folder's `index.html`. Versions are shuffled with random file names, and the page has no voice names. After listening, open `answer_key.json` in the same folder.

3. **Wire in the winner** (roadmap step 3, owned by the indexer pipeline). Add it as a pre-generation option in `indexer/pregenerate.py`, and as a voice tier on the Settings page that applies to suggested questions only.

## Measured

Warm real-time factor is render time divided by audio length; lower is faster.

| Engine | Hardware (local run) | Warm real-time factor |
| --- | --- | --- |
| Kokoro `am_michael` | CPU only | 0.16 |
| Kokoro | Apple GPU (MPS) | 0.11 |
| Chatterbox (clone) | Apple GPU (MPS) | 2.0 |
| edge-tts (free Microsoft) | network call to Microsoft | 0.44 |
| ElevenLabs (hosted) | network call to ElevenLabs | 0.18 |

Notes:
- The first call loads the model: about 25 seconds for Kokoro, against 3 seconds warm. `ab.py` now renders a throwaway sentence per engine before timing.
- Chatterbox uses only about the first 6 to 10 seconds of the reference clip, so pick a window whose opening is clean (`--rank N` cuts the Nth candidate).

## Fixed after the first real run (October 5)

The first A/B run on the local build machine found six problems, all fixed:
- **Missing pin:** `setuptools<81` was missing from `localvoice/requirements.txt`, so Chatterbox failed with `TypeError: 'NoneType' object is not callable`.
- **Crashes:** one engine crashing stopped the whole run.
- **Hand-made clips:** ElevenLabs clips dropped into the run folder never appeared on the page and weren't loudness-matched.
- **Not truly blind:** file names and hidden labels gave the answer away, and the order never changed. Files now get random names, the answer key is a separate `answer_key.json`, and the shuffle is seeded (`--seed`).
- **Timing:** model loading was counted in segment 1.
- **Permissions:** the reference clip was readable by every account on the machine. It's now mode 600, and folders under `_private/` are 700.
