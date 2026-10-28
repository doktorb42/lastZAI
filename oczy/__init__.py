"""Oczy PEva: obraz gry, odczyt UI, OCR oraz pakiety wiadomości."""
"""Oczy PEva: lekki odczyt ekranu, lokalny OCR i bezpieczne paczki tekstu."""

from .identity import ConversationHeader, parse_private_header

__all__ = ["ConversationHeader", "parse_private_header"]
