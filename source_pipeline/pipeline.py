from __future__ import annotations

import json
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import OutputRecord, Policy, RawRecord


class ValidationError(Exception):
    """Raised when the input file cannot be parsed or fails schema validation."""


# ---------------------------------------------------------------- normalisation

# Tracking params we strip before comparing URLs. Exact keys and prefixes are
# kept as two named constants because they are two different mechanisms.
_TRACKING_QUERY_KEYS = {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "src"}
_TRACKING_QUERY_PREFIXES = ("utm_",)


def canonicalize(url: str) -> str:
    """Best-effort canonical form: lower host, strip fragment, drop tracking
    params, sort query, strip trailing slash (except root)."""
    s = urlsplit(url)
    scheme = s.scheme.lower()
    host = (s.hostname or "").lower()

    port = s.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"

    path = s.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    def _is_tracking(key: str) -> bool:
        k = key.lower()
        return k in _TRACKING_QUERY_KEYS or k.startswith(_TRACKING_QUERY_PREFIXES)

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(s.query, keep_blank_values=True)
        if not _is_tracking(k)
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)

    return urlunsplit((scheme, netloc, path, query, ""))


# ---------------------------------------------------------------- loading

def load_records(path: str) -> list[RawRecord]:
    """Read a JSONL file, validating each line against RawRecord.
    Fails fast with line-numbered ValidationError on any bad line."""
    records: list[RawRecord] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"line {lineno}: invalid JSON: {exc}") from exc
            try:
                records.append(RawRecord.model_validate(data))
            except Exception as exc:
                raise ValidationError(f"line {lineno}: {exc}") from exc
    return records


# ---------------------------------------------------------------- dedupe

def _ordered_unique(items):
    """Preserve first-seen order while dropping repeats."""
    seen: dict = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def dedupe(records: list[RawRecord]) -> list[OutputRecord]:
    groups: dict[str, list[RawRecord]] = defaultdict(list)
    for r in records:
        groups[canonicalize(r.url)].append(r)

    return [_merge_group(url, groups[url]) for url in sorted(groups)]


def _merge_group(canonical_url: str, group: list[RawRecord]) -> OutputRecord:
    ordered = sorted(group, key=lambda r: r.source_id)
    rep = ordered[0]

    # Preserve any known date in the group; never fabricate a missing one.
    # If two records for the same URL disagree on the date, the first
    # known value wins (sorted by source_id). That's deliberate: a
    # disagreement is a data-quality problem to fix upstream, not
    # something this pipeline should adjudicate silently.
    pub = next((r.publication_date for r in ordered if r.publication_date), None)
    evt = next((r.event_date for r in ordered if r.event_date), None)

    lineage = _ordered_unique(item for r in ordered for item in r.discovery_lineage)
    contents = _ordered_unique(r.content for r in ordered if r.content)

    return OutputRecord(
        source_id=rep.source_id,
        canonical_url=canonical_url,
        publisher=rep.publisher,
        source_type=rep.source_type,
        publication_date=pub,
        event_date=evt,
        discovery_lineage=lineage,
        merged_source_ids=[r.source_id for r in ordered],
        content="\n".join(contents) if contents else None,
        eligibility="eligible",  # default; policy will set the real value
        reason_codes=[],
    )


# ---------------------------------------------------------------- policy

def _hits(text: str, keywords: list[str]) -> bool:
    return any(k.lower() in text for k in keywords)


def _classify(record: OutputRecord, policy: Policy) -> tuple[str, list[str], bool]:
    """Return (eligibility, reason_codes, requires_independent_verification)."""
    text = (record.content or "").lower()
    role = policy.role_for(record.source_type)

    if role == "ineligible":
        return "excluded", ["source_type_ineligible"], False
    if role == "competitor":
        if _hits(text, policy.competitor_promo_keywords):
            return "excluded", ["competitor_product_claim"], False
        if _hits(text, policy.competitor_generic_keywords):
            # A generic industry fact from a competitor is a discovery lead,
            # not publication evidence — the original source needs finding.
            return "excluded", ["competitor_generic_claim"], True
        return "excluded", ["competitor_unclassified_claim"], True
    if role == "authoritative":
        return "eligible", [], False
    return "excluded", ["source_type_not_authoritative"], False


def apply_policy(record: OutputRecord, policy: Policy) -> OutputRecord:
    eligibility, reasons, needs_verify = _classify(record, policy)
    return record.model_copy(update={
        "eligibility": eligibility,
        "reason_codes": reasons,
        "requires_independent_verification": needs_verify,
    })
