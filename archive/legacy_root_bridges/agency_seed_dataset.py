"""Mostek zgodności do syntetycznego zestawu bazowego Agency w Treningu."""

from trening.agency_seed_dataset import MANIFEST, OUTPUT, examples, main, write_dataset

__all__ = ["OUTPUT", "MANIFEST", "examples", "write_dataset", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
