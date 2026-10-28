"""Mostek zgodności do syntetycznych korekt Agency V2 w Treningu."""

import json

from trening.agency_corrective_dataset_v2 import (
    EXPECTED_COUNTS, MANIFEST, OUTPUT, SOURCE, examples, proactive, reactive,
    rendered, validate, write,
)

__all__ = ["OUTPUT", "MANIFEST", "SOURCE", "EXPECTED_COUNTS", "reactive", "proactive", "examples", "rendered", "validate", "write"]


if __name__ == "__main__":
    print(json.dumps(write(), ensure_ascii=False, indent=2))
