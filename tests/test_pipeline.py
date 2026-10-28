from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from source_pipeline.models import Policy
from source_pipeline.pipeline import (
    ValidationError,
    apply_policy,
    canonicalize,
    dedupe,
    load_records,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
POLICY = Policy.model_validate_json((ROOT / "policy.json").read_text())


def evaluate_fixture(path: Path):
    raw = load_records(str(path))
    merged = dedupe(raw)
    return [apply_policy(r, POLICY) for r in merged]


def find_by_url(records, url):
    return next(r for r in records if r.canonical_url == url)


# ------------------------------------------------------------------ canonicalization

def test_canonicalize_normalises_host_trailing_slash_and_tracking():
    assert (
        canonicalize("https://Example.com/a/?utm_source=x&b=2&a=1#frag")
        == "https://example.com/a?a=1&b=2"
    )


# ------------------------------------------------------------------ duplicate handling

def test_exact_and_canonical_duplicates_preserve_provenance():
    out = evaluate_fixture(FIXTURES / "sources.jsonl")
    reuters = find_by_url(out, "https://www.reuters.com/world/example")

    assert sorted(reuters.merged_source_ids) == ["s1", "s1b", "s2"]
    assert "manual" in reuters.discovery_lineage
    assert "newsletter" in reuters.discovery_lineage


def test_merged_source_ids_length_distinguishes_duplicates():
    out = evaluate_fixture(FIXTURES / "sources.jsonl")
    reuters = find_by_url(out, "https://www.reuters.com/world/example")
    # Dedupe-ness is derivable rather than a stored boolean.
    assert len(reuters.merged_source_ids) > 1


# ------------------------------------------------------------------ unknown dates

def test_unknown_date_remains_unknown():
    out = evaluate_fixture(FIXTURES / "sources.jsonl")
    gov = next(r for r in out if r.source_id == "s3")
    assert gov.publication_date is None
    assert gov.event_date is None


# ------------------------------------------------------------------ competitor: product claim

def test_competitor_product_claim_is_excluded_with_reason():
    out = evaluate_fixture(FIXTURES / "sources.jsonl")
    acme = next(r for r in out if r.source_id == "s4")
    assert acme.eligibility == "excluded"
    assert "competitor_product_claim" in acme.reason_codes
    assert acme.requires_independent_verification is False


# ------------------------------------------------------------------ competitor: generic claim

def test_competitor_generic_claim_is_discovery_lead_not_evidence():
    out = evaluate_fixture(FIXTURES / "sources.jsonl")
    acme = next(r for r in out if r.source_id == "s5")
    assert acme.eligibility == "excluded"
    assert "competitor_generic_claim" in acme.reason_codes
    assert acme.requires_independent_verification is True


# ------------------------------------------------------------------ valid primary source

def test_valid_primary_source_is_accepted():
    out = evaluate_fixture(FIXTURES / "sources.jsonl")
    reuters = find_by_url(out, "https://www.reuters.com/world/example")
    assert reuters.eligibility == "eligible"
    assert reuters.reason_codes == []


# ------------------------------------------------------------------ malformed input

def test_malformed_input_raises_validation_error():
    with pytest.raises(ValidationError):
        load_records(str(FIXTURES / "malformed.jsonl"))


# ------------------------------------------------------------------ policy contract

def test_policy_rejects_old_flat_shape():
    # If someone reverts policy.json to the old flat layout this must fail
    # loudly rather than silently defaulting everything to "neutral".
    with pytest.raises(PydanticValidationError):
        Policy.model_validate({
            "authoritative_source_types": ["primary"],
            "ineligible_source_types": [],
            "competitor_source_type": "competitor",
            "competitor_promo_keywords": [],
            "competitor_generic_keywords": [],
        })


# ------------------------------------------------------------------ both buckets populated

def test_output_preserves_both_eligible_and_excluded():
    out = evaluate_fixture(FIXTURES / "sources.jsonl")
    assert any(r.eligibility == "eligible" for r in out)
    assert any(r.eligibility == "excluded" for r in out)
    # Every excluded record has a reason.
    for r in out:
        if r.eligibility == "excluded":
            assert r.reason_codes
