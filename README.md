# source-pipeline

Small local pipeline that ingests saved source records, normalises them,
deduplicates by canonical URL while preserving provenance, and applies a
configurable source + competitor policy. Both eligible and excluded records
are exported with explicit reasons.

## Install

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt

## Run the tests

    pytest -q

## Run the demo

    python -m source_pipeline.cli \
      --input fixtures/sources.jsonl \
      --policy policy.json \
      --out-dir out/

Inspect:
- `out/eligible.jsonl` — authoritative records (incl. the merged Reuters record)
- `out/excluded.jsonl` — competitor promo / generic / unclassified claims with reason codes

Demonstrate validation failure:

    python -m source_pipeline.cli \
      --input fixtures/malformed.jsonl \
      --policy policy.json \
      --out-dir out/
    # -> exits 2 with "VALIDATION ERROR: line N: ..."

## Architecture / design notes

**Why this normalisation approach.** URLs are canonicalised as
"scheme lower, host lower, default-port stripped, path trailing-slash stripped
(except root), tracking params (`utm_*`, `gclid`, `fbclid`, `ref`, `src`, …)
removed, remaining query sorted". This gives a stable canonical key for
dedupe without discarding meaningful query parameters. `canonicalize()` is a
pure function, so it is trivially testable and safe to swap.

**Dedupe & provenance.** Records are grouped by canonical URL. For each group
we pick the lexicographically-smallest `source_id` as the representative,
union all `discovery_lineage` entries (order-preserving, de-duplicated), and
record every contributing `source_id` in `merged_source_ids`. Duplicate status
is derivable from `len(merged_source_ids) > 1`; we do not store a separate
boolean.

**Policies are configurable.** All rules live in `policy.json` and are validated
by `Policy` (Pydantic). The evaluator only reads from that object — no
hard-coded publisher names or keywords inside `pipeline.py`. Source types map
to one of four roles (`authoritative` / `ineligible` / `competitor` /
`neutral`); unknown source types default to `neutral` so an unconfigured type
becomes a review flag rather than silent trust.

**Dates.** If a date is missing in every record of a group it stays `None`.
If some record in the same canonical group knows a date, we take it — that is
the same page, so it is evidence, not inference. Date disagreements are not
adjudicated here (see the comment in `_merge_group`).

**Competitor handling.**
- Promo keywords → `excluded`, `competitor_product_claim`
- Generic industry keywords → `excluded`, `competitor_generic_claim`,
  `requires_independent_verification=True` (a discovery lead, not evidence)
- Any other competitor content → `excluded`, `competitor_unclassified_claim`,
  `requires_independent_verification=True`

**Content is untrusted.** `content` is only ever string-matched against
keywords; it is never executed or fed to a model. There is no model in this
submission.

## Assumptions and known limitations

- `source_type` is supplied by the input rather than inferred — this keeps
  policy evaluation deterministic and reviewable.
- A group with mixed `source_type` uses the representative's type (sorted by
  `source_id`). Mixed-type groups are not flagged; a production system would
  add a `mixed_source_types` reason code.
- `canonicalize()` does not follow redirects — saved snapshots are treated as
  ground truth. No live connector is implemented.
- Keyword lists in `policy.json` are illustrative; adjust to your domain.
- Provenance is captured by `discovery_lineage` and `merged_source_ids` only.
  Per-discovery timestamps are not tracked.
