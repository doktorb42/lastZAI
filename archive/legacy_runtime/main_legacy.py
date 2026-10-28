from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from archive.legacy_runtime import adb_controll
from oczy import screen_detection as screenDetector
from archive.legacy_runtime.action_ex import (
    ActionExecutor,
    build_private_reply_action,
)
from archive.legacy_runtime.agent import LastZAgent
from archive.legacy_runtime.chat_capture import ChatCapture
from mozg.conversation_store import ConversationStore
from oczy.game_state import get_game_state
from mozg.lastz_ai import AIConfig, LastZAI
from mozg.llama_server_manager import LlamaServerManager, start_configured_server
from archive.legacy_runtime.navigation_manager import NavigationManager
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig, PlayerLoopRecorder
from archive.legacy_runtime.private_chat_navigation import next_private_chat_transition
from config.runtime_config import (
    ADB_DEVICE,
    ADB_PATH,
    CONVERSATION_DB_PATH,
    DATA_DIR,
    MAX_MESSAGE_CHARS,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    SELF_UID,
    TEMPLATES_DIR,
    is_direct_private_room,
)
from config.runtime_settings import (
    ModelRegistry,
    PevaSettings,
    ResolvedModel,
    SettingsError,
    load_model_registry,
    load_settings,
    render_effective_settings,
    resolve_model_for_role,
)
from archive.legacy_runtime.settings_menu import run_settings_menu
from archive.legacy_runtime.coordinates import (
    ALLIANCE_CHAT_TAB,
    BACK_BUTTON,
    CHAT_BUTTON,
    PRIVATE_CHAT_TAB,
    PRIVATE_PERSON_FIRST_Y,
    PRIVATE_PERSON_X,
    SEND_TEXT,
    WRITE_CHAT,
)


DEFAULT_DB_PATH = CONVERSATION_DB_PATH

DEFAULT_SCREENSHOT_NAME = "main_current.png"


# ======================================================================
# CONFIG
# ======================================================================


@dataclass(slots=True)
class MainConfig:
    auto_send: bool = False
    clear_logcat: bool = True

    # 0 = unlimited
    max_messages: int = 0

    capture_poll_timeout: float = 0.50
    navigation_timeout: float = 12.0
    navigation_step_delay: float = 0.70

    # Consecutive UNKNOWN detections allowed while navigating.
    max_unknown_states: int = 4

    screenshot_name: str = DEFAULT_SCREENSHOT_NAME


# ======================================================================
# APPLICATION
# ======================================================================


class LastZApplication:
    """
    Main runtime for the current PEva architecture.

    Flow:

        Last Z push.chat.room
                ↓
        chat_capture.py
                ↓
        agent.py
                ↓
        conversation_store.py + lastz_ai.py
                ↓
        PendingReply
                ↓
        action_ex.py
                ↓
        adb_controll.py
                ↓
        UID-verified PM + send confirmation
                ↓
        agent.mark_reply_sent()
                ↓
        conversation.db

    In preview mode the full pipeline stops before game navigation/sending.
    """

    def __init__(
        self,
        config: MainConfig,
        *,
        settings: PevaSettings | None = None,
        chat_model: ResolvedModel | None = None,
    ):
        self.config = config
        self.settings = settings
        self.chat_model = chat_model

        ai_config = AIConfig.from_env()
        if settings is not None:
            ai_config.base_url = settings.llm.endpoint
        if chat_model is not None:
            ai_config.model = str(chat_model.path)
        self.ai = LastZAI(config=ai_config)
        self.store = ConversationStore(
            DEFAULT_DB_PATH
        )
        self.agent = LastZAgent(
            ai=self.ai,
            store=self.store,
        )
        self.executor = ActionExecutor()
        self.capture = ChatCapture()
        self.server_manager: LlamaServerManager | None = None

        self._processed_messages = 0

    # ------------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self.capture.stop()
        except Exception:
            pass

        try:
            self.agent.close()
        except Exception:
            pass

        # agent does not own the injected store, so close it here.
        try:
            self.store.close()
        except Exception:
            pass

        if self.server_manager is not None:
            try:
                self.server_manager.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # PREFLIGHT
    # ------------------------------------------------------------------

    def preflight(
        self,
        *,
        require_ai: bool = True,
        require_adb: bool = True,
    ) -> bool:
        print("=" * 68)
        print("LAST Z AI - PREFLIGHT")
        print("=" * 68)

        checks: list[
            tuple[str, bool, str]
        ] = []

        if self.settings is not None:
            source_detail = (
                str(self.settings.source_path)
                if self.settings.source_path.is_file()
                else f"safe defaults (missing {self.settings.source_path})"
            )
            checks.extend([
                ("Settings", True, source_detail),
                ("Profile", True, self.settings.profile),
                (
                    "Runtime mode",
                    True,
                    (
                        f"player_loop={str(self.settings.runtime.player_loop).lower()} "
                        f"private_only={str(self.settings.runtime.private_only).lower()} "
                        f"auto_send={str(self.settings.runtime.auto_send).lower()}"
                    ),
                ),
                ("LLM endpoint", True, self.settings.llm.endpoint),
            ])
            if self.chat_model is not None:
                model_ok = (
                    self.chat_model.path.is_file()
                    if self.settings.llm.auto_start_server else True
                )
                checks.append((
                    "Chat model",
                    model_ok,
                    self.chat_model.model_id or self.chat_model.name,
                ))

        # Interfaces first. This detects accidentally leaving an older
        # file from one of the previous project steps in the folder.
        interface_checks = [
            (
                "chat_capture.ChatCapture.start",
                callable(
                    getattr(
                        self.capture,
                        "start",
                        None,
                    )
                ),
            ),
            (
                "conversation_store.get_ai_context",
                callable(
                    getattr(
                        self.store,
                        "get_ai_context",
                        None,
                    )
                ),
            ),
            (
                "agent.process_chat_message",
                callable(
                    getattr(
                        self.agent,
                        "process_chat_message",
                        None,
                    )
                ),
            ),
            (
                "agent.mark_reply_sent",
                callable(
                    getattr(
                        self.agent,
                        "mark_reply_sent",
                        None,
                    )
                ),
            ),
            (
                "action_ex.build_private_reply_action",
                callable(
                    build_private_reply_action
                ),
            ),
            (
                "adb_controll.send_private_reply_verified",
                callable(
                    getattr(
                        adb_controll,
                        "send_private_reply_verified",
                        None,
                    )
                ),
            ),
            (
                "adb_controll.navigate_private_reply_target_verified",
                callable(
                    getattr(
                        adb_controll,
                        "navigate_private_reply_target_verified",
                        None,
                    )
                ),
            ),
        ]

        for name, ok in interface_checks:
            checks.append(
                (
                    name,
                    ok,
                    "available" if ok else "MISSING / old file",
                )
            )

        # One-source-of-truth configuration checks. A UID/device mismatch here
        # is unsafe because capture and verified sending would refer to
        # different accounts/devices.
        identity_values = {
            str(SELF_UID),
            str(self.capture.config.self_uid),
            str(self.agent.config.own_uid),
            str(adb_controll.SELF_UID),
        }
        identity_ok = len(identity_values) == 1
        checks.append(
            (
                "Runtime self UID",
                identity_ok,
                (
                    str(SELF_UID)
                    if identity_ok
                    else f"MISMATCH: {sorted(identity_values)}"
                ),
            )
        )

        adb_config_ok = (
            str(self.capture.config.adb_path) == str(ADB_PATH)
            and str(self.capture.config.device) == str(ADB_DEVICE)
            and str(adb_controll.ADB_PATH) == str(ADB_PATH)
            and str(adb_controll.DEVICE) == str(ADB_DEVICE)
        )
        checks.append(
            (
                "Runtime ADB config",
                adb_config_ok,
                (
                    f"{ADB_DEVICE} via {ADB_PATH}"
                    if adb_config_ok
                    else "Capture/ADB controller configuration mismatch"
                ),
            )
        )

        limit_ok = (
            int(self.ai.config.max_reply_chars) == int(MAX_MESSAGE_CHARS)
            and int(self.executor.policy.max_message_chars)
            == int(MAX_MESSAGE_CHARS)
        )
        checks.append(
            (
                "Message length contract",
                limit_ok,
                (
                    f"{MAX_MESSAGE_CHARS} chars"
                    if limit_ok
                    else (
                        f"AI={self.ai.config.max_reply_chars}, "
                        f"executor={self.executor.policy.max_message_chars}, "
                        f"runtime={MAX_MESSAGE_CHARS}"
                    )
                ),
            )
        )

        room_contract_ok = (
            is_direct_private_room(
                f"custom_999_{SELF_UID}_v2",
                self_uid=SELF_UID,
                peer_uid="999",
            )
            and not is_direct_private_room(
                "custom_lang_en_769",
                self_uid=SELF_UID,
                peer_uid="999",
            )
        )
        checks.append(
            (
                "Direct PM room contract",
                room_contract_ok,
                (
                    "strict custom_<peer>_<self>_vN"
                    if room_contract_ok
                    else "private-room validator failed"
                ),
            )
        )

        try:
            prompt_path = self.ai.prompts.path_for(
                self.ai.config.system_prompt_name
            )
            self.ai.prompts.get(
                self.ai.config.system_prompt_name
            )
            prompt_ok = prompt_path.is_file()
            prompt_detail = str(prompt_path)
        except Exception as exc:
            prompt_ok = False
            prompt_detail = f"{type(exc).__name__}: {exc}"

        checks.append(
            (
                "Reply system prompt",
                prompt_ok,
                prompt_detail,
            )
        )

        critical_templates = {
            "alliance_selected",
            "alliance_unselected",
            "private_selected",
            "private_unselected",
            "private_conversation",
            "hq_button",
            "world_button",
            "unread_dot",
        }
        template_info = screenDetector.template_diagnostics()
        missing_templates = sorted(
            name
            for name in critical_templates
            if not bool(
                template_info.get(name, {}).get("ok")
            )
        )
        templates_ok = not missing_templates
        checks.append(
            (
                "Screen templates",
                templates_ok,
                (
                    str(TEMPLATES_DIR)
                    if templates_ok
                    else "missing: " + ", ".join(missing_templates)
                ),
            )
        )

        coordinate_points = {
            "CHAT_BUTTON": CHAT_BUTTON,
            "ALLIANCE_CHAT_TAB": ALLIANCE_CHAT_TAB,
            "PRIVATE_CHAT_TAB": PRIVATE_CHAT_TAB,
            "WRITE_CHAT": WRITE_CHAT,
            "SEND_TEXT": SEND_TEXT,
            "BACK_BUTTON": BACK_BUTTON,
            "PRIVATE_PERSON_FIRST": (
                PRIVATE_PERSON_X,
                PRIVATE_PERSON_FIRST_Y,
            ),
        }
        invalid_points = [
            name
            for name, (x, y) in coordinate_points.items()
            if not (
                0 <= int(x) < int(SCREEN_WIDTH)
                and 0 <= int(y) < int(SCREEN_HEIGHT)
            )
        ]
        coordinates_ok = not invalid_points
        checks.append(
            (
                "UI coordinate bounds",
                coordinates_ok,
                (
                    f"{SCREEN_WIDTH}x{SCREEN_HEIGHT}"
                    if coordinates_ok
                    else "out of bounds: " + ", ".join(invalid_points)
                ),
            )
        )

        if require_adb:
            try:
                adb_ok = (
                    adb_controll.check_connection()
                    and self.capture.check_connection()
                )
            except Exception as exc:
                adb_ok = False
                adb_detail = (
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                adb_detail = (
                    "BlueStacks connected"
                    if adb_ok
                    else "BlueStacks/ADB not connected"
                )

            checks.append(
                (
                    "ADB",
                    adb_ok,
                    adb_detail,
                )
            )

            if adb_ok:
                expected_size = (
                    int(SCREEN_WIDTH),
                    int(SCREEN_HEIGHT),
                )

                wm_size = adb_controll.get_screen_size()

                # `wm size` can be reported in BlueStacks' natural display
                # orientation while Last Z is portrait-locked. A reversed
                # pair is therefore not an error by itself.
                wm_ok = (
                    wm_size == expected_size
                    or (
                        wm_size is not None
                        and wm_size
                        == (
                            expected_size[1],
                            expected_size[0],
                        )
                    )
                )

                if wm_size is None:
                    wm_detail = "could not read `wm size`"
                elif wm_size == expected_size:
                    wm_detail = (
                        f"{wm_size[0]}x{wm_size[1]}"
                    )
                elif wm_size == (
                    expected_size[1],
                    expected_size[0],
                ):
                    wm_detail = (
                        f"{wm_size[0]}x{wm_size[1]} "
                        f"(natural/rotated orientation; expected input "
                        f"space {expected_size[0]}x{expected_size[1]})"
                    )
                else:
                    wm_detail = (
                        f"expected {expected_size[0]}x{expected_size[1]} "
                        f"or rotated {expected_size[1]}x{expected_size[0]}, "
                        f"got {wm_size[0]}x{wm_size[1]}"
                    )

                checks.append(
                    (
                        "BlueStacks wm size",
                        wm_ok,
                        wm_detail,
                    )
                )

                # This is the important check for our coordinates/templates:
                # it reads the dimensions of the CURRENT rotated screenshot.
                screencap_size = (
                    adb_controll.get_screencap_size()
                )
                screencap_ok = (
                    screencap_size == expected_size
                )

                if screencap_size is None:
                    screencap_detail = (
                        "could not read screencap PNG size"
                    )
                elif screencap_ok:
                    screencap_detail = (
                        f"{screencap_size[0]}x"
                        f"{screencap_size[1]}"
                    )
                else:
                    screencap_detail = (
                        f"expected current game/input space "
                        f"{expected_size[0]}x{expected_size[1]}, got "
                        f"{screencap_size[0]}x{screencap_size[1]}"
                    )

                checks.append(
                    (
                        "Last Z rotated screencap size",
                        screencap_ok,
                        screencap_detail,
                    )
                )

        if require_ai:
            try:
                ai_ok = self.ai.health_check()
            except Exception as exc:
                ai_ok = False
                ai_detail = (
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                ai_detail = (
                    f"{self.ai.config.base_url} healthy"
                    if ai_ok
                    else (
                        f"No healthy llama-server at "
                        f"{self.ai.config.base_url}"
                    )
                )

            checks.append(
                (
                    "Local LLM",
                    ai_ok,
                    ai_detail,
                )
            )

        try:
            self.store.message_count()

            with sqlite3.connect(
                self.store.db_path
            ) as conn:
                integrity = conn.execute(
                    "PRAGMA integrity_check;"
                ).fetchone()

            integrity_value = (
                str(integrity[0]).lower()
                if integrity
                else ""
            )
            db_ok = integrity_value == "ok"
            db_detail = (
                f"{self.store.db_path} (integrity={integrity_value or 'unknown'})"
            )
        except Exception as exc:
            db_ok = False
            db_detail = (
                f"{type(exc).__name__}: {exc}"
            )

        checks.append(
            (
                "SQLite store",
                db_ok,
                db_detail,
            )
        )

        all_ok = True

        for name, ok, detail in checks:
            marker = "OK" if ok else "FAIL"

            print(
                f"[{marker:4}] {name}: {detail}"
            )

            if not ok:
                all_ok = False

        print("-" * 68)

        if all_ok:
            print("[PREFLIGHT] ALL CHECKS PASSED")
        else:
            print(
                "[PREFLIGHT] FAILED - runtime will not start."
            )

        return all_ok

    # ------------------------------------------------------------------
    # SCREEN STATE
    # ------------------------------------------------------------------

    def read_game_state(
        self,
    ) -> dict[str, Any] | None:
        """
        Capture one screenshot and run the existing deterministic
        screen detector.
        """

        try:
            path = adb_controll.screenshot(
                self.config.screenshot_name
            )
        except Exception as exc:
            print(
                "[SCREEN ERROR]",
                f"{type(exc).__name__}: {exc}",
            )
            return None

        screen = cv2.imread(
            str(path)
        )

        if screen is None:
            print(
                "[SCREEN ERROR] "
                f"Cannot load screenshot: {path}"
            )
            return None

        try:
            return get_game_state(
                screen
            )
        except Exception as exc:
            print(
                "[STATE ERROR]",
                f"{type(exc).__name__}: {exc}",
            )
            return None

    # ------------------------------------------------------------------
    # NAVIGATION
    # ------------------------------------------------------------------

    def ensure_private_chat_list(
        self,
    ) -> dict[str, Any] | None:
        """
        Move only through known chat states until PRIVATE_CHAT is visible.

        Fail-closed behavior:
        - UNKNOWN does not cause blind taps;
        - unsupported screens do not cause blind taps;
        - navigation has a timeout;
        - UID selection/sending begins only after PRIVATE_CHAT is confirmed.
        """

        deadline = (
            time.monotonic()
            + self.config.navigation_timeout
        )

        unknown_count = 0

        while (
            time.monotonic()
            < deadline
        ):
            state = self.read_game_state()

            if state is None:
                time.sleep(
                    self.config.navigation_step_delay
                )
                continue

            screen = str(
                state.get(
                    "screen",
                    "UNKNOWN",
                )
            )

            print(
                "[NAV]",
                f"screen={screen}",
            )

            transition = next_private_chat_transition(screen)
            if transition.step == "private_chat_ready":
                return state
            if transition.action is not None:
                result = self.executor.execute(
                    {
                        "action": transition.action,
                    },
                    state,
                )

                print(
                    "[NAV RESULT]",
                    result.status,
                    "-",
                    result.message,
                )

                if not result.ok:
                    return None

                time.sleep(
                    self.config.navigation_step_delay
                )
                continue

            # Never tap blindly from UNKNOWN or a screen we do not understand.
            unknown_count += 1

            print(
                "[NAV WAIT] Unknown/unsupported screen "
                f"({unknown_count}/{self.config.max_unknown_states})."
            )

            if (
                unknown_count
                >= self.config.max_unknown_states
            ):
                print(
                    "[NAV BLOCKED] Could not safely reach PRIVATE_CHAT."
                )
                return None

            time.sleep(
                self.config.navigation_step_delay
            )

        print(
            "[NAV BLOCKED] Navigation timeout."
        )

        return None

    # ------------------------------------------------------------------
    # ONE MESSAGE
    # ------------------------------------------------------------------

    def process_message(
        self,
        message: Any,
    ) -> None:
        sender = (
            getattr(
                message,
                "sender_name",
                None,
            )
            or getattr(
                message,
                "sender_uid",
                "unknown",
            )
        )

        sender_uid = str(
            getattr(
                message,
                "sender_uid",
                "",
            )
        )

        incoming_text = str(
            getattr(
                message,
                "text",
                "",
            )
        )

        print()
        print("=" * 68)
        print(
            f"[IN] {sender} ({sender_uid})"
        )
        print(
            "[TEXT]",
            incoming_text,
        )

        result = self.agent.process_chat_message(
            message
        )

        print(
            "[AGENT]",
            result.status,
            "-",
            result.message,
        )

        if not result.reply_ready:
            if result.ai_reply is not None:
                if result.ai_reply.error:
                    print(
                        "[AI ERROR]",
                        result.ai_reply.error,
                    )

            return

        pending = result.pending_reply

        if pending is None:
            print(
                "[ERROR] reply_ready without PendingReply."
            )
            return

        print(
            "[AI]",
            pending.text,
        )
        print(
            "[REPLY ID]",
            pending.reply_id,
        )

        if not self.config.auto_send:
            print(
                "[PREVIEW] Nothing was sent to Last Z."
            )

            # Avoid an ever-growing RAM-only queue during preview runs.
            self.agent.discard_reply(
                pending.reply_id
            )

            return

        # ----------------------------------------------------------
        # AUTO-SEND
        # ----------------------------------------------------------

        print(
            "[SEND] Preparing UID-verified private reply..."
        )

        private_state = (
            self.ensure_private_chat_list()
        )

        if private_state is None:
            print(
                "[SEND BLOCKED] Could not safely open PRIVATE_CHAT."
            )
            print(
                "[PENDING] Reply remains pending:",
                pending.reply_id,
            )
            return

        action = build_private_reply_action(
            pending
        )

        # The legacy one-message path shares the same split contract as
        # PlayerLoop: prove the UID/room target first, then allow physical
        # text entry only from that verified conversation.
        prepare_action = {
            **action,
            "action": "prepare_private_reply_target",
        }
        prepare_result = self.executor.execute(
            prepare_action,
            private_state,
        )
        if not prepare_result.ok:
            print(
                "[SEND BLOCKED] Target conversation was not verified:",
                prepare_result.message,
            )
            print("[PENDING] Reply remains pending:", pending.reply_id)
            return
        verified_state = {
            **private_state,
            "screen": "PRIVATE_CONVERSATION",
            "current_conversation_verified": True,
            "current_conversation_uid": prepare_result.data.get("current_conversation_uid"),
            "current_conversation_room_id": prepare_result.data.get("current_conversation_room_id"),
        }

        print(
            "[ACTION]",
            {
                "action": action.get("action"),
                "reply_id": action.get("reply_id"),
                "target_uid": action.get("target_uid"),
                "room_id": action.get("room_id"),
                "text": action.get("text"),
            },
        )

        action_result = self.executor.execute(
            action,
            verified_state,
        )

        print(
            "[SEND RESULT]",
            action_result.status,
            "-",
            action_result.message,
        )

        if not (
            action_result.ok
            and action_result.status == "sent"
        ):
            # Deliberately no automatic retry:
            # if Send was tapped but the server echo arrived late, retrying
            # blindly could duplicate the message.
            print(
                "[PENDING] Reply was NOT marked as sent."
            )
            print(
                "[PENDING ID]",
                pending.reply_id,
            )
            print(
                "[INFO] No automatic retry will be attempted."
            )
            return

        transport = action_result.data.get(
            "transport_result"
        )

        server_time = None
        seq_id = None

        if isinstance(
            transport,
            dict,
        ):
            server_time = transport.get(
                "server_time"
            )
            seq_id = transport.get(
                "seq_id"
            )

        outgoing_id = self.agent.mark_reply_sent(
            pending.reply_id,
            sender_uid=self.agent.config.own_uid,
            sender_name="PEva",
            server_time=server_time,
        )

        print(
            "[CONFIRMED] Reply stored in conversation.db"
        )
        print(
            "[OUTGOING DB ID]",
            outgoing_id,
        )

        if seq_id is not None:
            print(
                "[LAST Z SEQ ID]",
                seq_id,
            )

    # ------------------------------------------------------------------
    # MAIN LOOP
    # ------------------------------------------------------------------

    def run(
        self,
    ) -> None:
        if self.config.auto_send:
            raise RuntimeError(
                "Legacy one-message auto-send is disabled. Use the persistent PlayerLoop path."
            )
        if not self.preflight(
            require_ai=True,
            require_adb=True,
        ):
            raise SystemExit(1)

        print()
        print("=" * 68)
        print("LAST Z AI - RUNTIME")
        print("=" * 68)
        print(
            "Mode:",
            (
                "AUTO SEND"
                if self.config.auto_send
                else "PREVIEW"
            ),
        )
        print(
            "Database:",
            self.store.db_path,
        )
        print(
            "Self UID:",
            self.agent.config.own_uid,
        )

        if self.config.max_messages > 0:
            print(
                "Stop after messages:",
                self.config.max_messages,
            )

        print()

        self.capture.start(
            clear_logcat=self.config.clear_logcat
        )

        print(
            "[CAPTURE] Waiting for private messages..."
        )
        print(
            "[INFO] Ctrl+C to stop."
        )

        try:
            while True:
                message = self.capture.get_message(
                    timeout=(
                        self.config.capture_poll_timeout
                    )
                )

                if message is None:
                    if (
                        self.capture.last_error
                        and not self.capture.is_running
                    ):
                        print(
                            "[CAPTURE ERROR]",
                            self.capture.last_error,
                        )
                        break

                    continue

                self._processed_messages += 1

                try:
                    self.process_message(
                        message
                    )
                except Exception as exc:
                    # One bad message should not kill the entire listener.
                    print(
                        "[MESSAGE ERROR]",
                        f"{type(exc).__name__}: {exc}",
                    )

                if (
                    self.config.max_messages > 0
                    and self._processed_messages
                    >= self.config.max_messages
                ):
                    print()
                    print(
                        "[INFO] max_messages reached."
                    )
                    break

        except KeyboardInterrupt:
            print()
            print(
                "[INFO] Stopped by user."
            )

        finally:
            stats = self.capture.stats()

            print()
            print(
                "[CAPTURE STATS]",
                stats,
            )

            self.close()


# ======================================================================
# CLI
# ======================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "PEva / Last Z integrated runtime."
        )
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--preview",
        action="store_true",
        default=None,
        help=(
            "Generate replies but never navigate/send. "
            "This is the default mode."
        ),
    )

    mode.add_argument(
        "--auto-send",
        action="store_true",
        default=None,
        help=(
            "Generate and send UID-verified private replies."
        ),
    )

    mode.add_argument(
        "--preflight",
        action="store_true",
        default=False,
        help=(
            "Only verify modules, ADB, SQLite and llama-server."
        ),
    )

    parser.add_argument(
        "--player-loop",
        action="store_true",
        default=None,
        help=(
            "Run the Stage 1 event-driven PEva v2 loop. "
            "Without --auto-send it is preview-only."
        ),
    )

    parser.add_argument(
        "--record-player-loop",
        action="store_true",
        default=None,
        help="Append player-loop events, decisions and actions to data/player-loop/.",
    )

    parser.add_argument(
        "--screen-poll-interval",
        type=float,
        default=None,
        help="Seconds between deterministic screen-state polls in --player-loop mode.",
    )

    parser.add_argument(
        "--social-debug",
        action="store_true",
        default=None,
        help="Show detailed Stage 2 [INTENT] and [SOCIAL] decisions in --player-loop mode.",
    )

    parser.add_argument(
        "--private-only",
        action="store_true",
        default=None,
        help="In --player-loop mode capture only verified private (custom) messages.",
    )

    logcat_group = parser.add_mutually_exclusive_group()

    logcat_group.add_argument(
        "--clear-logcat",
        dest="clear_logcat",
        action="store_true",
        help=(
            "Clear Android logcat before capture starts (default)."
        ),
    )

    logcat_group.add_argument(
        "--keep-logcat",
        dest="clear_logcat",
        action="store_false",
        help=(
            "Debug only: keep the existing Android logcat buffer."
        ),
    )

    parser.set_defaults(
        clear_logcat=None
    )

    parser.add_argument(
        "--max-messages",
        type=int,
        default=0,
        help=(
            "Stop after N captured messages. "
            "0 means unlimited."
        ),
    )

    parser.add_argument(
        "--navigation-timeout",
        type=float,
        default=None,
        help=(
            "Maximum seconds allowed to reach PRIVATE_CHAT."
        ),
    )

    parser.add_argument(
        "--profile",
        help="Load config/profiles/<name>.json after config/settings.json.",
    )
    parser.add_argument(
        "--settings-file",
        help="Alternative settings JSON path (resolved relative to the project root).",
    )
    parser.add_argument(
        "--show-settings",
        action="store_true",
        help="Print the effective safe configuration and exit.",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Open the terminal settings editor.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.player_loop and args.preflight:
        parser.error("--player-loop cannot be combined with --preflight")

    cli_overrides = _settings_cli_overrides(args)
    try:
        settings = load_settings(
            settings_path=args.settings_file,
            profile=args.profile,
            cli_overrides=cli_overrides,
        )
        registry = load_model_registry(project_root=settings.project_root)
    except SettingsError as exc:
        parser.error(str(exc))

    if args.show_settings:
        print(render_effective_settings(settings, registry))
        return
    if args.settings:
        run_settings_menu(settings, registry)
        return

    try:
        chat_model = resolve_model_for_role(settings, registry, role="chat")
    except SettingsError as exc:
        parser.error(str(exc))

    runtime = settings.runtime
    player_loop_enabled = bool(runtime.player_loop or runtime.auto_send)
    if runtime.private_only and not player_loop_enabled:
        parser.error("private_only requires runtime.player_loop=true")

    config = MainConfig(
        auto_send=runtime.auto_send,
        clear_logcat=runtime.clear_logcat,
        max_messages=max(
            0,
            int(args.max_messages),
        ),
        navigation_timeout=max(
            3.0,
            float(args.navigation_timeout if args.navigation_timeout is not None else 12.0),
        ),
    )

    app = LastZApplication(
        config,
        settings=settings,
        chat_model=chat_model,
    )

    if args.preflight:
        try:
            ok = app.preflight(
                require_ai=True,
                require_adb=True,
            )
        finally:
            app.close()

        raise SystemExit(
            0 if ok else 1
        )

    # Auto-send is never allowed through the old one-message flow.  The
    # event-driven PlayerLoop is the single production navigator because it
    # persists the parent goal and re-observes after every navigation action.
    if player_loop_enabled:
        try:
            if settings.llm.auto_start_server:
                app.server_manager = start_configured_server(settings, chat_model)
                print(f"[LLAMA] managed={str(bool(app.server_manager and app.server_manager.managed)).lower()}")
            if not app.preflight(
                require_ai=True,
                require_adb=True,
            ):
                raise SystemExit(1)

            # One existing capture/listener handles both Stage 2 channels;
            # Stage 2.1 can restrict that same listener to PMs only.
            app.capture.config.allowed_groups = (
                {"custom"} if runtime.private_only else {"custom", "alliance"}
            )
            recorder = _settings_recorder(settings) if runtime.record_player_loop else None
            loop = PlayerLoop(
                capture=app.capture,
                agent=app.agent,
                executor=app.executor,
                screen_provider=app.read_game_state,
                navigation_manager=NavigationManager(
                    ui_effect_timeouts={
                        "open_chat": settings.navigation.ui_effect_timeout_ms / 1000,
                        "open_private_chat": settings.navigation.ui_effect_timeout_ms / 1000,
                        "recover_back": settings.navigation.ui_effect_timeout_ms / 1000,
                        "prepare_private_reply_target": settings.navigation.ui_effect_timeout_ms / 1000,
                    },
                    min_private_chat_samples=settings.navigation.private_chat_stable_samples,
                ),
                recorder=recorder,
                logger=_runtime_logger(settings),
                config=PlayerLoopConfig(
                    auto_send=runtime.auto_send,
                    clear_logcat=config.clear_logcat,
                    record_events=runtime.record_player_loop,
                    screen_poll_interval=max(
                        0.1,
                        float(args.screen_poll_interval if args.screen_poll_interval is not None else 2.0),
                    ),
                    ui_effect_poll_interval=settings.navigation.ui_effect_poll_ms / 1000,
                    social_debug=runtime.social_debug,
                    humanization_enabled=settings.humanization.enabled,
                    naturalness_rewrite=settings.humanization.naturalness_rewrite,
                    response_delay=settings.humanization.response_delay,
                    relationship_adaptation=settings.humanization.relationship_adaptation,
                    memory_adaptation=settings.humanization.memory_adaptation,
                    proactive_enabled=settings.proactive.enabled,
                    proactive_auto_send=settings.proactive.auto_send,
                    proactive_global_hourly_limit=settings.proactive.global_hourly_limit,
                    proactive_same_player_min_minutes=settings.proactive.same_player_min_minutes,
                    proactive_same_topic_min_minutes=settings.proactive.same_topic_min_minutes,
                    agency_enabled=settings.agency.enabled,
                    agency_social_preview_fallback=settings.agency.social_preview_fallback,
                ),
            )
            loop.set_runtime_availability(
                adb_connected=adb_controll.check_connection(),
                ai_available=app.ai.health_check(),
            )
            if runtime.auto_send and not args.player_loop:
                print("[RUNTIME] Auto-send uses the persistent PlayerLoop navigator.")
            print(f"[SETTINGS] profile={settings.profile}")
            print(
                "[RUNTIME] "
                f"player_loop={str(player_loop_enabled).lower()} "
                f"private_only={str(runtime.private_only).lower()} "
                f"auto_send={str(runtime.auto_send).lower()}"
            )
            print(f"[MODEL] chat={chat_model.model_id or chat_model.name}")
            print(f"[LLM] endpoint={settings.llm.endpoint}")
            print(f"[LLAMA] managed={str(bool(app.server_manager and app.server_manager.managed)).lower()}")
            print(
                "[PLAYER LOOP] Mode:",
                "AUTO SEND" if runtime.auto_send else "PREVIEW",
            )
            print(
                "[PLAYER LOOP] Capture groups:",
                ", ".join(sorted(app.capture.config.allowed_groups)),
            )
            loop.run()
        finally:
            app.close()
        return

    # A profile can deliberately disable PlayerLoop; retain legacy preview.
    app.run()


def _settings_cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    runtime: dict[str, bool] = {}
    if args.player_loop is True:
        runtime["player_loop"] = True
    if args.private_only is True:
        runtime["private_only"] = True
    if args.auto_send is True:
        runtime["auto_send"] = True
    if args.preview is True:
        runtime["auto_send"] = False
    if args.record_player_loop is True:
        runtime["record_player_loop"] = True
    if args.social_debug is True:
        runtime["social_debug"] = True
    if args.clear_logcat is not None:
        runtime["clear_logcat"] = bool(args.clear_logcat)
    return {"runtime": runtime} if runtime else {}


def _runtime_logger(settings: PevaSettings):
    message_prefixes = ("[INCOMING]", "[AI REPLY]", "[AI ORIGINAL]", "[AI FINAL]", "[SEND ", "[PROACTIVE CANDIDATE]")
    navigation_prefixes = ("[NAV ", "[UI EFFECT", "[STABILITY]", "[REPLAN]", "[RECOVERY]")

    def logger(line: str) -> None:
        if line.startswith(message_prefixes) and not settings.logging.message_content:
            return
        if line.startswith("[OBSERVE]") and not settings.logging.observations:
            return
        if line.startswith(navigation_prefixes) and not settings.logging.navigation:
            return
        print(line)

    return logger


def _settings_recorder(settings: PevaSettings) -> PlayerLoopRecorder:
    session_root = DATA_DIR / "player-loop" / f"session-{int(time.time() * 1000)}"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "settings.json").write_text(
        render_effective_settings(settings),
        encoding="utf-8",
    )
    return PlayerLoopRecorder(session_root)


if __name__ == "__main__":
    main()
