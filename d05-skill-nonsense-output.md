# Diagnostic d05: Skill call validates but produces nonsense outputs

**Symptom.** A skill (e.g., `classify_thank_you_candidate`) returns a syntactically valid output (passes output schema validation) but the content is clearly wrong. Example: Kate saying "thanks for hosting" is classified `is_candidate: false` when it should be `true`. Or confidence scores that cluster at 0.5 for everything.

**When to use.** During prompts 09b, 10b, 10c, or any time a pipeline's behavior looks off and the root cause is the skill's judgment.

---

## Read first

1. The skill's `SKILL.md` (the prompt body).
2. The skill's `examples/` directory (positive and negative examples).
3. The failing fixture — what was the input, what did the skill say, what did you expect?
4. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §3 for the canonical example of a well-prompted skill.

## Likely causes (ranked)

1. **Prompt is underspecified.** The SKILL.md doesn't distinguish clearly between positive and negative cases. The model makes reasonable guesses that don't match your intent.
2. **Examples don't cover the failing case.** If you have 3 positive examples and 0 negatives (or vice versa), the model learns a skewed distribution.
3. **Model temperature too high.** Skill manifests often default to a nonzero temperature. For classifiers, temperature should be 0.
4. **Model itself is wrong for the task.** A small model (haiku) might be inadequate for nuanced social-signal classification; reach for a larger model (sonnet) on nuanced tasks.
5. **Input preprocessing issue.** The input reaching the skill isn't what you think. A normalization step (e.g., lowercasing, stripping signatures) might be destroying the signal.

## Procedure

1. **Reproduce.** Run `adminme skill test <skill_id> --fixture <name>` — shows input, raw response, post-processed output.
2. **Inspect raw response.** If the skill returns JSON via structured output, is the JSON what you'd produce by hand if you were the model? If yes, the bug is in the caller. If no, the bug is in the skill.
3. **Run it 5 times** with the same input. Results vary wildly? → temperature too high. Results consistent but wrong? → prompt issue.
4. **Add the failing case to `examples/`** as a negative example (with the correct output). Re-run. Does it now get it right on this input? If yes, that's evidence the example set was inadequate.
5. **Swap the model.** In `pack.yaml`, change `provider: anthropic-claude-sonnet-4-6` to a smaller or larger model. Re-run. If larger fixes it → the task needs more capability.

## Fix pattern

**A.** Temperature 0 for classifiers. Temperature 0 for extractors. Higher temps only for composition (morning_digest, paralysis_nudge).

**B.** Minimum 3 positive + 3 negative examples per classifier skill. Each example should be a concrete, non-obvious case — not a cartoon.

**C.** Explicit rubric in the prompt. Instead of "decide if this is a thank-you candidate", use "decide if this is a thank-you candidate. A thank-you candidate has ALL of: (a) the sender is in the household's close social circle; (b) the content references hospitality received; (c) it's within the last two weeks. If ANY of these is false, `is_candidate: false`."

**D.** Schema constraints. Structured output schemas should include descriptions: `"confidence": {"type": "number", "description": "How confident you are. 0.9+ only for unambiguous cases. 0.5 means genuinely uncertain — better to say false and let a human decide."}`.

## Verify fix

```bash
# Re-run all fixtures for the skill
poetry run pytest packs/skills/<skill>/tests/ -v

# Run the specific failing fixture 10 times
for i in $(seq 1 10); do
  adminme skill test <skill_id> --fixture <name> --json | jq .output.is_candidate
done
# All 10 should produce the correct answer.

# Add the failing case as a fixture (if not already), add it to the test suite
```

## Escalate if

After fix pattern A-D, the skill still produces nonsense on some inputs. This may mean:
- The task is inherently ambiguous and needs human review — reclassify as "review" action gate instead of "allow".
- The model tier is fundamentally inadequate — document this; if it's a supported model you're obligated to use, consider breaking the task into smaller skills that are each easier.

Also: log the failing outputs to `~/.adminme/raw_events/skill_review_queue/<skill>/` for later review. A pattern in the failures is the most useful signal for a prompt rewrite.
