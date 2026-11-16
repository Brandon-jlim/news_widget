"""Offline threshold laboratory. Never imported by the relay or browser.

Policy experiments consume explicitly annotated evidence, NOT invented model
confidence scores. Their results measure the decision policy independently of
text extraction. Draft/synthetic labels are not a market accuracy benchmark.
"""
import argparse
from collections import Counter
import csv
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path

try:
    from .market_impact import assess_impact
except ImportError:
    from market_impact import assess_impact


ROOT = Path(__file__).resolve().parents[1]
LABELS = ("positive", "negative", "mixed", "neutral", "unknown")
VISIBLE = LABELS[:-1]


@dataclass(frozen=True)
class Thresholds:
    name: str
    eps_relative: str
    eps_absolute: str  # Currency units per share, same currency as the input.
    revenue_relative: str
    inflation_pp: str  # Percentage POINTS, never relative percentage change.


POLICIES = (
    Thresholds("sensitive", "0.02", "0.01", "0.005", "0.05"),
    Thresholds("balanced", "0.05", "0.02", "0.01", "0.10"),
    Thresholds("conservative", "0.10", "0.03", "0.02", "0.20"),
)


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Measurements must be finite")
    return result


def validate_cases(cases):
    ids, groups = set(), {}
    for case in cases:
        if case["id"] in ids:
            raise ValueError(f"Duplicate case: {case['id']}")
        ids.add(case["id"])
        if case["expected"] not in LABELS:
            raise ValueError(f"Invalid expected label: {case['id']}")
        if case["label_status"] not in {"draft", "reviewed"}:
            raise ValueError("label_status must be draft or reviewed")
        if case["split"] not in {"development", "validation", "test"}:
            raise ValueError("Invalid split")
        group = case["group_id"]
        if not isinstance(group, str) or not group.strip():
            raise ValueError("Every case needs an event group")
        if group in groups and groups[group] != case["split"]:
            raise ValueError(f"Same event group appears in different splits: {group}")
        groups[group] = case["split"]
        annotation = case["annotation"]
        if type(annotation["eligible"]) is not bool or type(annotation["neutral_supported"]) is not bool:
            raise ValueError("Eligibility and neutral support must be explicit booleans")
        text = " ".join(str(case["item"].get(k, "")) for k in ("originalTitle", "title", "snippet"))
        for evidence in annotation["evidence"]:
            if not evidence.get("quote") or evidence["quote"] not in text:
                raise ValueError(f"Evidence quote absent from input: {case['id']}")
            if evidence["kind"] == "event":
                if evidence.get("direction") not in {"positive", "negative"}:
                    raise ValueError("Event evidence must have a direction")
            elif evidence["kind"] in {"eps", "revenue", "inflation"}:
                decimal(evidence["actual"])
                decimal(evidence["expected"])
                if evidence["kind"] == "inflation" and evidence.get("unit") != "percentage_points":
                    raise ValueError("Inflation must use percentage points")
                if evidence["kind"] == "eps" and evidence.get("unit") != "USD_per_share":
                    raise ValueError("This pilot's EPS absolute floor is in USD per share")
                if evidence["kind"] == "revenue" and evidence.get("unit") != "USD":
                    raise ValueError("Normalize both revenue measurements to USD")
            else:
                raise ValueError("Unknown evidence kind")
    return cases


def decide(annotation, policy):
    """Four display labels plus internal abstention; threshold equality qualifies.

    Both sides need material evidence for mixed. No subtraction, synonym counts,
    or conversion of missing evidence to neutral. Annotation completeness is a
    separate gate, not a numerical confidence estimate.
    """
    if not annotation["eligible"]:
        return {"direction": "unknown", "reason": "insufficient_context", "measurements": []}
    directions, measurements = set(), []
    for evidence in annotation["evidence"]:
        kind = evidence["kind"]
        if kind == "event":
            directions.add(evidence["direction"])
            measurements.append({"kind": kind, "direction": evidence["direction"], "quote": evidence["quote"]})
            continue
        actual, expected = decimal(evidence["actual"]), decimal(evidence["expected"])
        change = actual - expected
        if kind == "eps":
            cutoff = max(abs(expected) * decimal(policy.eps_relative), decimal(policy.eps_absolute))
        elif kind == "revenue":
            if expected <= 0:
                return {"direction": "unknown", "reason": "revenue_baseline_not_positive", "measurements": measurements}
            cutoff = abs(expected) * decimal(policy.revenue_relative)
        else:
            cutoff = decimal(policy.inflation_pp)
        material = abs(change) >= cutoff and change != 0
        positive = change < 0 if kind == "inflation" else change > 0
        direction = ("positive" if positive else "negative") if material else "neutral"
        if material:
            directions.add(direction)
        measurements.append({"kind": kind, "change": str(change), "cutoff": str(cutoff),
                             "direction": direction, "quote": evidence["quote"]})
    if len(directions) > 1:
        direction = "mixed"
    elif directions:
        direction = next(iter(directions))
    else:
        direction = "neutral" if annotation["neutral_supported"] else "unknown"
    return {"direction": direction, "reason": "annotated_evidence_policy", "measurements": measurements}


def metrics(expected, predicted):
    if len(expected) != len(predicted):
        raise ValueError("Expected and predicted arrays differ in length")
    confusion = {label: dict.fromkeys(LABELS, 0) for label in LABELS}
    for truth, result in zip(expected, predicted):
        confusion[truth][result] += 1
    classes = {}
    for label in VISIBLE:
        tp = confusion[label][label]
        support = sum(confusion[label].values())
        shown = sum(row[label] for row in confusion.values())
        classes[label] = {"support": support, "predicted": shown, "correct": tp,
                          "precision": tp / shown if shown else None,
                          "recall": tp / support if support else None}
    total = len(expected)
    displayed = sum(result != "unknown" for result in predicted)
    return {"total": total, "displayed": displayed,
            "coverage": displayed / total if total else None,
            "display_precision": sum(t == p and p != "unknown" for t, p in zip(expected, predicted)) / displayed if displayed else None,
            "opposite_direction_errors": confusion["positive"]["negative"] + confusion["negative"]["positive"],
            "unsupported_badges": sum(confusion["unknown"][label] for label in VISIBLE),
            "classes": classes, "confusion": confusion}


def evaluate(cases, split, policy_name=None):
    validate_cases(cases)  # Check leakage before filtering.
    selected = [case for case in cases if case["split"] == split]
    if not selected:
        raise ValueError(f"No cases in split: {split}")
    if split == "test" and policy_name is None:
        raise ValueError("Test split requires one fixed policy; tune on validation data")
    policies = [policy for policy in POLICIES if policy_name is None or policy.name == policy_name]
    if not policies:
        raise ValueError(f"Unknown policy: {policy_name}")
    truth = [case["expected"] for case in selected]
    predictions = {"current_rules": [assess_impact(case["item"])["direction"] for case in selected]}
    decisions = {}
    for policy in policies:
        decisions[policy.name] = [decide(case["annotation"], policy) for case in selected]
        predictions[policy.name] = [result["direction"] for result in decisions[policy.name]]
    return {
        "schema_version": 1,
        "scope": "offline_policy_experiment_not_price_forecast",
        "split": split,
        "label_status_counts": dict(Counter(case["label_status"] for case in selected)),
        "origin_counts": dict(Counter(case["origin"] for case in selected)),
        "limitations": ["Draft labels were authored for boundary testing, not independently reviewed.",
                        "Candidate policies use annotated evidence; current_rules extracts text automatically. These are different evaluation scopes.",
                        "Candidate values are hypotheses, not calibrated probabilities or recommended production thresholds.",
                        "No threshold is selected automatically. Keep event groups and later test data separate."],
        "policies": [asdict(policy) for policy in policies],
        "results": {name: metrics(truth, values) for name, values in predictions.items()},
        "cases": [{"id": case["id"], "title": case["item"]["title"], "expected": case["expected"],
                   "rationale": case["rationale"], "label_status": case["label_status"],
                   "predictions": {name: values[i] for name, values in predictions.items()},
                   "decisions": {name: values[i] for name, values in decisions.items()}}
                  for i, case in enumerate(selected)],
    }


def collect_snapshot(paths):
    """Freeze local inputs only; no network, article body retrieval or API usage."""
    unique = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("items", []) if isinstance(payload, dict) else payload
        for item in items:
            identity = str(item.get("id") or item.get("url") or item.get("originalTitle") or item.get("title"))
            key = hashlib.sha256(identity.encode()).hexdigest()[:20]
            if key in unique:
                continue
            frozen = {k: item[k] for k in ("id", "title", "originalTitle", "snippet", "entities", "source", "publishedAt", "url") if k in item}
            unique[key] = {"review_id": key, "snapshot_source": path.name, "item": frozen,
                           "current_assessment": assess_impact(frozen),
                           "expected": None, "label_status": "unreviewed", "group_id": "", "review_reason": ""}
    return list(unique.values())


def write_review_csv(path, snapshot):
    # Hide the current prediction from annotators to reduce anchoring.
    columns = ["review_id", "published_at", "source", "title", "snippet", "symbols", "expected",
               "group_id", "scope", "horizon", "evidence_quote", "review_reason", "reviewer"]
    def safe(value):
        value = str(value or "")
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in snapshot:
            item = row["item"]
            writer.writerow({key: safe(value) for key, value in {
                "review_id": row["review_id"], "published_at": item.get("publishedAt"),
                "source": item.get("source"), "title": item.get("originalTitle") or item.get("title"),
                "snippet": item.get("snippet"),
                "symbols": ", ".join(e.get("symbol", "") for e in item.get("entities", [])),
                "horizon": "단기", "expected": "", "group_id": "", "scope": "", "evidence_quote": "",
                "review_reason": "", "reviewer": ""}.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/fixtures/impact_threshold_cases.json")
    parser.add_argument("--split", choices=("development", "validation", "test"), default="development")
    parser.add_argument("--policy", choices=tuple(policy.name for policy in POLICIES),
                        help="Required for the test split; lock this choice using validation data first")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data/impact-evaluation")
    parser.add_argument("--feeds", type=Path, nargs="*", default=[])
    args = parser.parse_args()
    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    report = evaluate(payload["cases"], args.split, args.policy)
    report["dataset_sha256"] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.feeds:
        snapshot = collect_snapshot(args.feeds)
        (args.out_dir / "review-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_review_csv(args.out_dir / "review.csv", snapshot)
        report["unreviewed_snapshot"] = {"total": len(snapshot), "current_directions": dict(Counter(row["current_assessment"]["direction"] for row in snapshot)),
                                         "note": "Distribution only. No accuracy or optimal threshold can be computed without reviewed labels."}
    (args.out_dir / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Offline experiment ({args.split}); labels: {report['label_status_counts']}")
    for name, result in report["results"].items():
        print(f"{name}: {result['displayed']}/{result['total']} displayed; "
              f"opposite errors={result['opposite_direction_errors']}; unsupported badges={result['unsupported_badges']}")
    if "unreviewed_snapshot" in report:
        print("Unreviewed local snapshot:", report["unreviewed_snapshot"])
    print("Results:", args.out_dir / "results.json")


if __name__ == "__main__":
    main()
