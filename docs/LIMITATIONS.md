# Known limitations

This document is deliberately blunt. CipherForge does not guarantee complete
anonymisation, and the honest position is that its contextual understanding is
useful but incomplete. Everything below is either measured, observed in the
test suite, or a known property of the components used.

## How to read the metrics

The evaluation harness (`python -m tests.evaluation`) reports
`P=0.983  R=0.983  F1=0.983` over 73 synthetic examples with 115 gold spans.

**This is not a claim of real-world accuracy.** The dataset and the recognisers
were written by the same author in the same session, which biases the numbers
upward. The dataset is small, uses a narrow set of phrasings, and its
"hard" cases were written with the fixes already in mind. Treat the number as a
regression guard and an internal-consistency check, not as a product claim.

Span matching in the harness uses type equality plus any overlap. `exact=0.982`
in the report means 98.2% of true positives also matched the boundary exactly.

## Where detection is genuinely weak

* **Coreference across sentences.** "Rahul Sharma and Rahul are the same
  person" is the one case the current build still gets wrong: it detects both
  names but does not always merge the identity, and can leave one span typed as
  a location. This is the main remaining failure in the synthetic set (2 FN,
  1 FP) and it is a real limitation, not a test artefact. True coreference
  resolution needs a dedicated model or a mention-clustering step.

* **Names spaCy and the cue patterns both miss.** A name that is lowercase,
  appears with no cue word, and is not a known place (for example, a bare
  `"priya"` in the middle of a sentence with no "my name is" nearby) will be
  missed. Detection of informal names leans heavily on cue phrases.

* **Ambiguous single tokens.** A lone capitalised word can be a name, a place,
  a brand, or the start of a sentence. The engine demotes such tokens and flags
  them for review rather than deciding. This is intentional, but it means
  recall on bare names is lower than on names with context.

* **Non-English and code-mixed text.** Only English (`en`) is supported. Hinglish
  or transliterated text is largely unsupported; transliterated names
  (`"rahul"` written in another script) will not be found.

* **Vague locative references.** "I live near the old school" is not an address
  and is correctly not treated as one. But a genuinely identifying indirect
  reference with no house number ("the flat above the bakery") will be missed.
  The current rule only promotes an address when a house number, "at", or
  "flat/plot/house" appears.

* **Addresses without cue words.** The engine detects addresses via patterns and
  via cues like "my address is" / "I live at". A free-floating address with
  neither will be missed unless spaCy tags part of it.

* **Entity type confusion is only partly corrected.** spaCy's small model
  confuses PERSON / GPE / ORG regularly. The contextual layer fixes the common
  cases with cue words and a gazetteer, but a novel confusion with no nearby cue
  will survive.

* **Organisation detection is effectively untested.** The synthetic set contains
  only 2 organisation gold spans, so the reported `ORGANIZATION F1=1.000` is not
  statistically meaningful. It should be read as "no evidence of a problem", not
  as evidence of good performance. Adding organisation examples is a priority.

* **Dates and times are not surfaced by default.** `DATE_TIME` exists in the
  schema but is not currently produced; spaCy's `DATE` output is noisy and was
  excluded to protect precision.

## Where anonymisation is limited

* **Placeholder coreference is an approximation.** "Rahul" and "Rahul Sharma"
  share a placeholder because one token set is a subset of the other. Two
  different people who share a first name and are both mentioned only by that
  first name will collide onto one placeholder. Two different people mentioned
  as "Rahul Sharma" and "Rahul Verma" will be kept distinct (neither token set
  contains the other), which is the safer default.

* **Uncertain spans are anonymised by default.** With `anonymize_uncertain=True`
  (the default), a span the engine flagged for review but scored above the
  review threshold is still replaced. This is privacy-first but means the
  engine may over-redact. Set `CIPHERFORGE_ANONYMIZE_UNCERTAIN=false` to keep
  such spans verbatim and surface them for review instead.

* **The `hash` strategy is only stable within one request.** A per-request salt
  is used so outputs cannot be correlated across requests. The same value in two
  different requests produces two different tokens. This is a deliberate privacy
  trade-off, not a bug.

* **No re-identification defence.** Placeholders preserve entity type and count
  (`[PERSON_1]`, `[PERSON_2]`), so a reader learns how many people were
  mentioned and in what order. This is standard for this class of tool but is
  not a formal privacy guarantee.

## Structural limitations of this milestone

* **No external model routing.** The brief's later vision — routing sanitised
  prompts to approved models — is not implemented. The pipeline is modular so a
  model call can be added after the `anonymize` step, but nothing sends or
  receives model traffic today.

* **No frontend integration.** The previous prototype's hard-coded JavaScript
  has not been replaced. `frontend/` is reserved but empty.

* **No persistence layer, by design.** There is no audit log, no request store
  and no metrics sink. Everything is computed per request and discarded.

* **Confidence scores are heuristic, not calibrated.** The scores combine
  recogniser base scores with additive contextual adjustments. They rank
  detections sensibly but are not probabilities and should not be read as such.

## What to build next, in priority order

1. **Coreference resolution** for person mentions, to fix the remaining failure
   and to stop first-name collisions.
2. **A larger evaluation set** written by someone other than the implementer,
   ideally with real (consented, synthetic-substituted) phrasing, to get an
   honest accuracy number.
3. **Organisation detection** — currently the weakest category.
4. **A calibrated confidence model** — replace additive heuristics with a small
   model trained on labelled data.
5. **Frontend integration** — replace the prototype's JavaScript with calls to
   `/api/v1/process`, and render the `needs_review` state distinctly.
6. **Optional local model routing** — integrate an approved local model behind
   the existing pipeline boundary.
