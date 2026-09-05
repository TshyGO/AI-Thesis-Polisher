"""Offline, exact-reference evaluation. No network or model-as-judge calls.

python -m benchmarks.evaluate --samples benchmarks/synthetic.jsonl --predictions predictions.jsonl
Prediction rows: {"id": "zh01", "revised": "...", "human_accepted": true}
"""
import argparse
import json
from pathlib import Path
from engine.validation import Validator


def read_rows(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("duplicate sample ids")
    return rows


def evaluate(samples, predictions):
    indexed = {p["id"]: p for p in predictions}
    if len(indexed) != len(predictions) or set(indexed) != {s["id"] for s in samples}:
        raise ValueError("predictions must cover exactly the sample ids")
    proposed = needed = matched = unnecessary = damaged = reviewed = accepted = failures = 0
    details = []
    for sample in samples:
        if (type(sample.get("should_edit")) is not bool
                or not isinstance(sample.get("original"), str)
                or not isinstance(sample.get("accepted"), list)
                or not all(isinstance(v, str) for v in sample["accepted"])):
            raise ValueError("invalid sample schema")
        prediction = indexed[sample["id"]]
        needed += sample["should_edit"]
        if prediction.get("status", "OK") != "OK":
            failures += 1
            details.append({"id": sample["id"], "status": prediction["status"]})
            continue
        revised = prediction.get("revised")
        if not isinstance(revised, str):
            raise ValueError("prediction revised must be a string")
        edited = revised != sample["original"]
        match = edited and sample["should_edit"] and revised in sample["accepted"]
        reason = Validator(sample.get("protected_terms", [])).validate(sample["original"], revised)
        proposed += edited
        matched += match
        unnecessary += edited and not sample["should_edit"]
        damaged += edited and bool(reason)
        if edited and "human_accepted" in prediction:
            if type(prediction["human_accepted"]) is not bool:
                raise ValueError("human_accepted must be boolean")
            reviewed += 1
            accepted += prediction["human_accepted"]
        details.append({"id": sample["id"], "edited": edited,
                        "reference_match": bool(match), "guard_rejection": reason})
    ratio = lambda n, d: n / d if d else None
    return {"samples": len(samples), "proposed": proposed, "failures": failures,
            "completion_rate": ratio(len(samples) - failures, len(samples)),
            "reference_precision": ratio(matched, proposed),
            "reference_recall": ratio(matched, needed),
            "unnecessary_edit_rate": ratio(unnecessary, proposed),
            "lexical_fact_damage_rate": ratio(damaged, proposed),
            "human_accept_rate": ratio(accepted, reviewed),
            "human_reviewed": reviewed, "details": details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--predictions", required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(read_rows(args.samples), read_rows(args.predictions)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
