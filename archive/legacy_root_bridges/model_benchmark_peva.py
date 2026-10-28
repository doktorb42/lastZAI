"""Mostek zgodności do inferencyjnego benchmarku w folderze Treningu."""

from trening.model_benchmark import *  # noqa: F401,F403


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
