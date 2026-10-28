"""Central, typed runtime configuration for PEva.

Only operational preferences live here. Delivery and navigation safety
invariants deliberately remain code, not configurable switches.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"
DEFAULT_MODELS_PATH = PROJECT_ROOT / "config" / "models.json"


class SettingsError(RuntimeError):
    """A user-actionable configuration error."""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    player_loop: bool = True
    private_only: bool = True
    auto_send: bool = False
    clear_logcat: bool = True
    record_player_loop: bool = True
    social_debug: bool = False


@dataclass(frozen=True, slots=True)
class ModelRoleSettings:
    enabled: bool = True
    use_chat_model: bool = False
    model_id: str | None = None
    path: Path | None = None
    alias: str = "chat"


@dataclass(frozen=True, slots=True)
class LLMSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    auto_start_server: bool = False
    shutdown_managed_server: bool = True
    allow_role_override: bool = False
    chat_model: ModelRoleSettings = ModelRoleSettings(
        enabled=True, model_id="qwen3b-base", alias="chat"
    )
    intent_model: ModelRoleSettings = ModelRoleSettings(
        enabled=True, use_chat_model=True, alias="intent"
    )
    memory_model: ModelRoleSettings = ModelRoleSettings(
        enabled=True, use_chat_model=True, alias="memory"
    )
    action_model: ModelRoleSettings = ModelRoleSettings(
        enabled=False, alias="action"
    )

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class LlamaServerSettings:
    executable: Path | None = None
    gpu_layers: int = 999
    context_size: int = 4096
    threads: int = 0


@dataclass(frozen=True, slots=True)
class NavigationSettings:
    ui_effect_poll_ms: int = 200
    ui_effect_timeout_ms: int = 2000
    private_chat_stable_samples: int = 2


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    message_content: bool = True
    navigation: bool = True
    observations: bool = True


@dataclass(frozen=True, slots=True)
class HumanizationSettings:
    # Historical humanization data remains reviewable, but it is no longer a
    # behavioral controller or a second-pass personality rewriter.
    enabled: bool = False
    naturalness_rewrite: bool = False
    response_delay: bool = False
    relationship_adaptation: bool = False
    memory_adaptation: bool = False


@dataclass(frozen=True, slots=True)
class ProactiveSettings:
    enabled: bool = True
    auto_send: bool = False
    global_hourly_limit: int = 3
    same_player_min_minutes: int = 180
    same_topic_min_minutes: int = 720


@dataclass(frozen=True, slots=True)
class AgencySettings:
    enabled: bool = True
    # Preview-only fallback for ordinary private social bids that Agency
    # mistakenly classifies as a silent decision.  It is still hard-disabled
    # in PlayerLoop whenever runtime auto-send is active.
    social_preview_fallback: bool = True


@dataclass(frozen=True, slots=True)
class PevaSettings:
    profile: str = "default"
    runtime: RuntimeSettings = RuntimeSettings()
    llm: LLMSettings = LLMSettings()
    llama_server: LlamaServerSettings = LlamaServerSettings()
    navigation: NavigationSettings = NavigationSettings()
    logging: LoggingSettings = LoggingSettings()
    humanization: HumanizationSettings = HumanizationSettings()
    proactive: ProactiveSettings = ProactiveSettings()
    agency: AgencySettings = AgencySettings()
    project_root: Path = PROJECT_ROOT
    source_path: Path = DEFAULT_SETTINGS_PATH

    def public_dict(self) -> dict[str, Any]:
        """Return the effective schema without environment/API secrets."""
        return {
            "profile": self.profile,
            "runtime": asdict(self.runtime),
            "llm": {
                "host": self.llm.host,
                "port": self.llm.port,
                "auto_start_server": self.llm.auto_start_server,
                "shutdown_managed_server": self.llm.shutdown_managed_server,
                "allow_role_override": self.llm.allow_role_override,
                "chat_model": _model_to_dict(self.llm.chat_model),
                "intent_model": _model_to_dict(self.llm.intent_model),
                "memory_model": _model_to_dict(self.llm.memory_model),
                "action_model": _model_to_dict(self.llm.action_model),
            },
            "llama_server": {
                "executable": _path_to_text(self.llama_server.executable),
                "gpu_layers": self.llama_server.gpu_layers,
                "context_size": self.llama_server.context_size,
                "threads": self.llama_server.threads,
            },
            "navigation": asdict(self.navigation),
            "logging": asdict(self.logging),
            "humanization": asdict(self.humanization),
            "proactive": asdict(self.proactive),
            "agency": asdict(self.agency),
        }


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    model_id: str
    name: str
    path: Path
    role: str | None = None


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    models: Mapping[str, ModelDefinition]
    source_path: Path

    def get(self, model_id: str) -> ModelDefinition:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise SettingsError(
                f"Unknown model_id {model_id!r} in {self.source_path}."
            ) from exc


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    role: str
    model_id: str | None
    name: str
    path: Path
    declared_role: str | None


_TOP_LEVEL_KEYS = frozenset({"profile", "runtime", "llm", "llama_server", "navigation", "logging", "humanization", "proactive", "agency"})
_RUNTIME_KEYS = frozenset({"player_loop", "private_only", "auto_send", "clear_logcat", "record_player_loop", "social_debug"})
_LLM_KEYS = frozenset({"host", "port", "auto_start_server", "shutdown_managed_server", "allow_role_override", "chat_model", "intent_model", "memory_model", "action_model"})
_MODEL_KEYS = frozenset({"enabled", "use_chat_model", "model_id", "path", "alias"})
_LLAMA_KEYS = frozenset({"executable", "gpu_layers", "context_size", "threads"})
_NAVIGATION_KEYS = frozenset({"ui_effect_poll_ms", "ui_effect_timeout_ms", "private_chat_stable_samples"})
_LOGGING_KEYS = frozenset({"message_content", "navigation", "observations"})
_HUMANIZATION_KEYS = frozenset({"enabled", "naturalness_rewrite", "response_delay", "relationship_adaptation", "memory_adaptation"})
_PROACTIVE_KEYS = frozenset({"enabled", "auto_send", "global_hourly_limit", "same_player_min_minutes", "same_topic_min_minutes"})
_AGENCY_KEYS = frozenset({"enabled", "social_preview_fallback"})

# PEva's model server is deliberately local.  A remote endpoint would expose
# private chat context to a third party and is not a supported deployment
# mode for this project.
_LOCAL_LLM_HOSTS = frozenset({"127.0.0.1", "localhost"})


def load_settings(
    *,
    settings_path: str | Path | None = None,
    profile: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    project_root: str | Path | None = None,
) -> PevaSettings:
    """Merge safe defaults, settings file, optional profile, then CLI values."""
    root = Path(project_root or PROJECT_ROOT).expanduser().resolve()
    path = _resolve_path(settings_path or root / "config" / "settings.json", root)
    raw = _read_json_object(path, missing_ok=True, label="settings")
    _validate_known_keys(raw, _TOP_LEVEL_KEYS, "settings")

    selected_profile = str(profile or raw.get("profile") or "default").strip() or "default"
    profile_raw: dict[str, Any] = {}
    if selected_profile != "default":
        profile_path = root / "config" / "profiles" / f"{selected_profile}.json"
        profile_raw = _read_json_object(profile_path, missing_ok=False, label=f"profile {selected_profile!r}")
        _validate_known_keys(profile_raw, _TOP_LEVEL_KEYS - {"profile"}, f"profile {selected_profile!r}")

    merged = _deep_merge(_default_mapping(), raw)
    merged = _deep_merge(merged, profile_raw)
    if cli_overrides:
        merged = _deep_merge(merged, dict(cli_overrides))
    merged["profile"] = selected_profile
    return _build_settings(merged, root=root, source_path=path)


def load_model_registry(
    *,
    registry_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> ModelRegistry:
    root = Path(project_root or PROJECT_ROOT).expanduser().resolve()
    path = _resolve_path(registry_path or root / "config" / "models.json", root)
    raw = _read_json_object(path, missing_ok=True, label="model registry")
    _validate_known_keys(raw, {"models"}, "model registry")
    models_raw = raw.get("models", {})
    if not isinstance(models_raw, Mapping):
        raise SettingsError("model registry.models must be an object.")
    models: dict[str, ModelDefinition] = {}
    for model_id, item in models_raw.items():
        if not isinstance(model_id, str) or not model_id.strip():
            raise SettingsError("Every model registry key must be a non-empty string.")
        if not isinstance(item, Mapping):
            raise SettingsError(f"Model registry entry {model_id!r} must be an object.")
        _validate_known_keys(item, {"name", "path", "role"}, f"model {model_id!r}")
        name = _required_text(item.get("name"), f"model {model_id!r}.name")
        model_path = _required_text(item.get("path"), f"model {model_id!r}.path")
        role = _optional_text(item.get("role"), f"model {model_id!r}.role")
        if role is not None and role not in {"chat", "intent", "memory", "action"}:
            raise SettingsError(f"model {model_id!r}.role is not a supported role.")
        models[model_id] = ModelDefinition(
            model_id=model_id,
            name=name,
            path=_resolve_path(model_path, root),
            role=role,
        )
    return ModelRegistry(models=models, source_path=path)


def resolve_model_for_role(
    settings: PevaSettings,
    registry: ModelRegistry,
    *,
    role: str = "chat",
) -> ResolvedModel:
    if role not in {"chat", "intent", "memory", "action"}:
        raise SettingsError(f"Unsupported model role {role!r}.")
    choice = getattr(settings.llm, f"{role}_model")
    if choice.use_chat_model:
        choice = settings.llm.chat_model
    if not choice.enabled:
        raise SettingsError(f"The {role}_model is disabled.")
    if choice.model_id:
        definition = registry.get(choice.model_id)
        model_id = definition.model_id
        name = definition.name
        path = definition.path
        declared_role = definition.role
    elif choice.path is not None:
        model_id = None
        name = choice.path.name
        path = choice.path
        declared_role = None
    else:
        raise SettingsError(
            f"{role}_model needs model_id or path (or use_chat_model=true)."
        )
    if declared_role and declared_role != role and not settings.llm.allow_role_override:
        raise SettingsError(
            f"Model {model_id or path.name!r} is declared role={declared_role!r}; "
            f"it cannot be used as {role}_model without llm.allow_role_override=true."
        )
    if not path.is_file():
        raise SettingsError(f"Selected {role} model file does not exist: {path}")
    return ResolvedModel(role, model_id, name, path, declared_role)


def save_settings(settings: PevaSettings, *, path: str | Path | None = None) -> Path:
    destination = _resolve_path(path or settings.source_path, settings.project_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(settings.public_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def render_effective_settings(settings: PevaSettings, registry: ModelRegistry | None = None) -> str:
    """Human-readable, secret-free effective configuration for --show-settings."""
    payload = settings.public_dict()
    if registry is not None:
        try:
            selected = resolve_model_for_role(settings, registry, role="chat")
        except SettingsError as exc:
            payload["selected_chat_model_error"] = str(exc)
        else:
            payload["selected_chat_model"] = {
                "id": selected.model_id,
                "name": selected.name,
                "path": str(selected.path),
            }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def with_runtime_settings(settings: PevaSettings, **changes: bool) -> PevaSettings:
    """Small immutable helper used by the terminal settings menu and tests."""
    allowed = set(_RUNTIME_KEYS)
    unknown = set(changes) - allowed
    if unknown:
        raise SettingsError(f"Unknown runtime setting(s): {', '.join(sorted(unknown))}")
    return replace(settings, runtime=replace(settings.runtime, **changes))


def _default_mapping() -> dict[str, Any]:
    return {
        "profile": "default",
        "runtime": asdict(RuntimeSettings()),
        "llm": {
            "host": "127.0.0.1",
            "port": 8080,
            "auto_start_server": False,
            "shutdown_managed_server": True,
            "allow_role_override": False,
            "chat_model": _model_to_dict(LLMSettings().chat_model),
            "intent_model": _model_to_dict(LLMSettings().intent_model),
            "memory_model": _model_to_dict(LLMSettings().memory_model),
            "action_model": _model_to_dict(LLMSettings().action_model),
        },
        "llama_server": {
            "executable": "",
            "gpu_layers": 999,
            "context_size": 4096,
            "threads": 0,
        },
        "navigation": asdict(NavigationSettings()),
        "logging": asdict(LoggingSettings()),
        "humanization": asdict(HumanizationSettings()),
        "proactive": asdict(ProactiveSettings()),
        "agency": asdict(AgencySettings()),
    }


def _build_settings(raw: Mapping[str, Any], *, root: Path, source_path: Path) -> PevaSettings:
    _validate_known_keys(raw, _TOP_LEVEL_KEYS, "settings")
    profile = _required_text(raw.get("profile"), "profile")
    runtime_raw = _object(raw.get("runtime"), "runtime")
    llm_raw = _object(raw.get("llm"), "llm")
    server_raw = _object(raw.get("llama_server"), "llama_server")
    navigation_raw = _object(raw.get("navigation"), "navigation")
    logging_raw = _object(raw.get("logging"), "logging")
    humanization_raw = _object(raw.get("humanization"), "humanization")
    proactive_raw = _object(raw.get("proactive"), "proactive")
    agency_raw = _object(raw.get("agency"), "agency")
    _validate_known_keys(runtime_raw, _RUNTIME_KEYS, "runtime")
    _validate_known_keys(llm_raw, _LLM_KEYS, "llm")
    _validate_known_keys(server_raw, _LLAMA_KEYS, "llama_server")
    _validate_known_keys(navigation_raw, _NAVIGATION_KEYS, "navigation")
    _validate_known_keys(logging_raw, _LOGGING_KEYS, "logging")
    _validate_known_keys(humanization_raw, _HUMANIZATION_KEYS, "humanization")
    _validate_known_keys(proactive_raw, _PROACTIVE_KEYS, "proactive")
    _validate_known_keys(agency_raw, _AGENCY_KEYS, "agency")

    runtime = RuntimeSettings(**{key: _bool(runtime_raw.get(key), f"runtime.{key}") for key in _RUNTIME_KEYS})
    roles = {
        role: _build_model_role(_object(llm_raw.get(f"{role}_model"), f"llm.{role}_model"), role, root)
        for role in ("chat", "intent", "memory", "action")
    }
    llm = LLMSettings(
        host=_host(llm_raw.get("host")),
        port=_int_range(llm_raw.get("port"), "llm.port", 1, 65535),
        auto_start_server=_bool(llm_raw.get("auto_start_server"), "llm.auto_start_server"),
        shutdown_managed_server=_bool(llm_raw.get("shutdown_managed_server"), "llm.shutdown_managed_server"),
        allow_role_override=_bool(llm_raw.get("allow_role_override"), "llm.allow_role_override"),
        chat_model=roles["chat"],
        intent_model=roles["intent"],
        memory_model=roles["memory"],
        action_model=roles["action"],
    )
    if not llm.chat_model.enabled:
        raise SettingsError("llm.chat_model.enabled must remain true for PEva runtime.")
    server = LlamaServerSettings(
        executable=_path_from_optional(server_raw.get("executable"), root, "llama_server.executable"),
        gpu_layers=_int_range(server_raw.get("gpu_layers"), "llama_server.gpu_layers", 0, 999),
        context_size=_int_range(server_raw.get("context_size"), "llama_server.context_size", 512, 32768),
        threads=_int_range(server_raw.get("threads"), "llama_server.threads", 0, 256),
    )
    navigation = NavigationSettings(
        ui_effect_poll_ms=_int_range(navigation_raw.get("ui_effect_poll_ms"), "navigation.ui_effect_poll_ms", 100, 1000),
        ui_effect_timeout_ms=_int_range(navigation_raw.get("ui_effect_timeout_ms"), "navigation.ui_effect_timeout_ms", 1000, 10000),
        private_chat_stable_samples=_int_range(navigation_raw.get("private_chat_stable_samples"), "navigation.private_chat_stable_samples", 2, 5),
    )
    if navigation.ui_effect_timeout_ms < navigation.ui_effect_poll_ms * 2:
        raise SettingsError("navigation.ui_effect_timeout_ms must allow at least two polls.")
    logging = LoggingSettings(**{key: _bool(logging_raw.get(key), f"logging.{key}") for key in _LOGGING_KEYS})
    humanization = HumanizationSettings(**{
        key: _bool(humanization_raw.get(key), f"humanization.{key}")
        for key in _HUMANIZATION_KEYS
    })
    proactive = ProactiveSettings(
        enabled=_bool(proactive_raw.get("enabled"), "proactive.enabled"),
        auto_send=_bool(proactive_raw.get("auto_send"), "proactive.auto_send"),
        global_hourly_limit=_int_range(proactive_raw.get("global_hourly_limit"), "proactive.global_hourly_limit", 0, 12),
        same_player_min_minutes=_int_range(proactive_raw.get("same_player_min_minutes"), "proactive.same_player_min_minutes", 30, 1440),
        same_topic_min_minutes=_int_range(proactive_raw.get("same_topic_min_minutes"), "proactive.same_topic_min_minutes", 60, 2880),
    )
    agency = AgencySettings(
        enabled=_bool(agency_raw.get("enabled"), "agency.enabled"),
        social_preview_fallback=_bool(
            agency_raw.get("social_preview_fallback"),
            "agency.social_preview_fallback",
        ),
    )
    return PevaSettings(
        profile=profile,
        runtime=runtime,
        llm=llm,
        llama_server=server,
        navigation=navigation,
        logging=logging,
        humanization=humanization,
        proactive=proactive,
        agency=agency,
        project_root=root,
        source_path=source_path,
    )


def _build_model_role(raw: Mapping[str, Any], role: str, root: Path) -> ModelRoleSettings:
    _validate_known_keys(raw, _MODEL_KEYS, f"llm.{role}_model")
    return ModelRoleSettings(
        enabled=_bool(raw.get("enabled"), f"llm.{role}_model.enabled"),
        use_chat_model=_bool(raw.get("use_chat_model"), f"llm.{role}_model.use_chat_model"),
        model_id=_optional_text(raw.get("model_id"), f"llm.{role}_model.model_id"),
        path=_path_from_optional(raw.get("path"), root, f"llm.{role}_model.path"),
        alias=_required_text(raw.get("alias"), f"llm.{role}_model.alias"),
    )


def _read_json_object(path: Path, *, missing_ok: bool, label: str) -> dict[str, Any]:
    if not path.is_file():
        if missing_ok:
            return {}
        raise SettingsError(f"{label.capitalize()} file does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsError(f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except OSError as exc:
        raise SettingsError(f"Cannot read {label} file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SettingsError(f"{label.capitalize()} file {path} must contain one JSON object.")
    return dict(payload)


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_known_keys(payload: Mapping[str, Any], allowed: set[str] | frozenset[str], label: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise SettingsError(f"Unknown setting(s) in {label}: {', '.join(unknown)}")


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsError(f"{label} must be a JSON object.")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsError(f"{label} must be true or false.")
    return value


def _int_range(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"{label} must be an integer.")
    if not minimum <= value <= maximum:
        raise SettingsError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _host(value: Any) -> str:
    result = _required_text(value, "llm.host")
    if "://" in result or "/" in result or any(char.isspace() for char in result):
        raise SettingsError("llm.host must be a plain hostname or IP address.")
    normalized = result.casefold()
    if normalized not in _LOCAL_LLM_HOSTS:
        raise SettingsError(
            "llm.host must be a local loopback address (127.0.0.1 or localhost); "
            "remote model endpoints are disabled to protect private chat data."
        )
    return normalized


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{label} must be a non-empty string.")
    return value.strip()


def _optional_text(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    return _required_text(value, label)


def _path_from_optional(value: Any, root: Path, label: str) -> Path | None:
    text = _optional_text(value, label)
    return _resolve_path(text, root) if text else None


def _resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _model_to_dict(model: ModelRoleSettings) -> dict[str, Any]:
    return {
        "enabled": model.enabled,
        "use_chat_model": model.use_chat_model,
        "model_id": model.model_id or "",
        "path": _path_to_text(model.path),
        "alias": model.alias,
    }


def _path_to_text(path: Path | None) -> str:
    return str(path) if path is not None else ""
