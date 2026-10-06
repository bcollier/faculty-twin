# Roadmap

Ideas for after the October 7 submission. Nothing here is in scope for Tier 1 to 3 in [SPEC.md](SPEC.md); each item moves into the spec before it is built.

## Voice: free and open-source models on the Mac mini

Added October 5, 2026. These run locally on Ben's Mac mini, so they cost nothing per character. Vercel functions cannot run them, so they fit **pre-generated audio** (the 8 to 10 suggested questions, rendered once by the indexer and uploaded under `audio/<voice tag>/`), not live answers. Live answers keep using the hosted tiers (my ElevenLabs clone, ElevenLabs stock voices, free Microsoft voices through edge-tts). Serving a local model for live answers would mean exposing a voice server on the Mac mini to the internet, which the security review advises against.

| Option | My voice? | License | Notes |
| --- | --- | --- | --- |
| [Kokoro](https://github.com/hexgrad/kokoro) | No, stock voices | Apache 2.0 | Excellent quality for its size (82M parameters); fast on the Mac mini's CPU |
| [Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI) | Yes, from about 10 seconds of audio | MIT | Free cloning of my own voice |
| [F5-TTS](https://github.com/SWivid/F5-TTS), [XTTS-v2](https://huggingface.co/coqui/XTTS-v2) | Yes | Weights are non-commercial only | Fine for a class project, not for anything sold |

Not yet verified: current versions, licenses, and quality on my recordings. Check each before building.

**What a first step would look like**

1. Cut 30 to 60 seconds of clean solo speech from my class recordings (instructor-only cues from the de-identified transcripts, so no student voice is in the sample).
2. Render the same three narration segments with Chatterbox (my clone) and Kokoro (stock), next to the ElevenLabs clone, and compare by ear.
3. If one is good enough, add it as a pre-generation option in `indexer/pregenerate.py` and as a voice tier on the Settings page that applies to suggested questions only, with the same disclosure rule: "AI voice made from my recordings" only for a clone of my voice.
