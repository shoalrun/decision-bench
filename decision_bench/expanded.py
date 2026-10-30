"""Validation and label-free requests for executable multi-module fixtures."""
import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def validate(data):
    if not data.get("cases") or not data.get("references"):
        raise ValueError("Expanded fixture needs cases and source references")
    for reference in data["references"].values():
        if hashlib.sha256(reference["text"].encode()).hexdigest() != reference["sha256"]:
            raise ValueError("Expanded source hash mismatch")
        if not reference.get("source"):
            raise ValueError("Expanded source provenance missing")
    seen_ids, seen_calls, groups = set(), set(), {}
    for case in data["cases"]:
        if case["id"] in seen_ids:
            raise ValueError("Duplicate expanded case ID")
        seen_ids.add(case["id"])
        if case["split"] not in ("dev", "test") or groups.setdefault(case["group"], case["split"]) != case["split"]:
            raise ValueError("Expanded family crosses splits")
        call = case["input"]
        if set(call) != {"module", "function", "args"} or not isinstance(call["args"], list):
            raise ValueError("Invalid expanded call shape")
        if call["module"] not in data["references"]:
            raise ValueError("Unknown expanded source module")
        key = canonical(call)
        if key in seen_calls:
            raise ValueError("Duplicate expanded input call")
        seen_calls.add(key)
        if canonical(call) != canonical(case["oracle"]["call"]):
            raise ValueError("Expanded visible input/oracle mismatch")
        outcome = case["oracle"]["outcome"]
        if not isinstance(outcome.get("ok"), bool):
            raise ValueError("Invalid expanded oracle outcome")
        if not case["query"].strip():
            raise ValueError("Empty expanded question")
        if case["task"] == "function-validity":
            expected = "yes" if outcome["ok"] else "no"
        elif case["task"] == "boolean-result":
            if not outcome["ok"] or not isinstance(outcome.get("result"), bool):
                raise ValueError("Expected boolean oracle result")
            expected = "yes" if outcome["result"] else "no"
        elif case["task"] == "function-outcome":
            options = case["options"]
            if len(options) != 4 or len({canonical(v) for v in options.values()}) != 4:
                raise ValueError("Expanded choices must have four distinct outcomes")
            matching = [key for key, value in options.items() if canonical(value) == canonical(outcome)]
            if len(matching) != 1:
                raise ValueError("Expanded choice needs one matching executable outcome")
            expected = matching[0]
        else:
            raise ValueError("Unknown expanded task")
        if case["expected"] != expected:
            raise ValueError("Expanded expected label disagrees with oracle")
    return data


def request(data, case):
    module = case["input"]["module"]
    state = {"query": case["query"],
             "passages": [{"id": "implementation", "text": data["references"][module]["text"]}],
             "input": {"function": case["input"]["function"], "args": case["input"]["args"]}}
    instructions = ("Predict the exact runtime behavior of the supplied JavaScript/TypeScript implementation. "
                    "The shown source is the authoritative rule. Evaluate only this local function call, "
                    "not broader project validity or external service behavior. "
                    "A thrown exception is a normal possible outcome of the function. " + case["query"])
    if case["task"] == "function-outcome":
        question = {"type": "choice", "instructions": instructions,
                    "criteria": {key: json.dumps(value, sort_keys=True, ensure_ascii=False)
                                 for key, value in case["options"].items()}}
    else:
        question = {"type": "noul", "instructions": instructions}
    return {"state": state, "questions": {"decision": question}}
