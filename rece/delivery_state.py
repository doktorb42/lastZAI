"""Stan potwierdzenia dawnego transportu wiadomości.

To są wyłącznie dane bezpieczeństwa Rąk: opisują próbę, potwierdzenie lub
niepewność dostarczenia. Nie uruchamiają ADB, nie wpisują tekstu i nie
wysyłają wiadomości.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class DeliveryState(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    ATTEMPTED = "ATTEMPTED"
    CONFIRMED = "CONFIRMED"
    AMBIGUOUS = "AMBIGUOUS"
    FAILED_SAFE = "FAILED_SAFE"
    MANUAL_ASSUMPTION = "MANUAL_ASSUMPTION"


DELIVERY_METADATA_FIELDS = (
    "navigation_verified",
    "target_uid_verified",
    "room_verified",
    "text_entered",
    "send_tap_attempted",
    "server_echo_seen",
    "server_echo_seq",
    "confirmation_source",
    "delivery_state",
    "reason",
)


def initial_delivery_metadata(*, reason: str = "not_attempted") -> dict[str, Any]:
    return {
        "navigation_verified": False,
        "target_uid_verified": False,
        "room_verified": False,
        "text_entered": False,
        "send_tap_attempted": False,
        "server_echo_seen": False,
        "server_echo_seq": None,
        "confirmation_source": None,
        "delivery_state": DeliveryState.NOT_ATTEMPTED.value,
        "reason": reason,
    }


def delivery_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalizuj niepełne dawne metadane, nie wymyślając faktów."""
    result = initial_delivery_metadata()
    if isinstance(value, Mapping):
        for field in DELIVERY_METADATA_FIELDS:
            if field in value:
                result[field] = value[field]
    state = str(result.get("delivery_state") or DeliveryState.NOT_ATTEMPTED.value)
    if state not in {item.value for item in DeliveryState}:
        state = DeliveryState.NOT_ATTEMPTED.value
    result["delivery_state"] = state
    result["navigation_verified"] = bool(result["navigation_verified"])
    result["target_uid_verified"] = bool(result["target_uid_verified"])
    result["room_verified"] = bool(result["room_verified"])
    result["text_entered"] = bool(result["text_entered"])
    result["send_tap_attempted"] = bool(result["send_tap_attempted"])
    result["server_echo_seen"] = bool(result["server_echo_seen"])
    result["reason"] = str(result.get("reason") or "")
    return result


def is_ambiguous_delivery(metadata: Mapping[str, Any] | None) -> bool:
    values = delivery_metadata(metadata)
    return (
        values["delivery_state"] == DeliveryState.AMBIGUOUS.value
        and values["send_tap_attempted"]
        and not values["server_echo_seen"]
    )
