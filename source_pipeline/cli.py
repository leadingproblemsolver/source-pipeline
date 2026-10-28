from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import Policy
from .pipeline import ValidationError, apply_policy, dedupe, load_records


def _write_jsonl(path: Path, records) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Normalise, dedupe and policy-filter saved source records."
    )
    ap.add_argument("--input", required=True, help="JSONL input file")
    ap.add_argument("--policy", required=True, help="Policy JSON file")
    ap.add_argument("--out-dir", required=True, help="Directory for eligible.jsonl / excluded.jsonl")
    args = ap.parse_args(argv)

    try:
        raw = load_records(args.input)
    except ValidationError as exc:
        print(f"VALIDATION ERROR: {exc}", file=sys.stderr)
        return 2

    policy = Policy.model_validate_json(Path(args.policy).read_text(encoding="utf-8"))

    merged = dedupe(raw)
    final = [apply_policy(r, policy) for r in merged]
    eligible = [r for r in final if r.eligibility == "eligible"]
    excluded = [r for r in final if r.eligibility == "excluded"]

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "eligible.jsonl", eligible)
    _write_jsonl(out / "excluded.jsonl", excluded)

    print(f"raw={len(raw)} merged={len(merged)} eligible={len(eligible)} excluded={len(excluded)}")
    for r in excluded:
        flag = " (needs independent verification)" if r.requires_independent_verification else ""
        print(f"  excluded {r.source_id} [{r.canonical_url}]: {r.reason_codes}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
