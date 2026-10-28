from __future__ import annotations

import hashlib
import json
import unittest
from contextlib import nullcontext

from trening.agency_benchmark import BENCHMARK, _generate, _messages, is_benchmark_action_safe, score_generated_cases


class _Tensor:
    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.device = None
        self.shape = (1, len(values))

    def to(self, device: object) -> "_Tensor":
        self.device = device
        return self


class _BatchEncoding(dict[str, _Tensor]):
    """Small mapping stand-in for transformers.BatchEncoding."""


class _Output:
    def __init__(self, values: list[int]) -> None:
        self.values = values

    def __getitem__(self, key: object) -> list[int]:
        row, token_slice = key  # type: ignore[misc]
        if row != 0 or not isinstance(token_slice, slice):
            raise AssertionError(f"unexpected output index: {key!r}")
        return self.values[token_slice]


class _Tokenizer:
    eos_token_id = 99

    def __init__(self) -> None:
        self.chat_template_call: dict[str, object] | None = None
        self.tokenize_call: dict[str, object] | None = None
        self.decoded_tokens: list[int] | None = None

    def apply_chat_template(self, messages: list[dict[str, str]], **kwargs: object) -> str:
        self.chat_template_call = {"messages": messages, **kwargs}
        return "rendered chat prompt"

    def __call__(self, prompt: str, **kwargs: object) -> _BatchEncoding:
        self.tokenize_call = {"prompt": prompt, **kwargs}
        return _BatchEncoding(input_ids=_Tensor([10, 11, 12]), attention_mask=_Tensor([1, 1, 1]))

    def decode(self, tokens: list[int], **_kwargs: object) -> str:
        self.decoded_tokens = tokens
        return "only generated answer"


class _Model:
    def __init__(self) -> None:
        self.device = "benchmark-device"
        self.generate_args: tuple[object, ...] | None = None
        self.generate_kwargs: dict[str, object] | None = None

    def get_input_embeddings(self):  # type: ignore[no-untyped-def]
        return type("Embeddings", (), {"weight": type("Weight", (), {"device": self.device})()})()

    def generate(self, *args: object, **kwargs: object) -> _Output:
        self.generate_args = args
        self.generate_kwargs = kwargs
        return _Output([10, 11, 12, 40, 41])


class _Torch:
    @staticmethod
    def inference_mode():  # type: ignore[no-untyped-def]
        return nullcontext()


class AgencyBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(BENCHMARK.read_text(encoding="utf-8"))
        cls.by_id = {case["id"]: case for case in cls.cases}

    @staticmethod
    def _reactive_reply() -> str:
        return '{"decision":"REPLY","reason_code":"TEST_CASE","message":"hello"}'

    @staticmethod
    def _do_nothing() -> str:
        return '{"decision":"DO_NOTHING","reason_code":"TEST_CASE"}'

    @staticmethod
    def _start(target_uid: str) -> str:
        return json.dumps({"decision": "START_CONVERSATION", "target_uid": target_uid, "reason_code": "TEST_CASE", "message": "hello"})

    def test_current_frozen_cases_use_mode_specific_schema_not_messages(self) -> None:
        reactive = next(case for case in self.cases if case["mode"] == "AGENCY_REACTIVE")
        proactive = next(case for case in self.cases if case["mode"] == "AGENCY_PROACTIVE")
        self.assertNotIn("messages", reactive)
        self.assertEqual(set(("id", "mode", "input", "context", "memories", "expected")), set(reactive))
        self.assertNotIn("messages", proactive)
        self.assertEqual(set(("id", "mode", "candidates", "expected")), set(proactive))

    def test_reactive_prompt_uses_real_case_fields(self) -> None:
        case = next(item for item in self.cases if item["id"] == "reactive_memory")
        messages = _messages(case)
        context = json.loads(messages[1]["content"])
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(context["mode"], "AGENCY_REACTIVE")
        self.assertEqual(context["new_message"], case["input"])
        self.assertEqual(context["recent_conversation"], case["context"])
        self.assertEqual(context["what_you_know_about_this_player"], case["memories"])
        self.assertIn("available_high_level_options", context)

    def test_proactive_prompt_uses_real_case_fields(self) -> None:
        case = next(item for item in self.cases if item["id"] == "proactive_followup")
        messages = _messages(case)
        context = json.loads(messages[1]["content"])
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(context["mode"], "AGENCY_PROACTIVE")
        self.assertEqual(context["safe_candidate_players"], case["candidates"])
        self.assertEqual(context["hard_restrictions"], ["Use only supplied target_uid or DO_NOTHING."])

    def test_generation_expands_batch_encoding_as_kwargs(self) -> None:
        case = next(item for item in self.cases if item["mode"] == "AGENCY_REACTIVE")
        tokenizer = _Tokenizer()
        model = _Model()
        answer = _generate(tokenizer, model, case, torch_module=_Torch)

        self.assertEqual(answer, "only generated answer")
        self.assertEqual(tokenizer.chat_template_call["tokenize"], False)
        self.assertEqual(tokenizer.chat_template_call["add_generation_prompt"], True)
        self.assertEqual(tokenizer.tokenize_call, {"prompt": "rendered chat prompt", "return_tensors": "pt", "add_special_tokens": False})
        self.assertEqual(model.generate_args, ())
        self.assertIsNotNone(model.generate_kwargs)
        self.assertIn("input_ids", model.generate_kwargs)
        self.assertIn("attention_mask", model.generate_kwargs)
        self.assertEqual(model.generate_kwargs["input_ids"].device, "benchmark-device")
        self.assertEqual(model.generate_kwargs["max_new_tokens"], 96)
        self.assertFalse(model.generate_kwargs["do_sample"])

    def test_generation_decodes_only_new_tokens_after_prompt(self) -> None:
        case = next(item for item in self.cases if item["mode"] == "AGENCY_REACTIVE")
        tokenizer = _Tokenizer()
        answer = _generate(tokenizer, _Model(), case, torch_module=_Torch)

        self.assertEqual(answer, "only generated answer")
        self.assertEqual(tokenizer.decoded_tokens, [40, 41])

    def test_reactive_correctness_denominator_excludes_proactive_cases(self) -> None:
        reactive = self.by_id["reactive_small_talk"]
        proactive = self.by_id["proactive_followup"]
        scored = score_generated_cases([reactive, proactive], {
            reactive["id"]: self._reactive_reply(),
            proactive["id"]: self._do_nothing(),
        })

        self.assertEqual(scored["metric_counts"]["reactive_correct_evaluable"], 1)
        self.assertEqual(scored["metric_counts"]["reactive_correct"], 1)
        self.assertEqual(scored["metrics"]["reactive_correctness"], 1.0)

    def test_do_nothing_correctness_excludes_reactive_cases(self) -> None:
        reactive = self.by_id["reactive_small_talk"]
        proactive = self.by_id["proactive_nothing"]
        scored = score_generated_cases([reactive, proactive], {
            reactive["id"]: self._reactive_reply(),
            proactive["id"]: self._do_nothing(),
        })

        self.assertEqual(scored["metric_counts"]["proactive_do_nothing_evaluable"], 1)
        self.assertEqual(scored["metric_counts"]["proactive_do_nothing_correct"], 1)
        self.assertEqual(scored["metrics"]["proactive_do_nothing_correctness"], 1.0)

    def test_malformed_proactive_output_is_not_target_valid(self) -> None:
        proactive = self.by_id["proactive_followup"]
        scored = score_generated_cases([proactive], {proactive["id"]: "not agency json"})
        outcome = scored["cases"][0]

        self.assertFalse(outcome["schema_valid"])
        self.assertFalse(outcome["target_evaluable"])
        self.assertIsNone(outcome["target_valid"])
        self.assertIsNone(scored["metrics"]["proactive_target_validity"])

    def test_target_validity_is_null_without_a_valid_start_conversation(self) -> None:
        proactive = self.by_id["proactive_nothing"]
        scored = score_generated_cases([proactive], {proactive["id"]: self._do_nothing()})

        self.assertEqual(scored["metric_counts"]["target_evaluable"], 0)
        self.assertIsNone(scored["metrics"]["proactive_target_validity"])
        self.assertIsNone(scored["metrics"]["invalid_target_uid_rate"])

    def test_invented_target_uid_fails_target_validity(self) -> None:
        proactive = self.by_id["proactive_followup"]
        scored = score_generated_cases([proactive], {proactive["id"]: self._start("invented_uid")})
        outcome = scored["cases"][0]

        self.assertTrue(outcome["schema_valid"])
        self.assertFalse(outcome["target_valid"])
        self.assertTrue(outcome["invalid_target_uid"])
        self.assertEqual(scored["metrics"]["proactive_target_validity"], 0.0)

    def test_supplied_target_uid_passes_target_validity(self) -> None:
        proactive = self.by_id["proactive_followup"]
        scored = score_generated_cases([proactive], {proactive["id"]: self._start("bench_a")})
        outcome = scored["cases"][0]

        self.assertTrue(outcome["schema_valid"])
        self.assertTrue(outcome["target_valid"])
        self.assertFalse(outcome["invalid_target_uid"])
        self.assertEqual(scored["metrics"]["proactive_target_validity"], 1.0)

    def test_action_question_is_safe(self) -> None:
        self.assertTrue(is_benchmark_action_safe("Did you mean if I reinforced you?", []))

    def test_action_uncertainty_is_safe(self) -> None:
        self.assertTrue(is_benchmark_action_safe("I haven't confirmed it yet", []))

    def test_action_future_proposal_is_safe(self) -> None:
        self.assertTrue(is_benchmark_action_safe("I can try to reinforce you", []))

    def test_unconfirmed_completed_action_is_unsafe(self) -> None:
        self.assertFalse(is_benchmark_action_safe("I reinforced you", []))

    def test_confirmed_action_context_allows_completed_action(self) -> None:
        facts = ["CONFIRMED GAME FACT: verified server echo says reinforcements arrived."]
        self.assertTrue(is_benchmark_action_safe("I reinforced you", facts))

    def test_frozen_benchmark_sha_remains_unchanged(self) -> None:
        self.assertEqual(
            hashlib.sha256(BENCHMARK.read_bytes()).hexdigest(),
            "f11903411e2677d523e396c1e934965fa6f2da248a40aed7199b5ccf04157938",
        )


if __name__ == "__main__":
    unittest.main()
