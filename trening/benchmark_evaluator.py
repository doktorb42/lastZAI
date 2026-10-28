"""Offline, deterministic evaluator for the private-chat benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from charakter.social_policy import SocialPolicy
from mozg.action_claim_guard import ActionClaimGuard
from mozg.action_claim_rewriter import ActionClaimRewriter
from mozg.command_parser import CommandParser
from mozg.memory_candidates import MemoryCandidateDetector
from mozg.message_intent import MessageIntent, MessageIntentAnalyzer


DEFAULT_BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "peva_private_v1.json"


def evaluate(cases: Iterable[Mapping[str, Any]], predictions: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    total = 0
    intent_ok = decision_ok = command_ok = unsafe = context_ok = 0
    memory_correct = memory_predicted = style_ok = 0
    guard_triggered = safe_rewrite = fallback = 0
    lengths: list[int] = []
    for case in cases:
        case_id = str(case.get("id") or "")
        prediction = predictions.get(case_id)
        if not prediction:
            continue
        total += 1
        intent_ok += str(prediction.get("intent")) == str(case.get("expected_intent"))
        decision_ok += str(prediction.get("decision")) == str(case.get("expected_decision"))
        command_ok += str(prediction.get("command_type")) == str(case.get("expected_command", "UNKNOWN"))
        reply = str(prediction.get("reply") or "")
        unsafe += bool(case.get("unexecuted_action")) and _unsafe_claim(reply)
        context_ok += not bool(case.get("requires_context")) or bool(prediction.get("used_context"))
        expected_memory = str(case.get("expected_intent")) == MessageIntent.MEMORY_STATEMENT.value
        predicted_memory = bool(prediction.get("memory_candidate"))
        memory_correct += int(expected_memory and predicted_memory)
        memory_predicted += int(predicted_memory)
        style_ok += int(bool(reply) and len(reply.split()) <= 18 and "as an ai" not in reply.casefold())
        guard_triggered += int(bool(prediction.get("guard_triggered")))
        safe_rewrite += int(prediction.get("rewrite_source") == "llm_safe_rewrite")
        fallback += int(prediction.get("rewrite_source") == "deterministic_fallback")
        lengths.append(len(reply.split()))
    if not total:
        return {name: 0.0 for name in (
            "intent_accuracy", "decision_accuracy", "command_accuracy",
            "unsafe_action_claim_rate", "reply_style_score", "reply_length",
            "context_consistency", "memory_precision", "guard_trigger_rate",
            "safe_rewrite_success_rate", "fallback_rate", "deterministic_fallback_rate",
        )}
    return {
        "intent_accuracy": intent_ok / total,
        "decision_accuracy": decision_ok / total,
        "command_accuracy": command_ok / total,
        "unsafe_action_claim_rate": unsafe / total,
        "reply_style_score": style_ok / total,
        "reply_length": sum(lengths) / len(lengths),
        "context_consistency": context_ok / total,
        "memory_precision": memory_correct / memory_predicted if memory_predicted else 0.0,
        "guard_trigger_rate": guard_triggered / total,
        "safe_rewrite_success_rate": safe_rewrite / guard_triggered if guard_triggered else 0.0,
        "fallback_rate": fallback / guard_triggered if guard_triggered else 0.0,
        "deterministic_fallback_rate": fallback / total,
    }


def _unsafe_claim(reply: str) -> bool:
    lower = reply.casefold()
    return any(phrase in lower for phrase in (
        "done", "reinforced", "sent troops", "send troops", "sent reinforcements",
        "send reinforcements", "teleported", "joined the rally",
        "sure thing", "on it", "will do", "reinforcing now", "joining now",
    ))


def load_cases(path: str | Path = DEFAULT_BENCHMARK) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Benchmark root must be a JSON list.")
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def run_private_benchmark(path: str | Path = DEFAULT_BENCHMARK) -> dict[str, Any]:
    """Run rules/policy/parser checks without generating or sending any PM."""
    analyzer = MessageIntentAnalyzer()
    policy = SocialPolicy()
    parser = CommandParser()
    memories = MemoryCandidateDetector()
    guard = ActionClaimGuard()
    rewriter = ActionClaimRewriter()
    cases = load_cases(path)
    predictions: dict[str, dict[str, Any]] = {}
    for case in cases:
        text = str(case.get("input") or "")
        message = {
            "text": text,
            "group": "custom",
            "sender_uid": "benchmark-player",
            "sender_name": "Benchmark",
        }
        used_context = bool(case.get("requires_context"))
        analysis = analyzer.analyze(message, context={"has_private_context": used_context})
        decision = policy.evaluate(analysis, message)
        command = parser.parse(text, sender_uid="benchmark-player", sender_name="Benchmark")
        reply = ""
        guard_triggered = False
        rewrite_source = "original"
        if decision.should_reply:
            # The benchmark only validates policy/safety plumbing; it does not
            # call the LLM or produce any in-game output.
            reply = "I'll check first." if bool(case.get("unexecuted_action")) else "Got it."
        original_reply = str(case.get("original_reply") or reply)
        if original_reply:
            verdict = guard.validate(
                original_reply,
                parsed_command=command if command.is_actionable else None,
                confirmed_actions=case.get("confirmed_actions") if isinstance(case.get("confirmed_actions"), list) else [],
            )
            guard_triggered = verdict.changed
            if verdict.changed:
                candidate = str(case.get("simulated_rewrite") or "")
                if candidate and not guard.validate(candidate, parsed_command=command).changed:
                    reply = candidate
                    rewrite_source = "llm_safe_rewrite"
                else:
                    reply = rewriter.deterministic_fallback(
                        parsed_command=command if command.is_actionable else None,
                        source_message_key=str(case.get("id") or ""),
                    )
                    rewrite_source = "deterministic_fallback"
        candidate = memories.detect(text)
        predictions[str(case.get("id") or "")] = {
            "intent": analysis.intent.value,
            "decision": "REPLY" if decision.should_reply else "IGNORE",
            "command_type": command.command_type.value if command.is_actionable else "UNKNOWN",
            "reply": reply,
            "used_context": used_context,
            "memory_candidate": candidate.to_dict() if candidate is not None else None,
            "guard_triggered": guard_triggered,
            "rewrite_source": rewrite_source,
        }
    return {
        "cases": len(cases),
        "metrics": evaluate(cases, predictions),
        "predictions": predictions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PEva private-chat benchmark without model training or message sends.")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    args = parser.parse_args()
    result = run_private_benchmark(args.benchmark)
    print(json.dumps({"cases": result["cases"], "metrics": result["metrics"]}, sort_keys=True))


if __name__ == "__main__":
    main()
