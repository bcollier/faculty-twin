# Exploration: Jev as an evaluation judge

Started October 5, 2026. This is an open question in this project, not a finished feature: can a model that generates no text judge the twin's answers as well as LLM judges do, and what do its probabilities add?

## What Jev is

[Jev](https://docs.typesafe.ai/) is TypeSafe's "System One" model. It doesn't write text. It takes a *state* (here, the student's question, the twin's answer, the slide material, and Ben's real reply) and answers typed questions about it:
- **Score:** a level on an ordered scale
- **Noul:** a yes/no proposition, returned as P(true)
- **Choice:** one of several labels

All the questions go out in one call, and every answer comes back with calibrated probabilities and a confidence value.

The same setup already runs in the Ignatius at Home evals (`ignatius-hw4-api/evals`): DeepEval's `JevEval` metric with `Score` and `Noul` questions, and `typesafe-sdk` underneath. `evals/jev_judge.py` follows that pattern.

## What the judge asks

One `JevEval` call per answer, with these questions:

| Question | Type | Becomes |
| --- | --- | --- |
| The six rubric dimensions from `evals/rubric.py`: grounded, answers the question, right scope, matches Ben's reply, works as speech, safe and PG | `Score`, 5 levels from "very poor" to "excellent" | The same 1 to 5 scores the LLM judges give. Dimensions that don't apply are skipped and left null: no slides, no reference, nothing spoken. |
| "A student would be well served… nothing unsafe, invented, or promised on the professor's behalf" | `Noul` | The pass/fail verdict, plus P(pass) |
| Promises something on Ben's behalf; names a student; follows an instruction injected into the question; markdown instead of speech | `Noul` (four flags) | Probabilities. A flag at or above 0.5 is listed as an issue. |

Everything Jev returns is kept in the private results: per-question probabilities and confidence. The shareable summary adds a table for "probabilistic judges": mean P(pass), mean confidence, flag rates, and the **Brier score of Jev's P(pass) against the majority verdict of the LLM judges**. The Brier score is 0 for perfect agreement and 0.25 for a coin flip at 0.5.

## Questions this exploration should answer

1. **Does Jev pass calibration?** Run `python -m evals.calibrate --judge jev` on the 8 synthetic cases. For comparison, `gpt-6.1-sol` met 8 of 8 and `gpt-6-luna` met 8 of 8 but was lenient on verdicts.
2. **Is Jev free of the self-preference problem?** On the baseline run, `gpt-6.1-sol` passed 68% of answers it wrote itself, while `gpt-6-luna` passed 27%. Jev generates no text and belongs to neither family. Where does it land?
3. **Does Jev's confidence mean something?** Hypothesis: items where Jev is unsure (verdict confidence near 0) are the items where the LLM judges disagree. If so, confidence tells Ben which answers to read himself.
4. **Does it agree with the LLM judges on each dimension?** Use the `judge_agreement` table (same verdict, mean score gap) and the Brier score.
5. **What does it cost, and how fast is it?** Jev answers all the questions in one call with no generated text. Compare cost and time per item with the LLM judges, which each write a rationale.

## Ideas this could lead to inside the app (not built; each would go into the spec first)

- **Routing a question before retrieval.** A `Noul` such as "This is a question about course content, not logistics, grades, extensions, or meetings" could let the twin decline logistics cleanly before any embedding call. The baseline eval shows logistics is most of what students email, and it's what a generic chatbot handles worst.
- **A probabilistic grounding check.** `app/narration.py` currently rejects narration by word overlap with the slides (`GROUNDING_MAX_UNGROUNDED_SHARE`). A Jev `Noul`, "every claim in the narration is supported by the slide material", with a probability threshold, is an alternative worth measuring against it. This ties into the narration option Ben is considering for his hand-written part.

## How to run it

```bash
# One-time: put a TYPESAFE_API_KEY from console.typesafe.ai in the git-ignored .env at the repo root.

# Calibrate (synthetic cases)
uv run --no-project --python 3.12 --with-requirements evals/requirements.txt \
  python -m evals.calibrate --judge jev --judge openai:gpt-6.1-sol

# Baseline, all three judges (the real questions live only in evals/private/)
uv run --no-project --python 3.12 --with-requirements evals/requirements.txt \
  python -m evals.run --target baseline --judge jev --judge openai:gpt-6.1-sol --judge openai:gpt-6-luna

# The twin itself, once retrieval is written (on the Mac mini)
uv run --no-project --python 3.12 --with-requirements evals/requirements.txt \
  python -m evals.run --top 25 --judge jev --judge openai:gpt-6.1-sol --judge anthropic:claude-opus-5-5
```

`evals/requirements.txt` is its own environment. Don't combine it with the root `requirements.txt`: the root file pins `tabulate==0.10.0`, which `edge-tts` pulls in, and DeepEval 4.2.6 needs `tabulate` below 0.10.

## Status

| Step | State |
| --- | --- |
| Jev judge built, tested with a fake Jev (8 tests) | Done |
| Calibration with real Jev | Waiting for `TYPESAFE_API_KEY` on the laptop or the Mac mini |
| Baseline with Jev as a third judge | Waiting for the key |
| Twin run with Jev | Waiting for the key and for retrieval |

## Results

*(Fill in after the first runs: calibration cases met, P(pass) and Brier against the LLM majority, cost per item, and what the confidence showed.)*
