"""Mostek zgodności do budownika danych treningowych PEva."""

from trening.dataset_builder import DatasetBuilder, PROCESSED_DIR, RAW_DIR, TrainingSample, main

__all__ = ["DatasetBuilder", "TrainingSample", "RAW_DIR", "PROCESSED_DIR", "main"]


if __name__ == "__main__":
    main()
