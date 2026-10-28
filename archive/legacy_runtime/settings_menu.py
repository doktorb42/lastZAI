"""Small dependency-free terminal editor for normal PEva runtime settings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from config.runtime_settings import (
    ModelRegistry,
    NavigationSettings,
    PevaSettings,
    render_effective_settings,
    save_settings,
)


def run_settings_menu(
    settings: PevaSettings,
    registry: ModelRegistry,
    *,
    input_func: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> PevaSettings:
    """Edit common preferences; saving is explicit and never enables safety bypasses."""
    current = settings
    while True:
        output("\nPEva Settings\n=========================")
        output("1. Runtime mode")
        output("2. Auto-send")
        output("3. Capture channel")
        output("4. Chat model")
        output("5. LLM server")
        output("6. Logging")
        output("7. Navigation")
        output("8. Humanization / proactive preview")
        output("9. Save")
        output("10. Show effective settings")
        output("0. Exit")
        choice = input_func("Select: ").strip()
        if choice == "0":
            return current
        if choice == "1":
            value = _yes_no(input_func, "Use PlayerLoop", current.runtime.player_loop)
            current = replace(current, runtime=replace(current.runtime, player_loop=value))
        elif choice == "2":
            value = _yes_no(input_func, "Enable auto-send", current.runtime.auto_send)
            current = replace(current, runtime=replace(current.runtime, auto_send=value))
        elif choice == "3":
            value = _yes_no(input_func, "Capture private messages only", current.runtime.private_only)
            current = replace(current, runtime=replace(current.runtime, private_only=value))
        elif choice == "4":
            current = _edit_chat_model(current, registry, input_func=input_func, output=output)
        elif choice == "5":
            current = _edit_server(current, input_func=input_func, output=output)
        elif choice == "6":
            value = _yes_no(input_func, "Show message-content logs", current.logging.message_content)
            current = replace(current, logging=replace(current.logging, message_content=value))
        elif choice == "7":
            current = _edit_navigation(current, input_func=input_func, output=output)
        elif choice == "8":
            current = _edit_humanization(current, input_func=input_func)
        elif choice == "9":
            destination = save_settings(current)
            output(f"Saved: {destination}")
        elif choice == "10":
            output(render_effective_settings(current, registry))
        else:
            output("Unknown choice.")


def _edit_chat_model(
    settings: PevaSettings,
    registry: ModelRegistry,
    *,
    input_func: Callable[[str], str],
    output: Callable[[str], None],
) -> PevaSettings:
    entries = list(registry.models.values())
    output("\nAvailable models:")
    for index, model in enumerate(entries, start=1):
        suffix = f" [{model.role.upper()} ONLY]" if model.role == "action" else ""
        output(f"{index}. {model.name}{suffix}")
    custom_index = len(entries) + 1
    output(f"{custom_index}. Enter custom GGUF path")
    raw = input_func("Selected: ").strip()
    try:
        selected = int(raw)
    except ValueError:
        output("No change.")
        return settings
    if 1 <= selected <= len(entries):
        model = entries[selected - 1]
        if model.role == "action" and not settings.llm.allow_role_override:
            output("Rejected: action-only model cannot be selected for chat.")
            return settings
        choice = replace(settings.llm.chat_model, model_id=model.model_id, path=None, use_chat_model=False)
    elif selected == custom_index:
        raw_path = input_func("GGUF path: ").strip()
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = settings.project_root / path
        path = path.resolve()
        if path.suffix.lower() != ".gguf" or not path.is_file():
            output("Rejected: enter an existing .gguf file.")
            return settings
        choice = replace(settings.llm.chat_model, model_id=None, path=path, use_chat_model=False)
    else:
        output("No change.")
        return settings
    return replace(settings, llm=replace(settings.llm, chat_model=choice))


def _edit_server(
    settings: PevaSettings,
    *,
    input_func: Callable[[str], str],
    output: Callable[[str], None],
) -> PevaSettings:
    raw_port = input_func(f"Port [{settings.llm.port}]: ").strip()
    port = settings.llm.port if not raw_port else _bounded_int(raw_port, 1, 65535)
    if port is None:
        output("Rejected: port must be 1-65535.")
        return settings
    auto = _yes_no(input_func, "Auto-start llama-server", settings.llm.auto_start_server)
    return replace(settings, llm=replace(settings.llm, port=port, auto_start_server=auto))


def _edit_navigation(
    settings: PevaSettings,
    *,
    input_func: Callable[[str], str],
    output: Callable[[str], None],
) -> PevaSettings:
    nav = settings.navigation
    raw_poll = input_func(f"UI poll milliseconds [{nav.ui_effect_poll_ms}]: ").strip()
    raw_timeout = input_func(f"UI timeout milliseconds [{nav.ui_effect_timeout_ms}]: ").strip()
    raw_samples = input_func(f"Stable PRIVATE_CHAT samples [{nav.private_chat_stable_samples}]: ").strip()
    poll = nav.ui_effect_poll_ms if not raw_poll else _bounded_int(raw_poll, 100, 1000)
    timeout = nav.ui_effect_timeout_ms if not raw_timeout else _bounded_int(raw_timeout, 1000, 10000)
    samples = nav.private_chat_stable_samples if not raw_samples else _bounded_int(raw_samples, 2, 5)
    if poll is None or timeout is None or samples is None or timeout < poll * 2:
        output("Rejected: navigation values are outside safety bounds.")
        return settings
    return replace(settings, navigation=NavigationSettings(poll, timeout, samples))


def _edit_humanization(
    settings: PevaSettings,
    *,
    input_func: Callable[[str], str],
) -> PevaSettings:
    humanization = replace(
        settings.humanization,
        enabled=_yes_no(input_func, "Enable humanized prompt context", settings.humanization.enabled),
        naturalness_rewrite=_yes_no(input_func, "Allow one naturalness rewrite", settings.humanization.naturalness_rewrite),
        response_delay=_yes_no(input_func, "Schedule non-blocking reply delays", settings.humanization.response_delay),
    )
    proactive = replace(
        settings.proactive,
        enabled=_yes_no(input_func, "Enable proactive candidate preview", settings.proactive.enabled),
        auto_send=_yes_no(input_func, "Allow proactive auto-send (requires runtime auto-send too)", settings.proactive.auto_send),
    )
    return replace(settings, humanization=humanization, proactive=proactive)


def _yes_no(input_func: Callable[[str], str], label: str, current: bool) -> bool:
    raw = input_func(f"{label} [y/n, current={'y' if current else 'n'}]: ").strip().lower()
    if raw in {"y", "yes", "t", "tak", "1", "true"}:
        return True
    if raw in {"n", "no", "nie", "0", "false"}:
        return False
    return current


def _bounded_int(raw: str, minimum: int, maximum: int) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if minimum <= value <= maximum else None
