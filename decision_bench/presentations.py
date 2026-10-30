"""Matched presentation views with identical underlying executable cases."""
import hashlib
import json

from .expanded import canonical, validate as validate_base


def validate(data):
    base = validate_base(data["base_fixture"])
    if hashlib.sha256(canonical(base).encode()).hexdigest() != data["base_fixture_hash"]:
        raise ValueError("Presentation base fixture hash mismatch")
    functions = {c["input"]["function"] for c in base["cases"]}
    if set(data["focused_sources"]) != functions or set(data["rules"]) != functions:
        raise ValueError("Presentation references must cover every function")
    for entries in (data["focused_sources"], data["rules"]):
        for reference in entries.values():
            if not reference["text"].strip() or hashlib.sha256(reference["text"].encode()).hexdigest() != reference["sha256"]:
                raise ValueError("Presentation reference hash mismatch")
    by_id = {c["id"]: c for c in base["cases"]}
    seen_ids, seen_views = set(), set()
    for case in data["cases"]:
        if case["id"] in seen_ids:
            raise ValueError("Duplicate presentation case ID")
        seen_ids.add(case["id"])
        original = by_id.get(case["base_case_id"])
        if original is None or case["presentation"] not in ("full", "focused", "rules"):
            raise ValueError("Unknown base case or presentation")
        key = (case["base_case_id"], case["presentation"])
        if key in seen_views:
            raise ValueError("Duplicate matched presentation")
        seen_views.add(key)
        copy = {k: v for k, v in case.items() if k not in ("base_case_id", "presentation")}
        copy["id"] = original["id"]
        if canonical(copy) != canonical(original):
            raise ValueError("Presentation changed the question, input, choices, answer, or split")
    if len(seen_views) != len(by_id) * 3:
        raise ValueError("Incomplete matched presentation coverage")
    return data


def request(data, case):
    function = case["input"]["function"]
    view = case["presentation"]
    if view == "full":
        text = data["base_fixture"]["references"][case["input"]["module"]]["text"]
    elif view == "focused":
        text = data["focused_sources"][function]["text"]
    else:
        text = data["rules"][function]["text"]
    state = {"query": case["query"], "passages": [{"id": "reference", "text": text}],
             "input": {"function": function, "args": case["input"]["args"]}}
    instructions = ("Apply the supplied reference to predict the exact runtime behavior of this local function call. "
                    "Only the reference and the shown arguments are in scope; do not add broader project or external-service validation. "
                    "An exception is a possible function outcome. " + case["query"])
    if case["task"] == "function-outcome":
        question = {"type": "choice", "instructions": instructions,
                    "criteria": {key: json.dumps(value, sort_keys=True, ensure_ascii=False)
                                 for key, value in case["options"].items()}}
    else:
        question = {"type": "noul", "instructions": instructions}
    return {"state": state, "questions": {"decision": question}}
