from __future__ import annotations

import unittest

from trening.train_agency_pilot_base import _chat_template_ids, _validate_chat_template_rows


class _BatchEncoding(dict[str, object]):
    """Minimal stand-in for the mapping returned by current Transformers."""


class _Tokenizer:
    def __init__(self, *, full: object, prompt: object) -> None:
        self.full = full
        self.prompt = prompt

    def apply_chat_template(self, messages: list[object], **_kwargs: object) -> object:
        return self.full if len(messages) == 3 else self.prompt


class AgencyPilotChatTemplateTests(unittest.TestCase):
    def test_batch_encoding_uses_input_ids_not_mapping_length(self) -> None:
        tokenizer = _Tokenizer(
            full=_BatchEncoding(input_ids=[10, 11, 12, 13], attention_mask=[1, 1, 1, 1]),
            prompt=_BatchEncoding(input_ids=[10, 11, 12], attention_mask=[1, 1, 1]),
        )
        messages = [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]

        self.assertEqual(_chat_template_ids(tokenizer, messages, add_generation_prompt=False), [10, 11, 12, 13])
        report = _validate_chat_template_rows(tokenizer, [{"id": "sample", "messages": messages}])

        self.assertEqual(report, {"records": 1, "max_tokens": 4})

    def test_single_item_batched_input_ids_are_flattened(self) -> None:
        tokenizer = _Tokenizer(full=_BatchEncoding(input_ids=[[7, 8, 9]]), prompt=_BatchEncoding(input_ids=[[7, 8]]))
        messages = [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]

        self.assertEqual(_chat_template_ids(tokenizer, messages, add_generation_prompt=False), [7, 8, 9])

    def test_validation_still_rejects_a_real_oversize_sample(self) -> None:
        tokenizer = _Tokenizer(full=_BatchEncoding(input_ids=list(range(1025))), prompt=_BatchEncoding(input_ids=[1]))
        messages = [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]

        with self.assertRaisesRegex(RuntimeError, r"sample: full=1025, prompt=1, limit=1024"):
            _validate_chat_template_rows(tokenizer, [{"id": "sample", "messages": messages}])


if __name__ == "__main__":
    unittest.main()
