from __future__ import annotations

import argparse
import json
import re
import queue
import subprocess
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from config.runtime_config import is_direct_private_room


DEFAULT_ADB_PATH = r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"
DEFAULT_DEVICE = "emulator-5554"

# UID konta PEva / konta obsługiwanego przez AI.
DEFAULT_SELF_UID = "1224463480000772"

# Last Z has used both names for the same incoming chat payload.  Keeping this
# small allow-list makes capture survive that server-side rename without
# accepting unrelated Unity callbacks.
CHAT_PUSH_COMMANDS = frozenset({"push.chat.room", "push.chat.up.room"})


@dataclass(slots=True)
class ChatMessage:
    """
    Jedna wiadomość przechwycona bezpośrednio z callbacku czatu Last Z.

    Ten obiekt będzie później przekazywany do:
        conversation_store.py
        -> lastz_ai.py
        -> agent.py

    Nie zawiera żadnej logiki AI ani klikania po grze.
    """

    room_id: str
    group: str

    sender_uid: str
    sender_name: str | None

    text: str
    seq_id: int | None

    server_time: int | None = None
    send_time: int | None = None

    sender_alliance: str | None = None
    sender_server_id: int | None = None

    message_type: int | None = None
    post_type: int | None = None
    original_lang: str | None = None

    pushseq: str | None = None

    # Całe "data" z callbacku czatu.
    # Przyda się później do obsługi attachmentów, lokacji, raportów itd.
    raw_data: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def source_message_key(self) -> str:
        """
        Stabilny identyfikator wiadomości.

        roomId + seqId jest wystarczające dla zwykłych wiadomości chatowych.
        """
        return f"{self.room_id}:{self.seq_id}"

    @property
    def is_private(self) -> bool:
        return self.group == "custom"

    @property
    def is_alliance(self) -> bool:
        return self.group == "alliance"

    @property
    def is_country(self) -> bool:
        return self.group == "country"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_message_key"] = self.source_message_key
        return data


@dataclass(slots=True)
class ChatCaptureConfig:
    adb_path: str = DEFAULT_ADB_PATH
    device: str = DEFAULT_DEVICE
    self_uid: str = DEFAULT_SELF_UID

    # Domyślnie interesują nas prywatne rozmowy.
    allowed_groups: set[str] = field(
        default_factory=lambda: {"custom"}
    )

    # Nie przekazuj wiadomości wysłanych przez samo konto AI.
    incoming_only: bool = True

    # Rozmiar pamięci deduplikacji.
    dedupe_limit: int = 5000


class ChatCapture:
    """
    Live reader dla zmodyfikowanego logcat Last Z.

    Patch gry drukuje callback JSON, którego `cmd` może być jednym z:
        [LUA][callback] ... {"cmd":"push.chat.room","data":{...}}
        [LUA][callback] ... {"cmd":"push.chat.up.room","data":{...}}

    ChatCapture:
      - uruchamia adb logcat,
      - wyciąga JSON,
      - bierze tylko znane callbacki czatu,
      - filtruje kanały,
      - ignoruje własne wiadomości,
      - deduplikuje po roomId + seqId,
      - wkłada ChatMessage do thread-safe queue.

    Przykład przyszłej integracji:

        capture = ChatCapture()
        capture.start(clear_logcat=True)

        while True:
            message = capture.get_message(timeout=1.0)
            if message is None:
                continue

            print(message.sender_name, message.text)
    """

    def __init__(
        self,
        config: ChatCaptureConfig | None = None,
    ):
        self.config = config or ChatCaptureConfig()

        self._messages: queue.Queue[ChatMessage] = queue.Queue()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None

        self._seen_order: deque[str] = deque()
        self._seen: set[str] = set()

        self._lock = threading.Lock()

        self.total_lines = 0
        self.total_callbacks = 0
        self.total_chat_pushes = 0
        self.total_emitted = 0
        self.total_duplicates = 0
        self.total_filtered = 0
        self.total_parse_errors = 0
        # Indicators showing where a missing chat event disappears.
        self.total_lua_mentions = 0
        self.total_websocket_mentions = 0
        self.lua_samples: deque[str] = deque(maxlen=16)
        self.total_push_mentions = 0
        self.total_unity_lines = 0
        self.total_json_candidates = 0
        self.other_commands: dict[str, int] = {}
        self.diagnostic_lines: deque[str] = deque(maxlen=40)

        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return bool(
            self._thread
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    def check_connection(self) -> bool:
        result = subprocess.run(
            [
                self.config.adb_path,
                "-s",
                self.config.device,
                "get-state",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )

        return (
            result.returncode == 0
            and result.stdout.strip() == "device"
        )

    def clear_logcat(self) -> bool:
        result = subprocess.run(
            [
                self.config.adb_path,
                "-s",
                self.config.device,
                "logcat",
                "-c",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )

        return result.returncode == 0

    def start(
        self,
        *,
        clear_logcat: bool = False,
    ) -> None:
        """
        Startuje przechwytywanie w osobnym wątku.
        """

        if self.is_running:
            return

        if not self.check_connection():
            raise RuntimeError(
                f"ADB device is not connected: {self.config.device}"
            )

        if clear_logcat and not self.clear_logcat():
            raise RuntimeError(
                "Nie udalo sie wyczyscic logcat. Bez tego nie mozna "
                "potwierdzic, ze odebrane wiadomosci sa NOWE."
            )

        self.last_error = None
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._reader_loop,
            name="LastZChatCapture",
            daemon=True,
        )

        self._thread.start()

    def stop(
        self,
        timeout: float = 3.0,
    ) -> None:
        """
        Zatrzymuje reader oraz proces adb logcat.
        """

        self._stop_event.set()

        process = self._process

        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

        thread = self._thread

        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=timeout)

        process = self._process

        if process is not None and process.poll() is None:
            try:
                process.kill()
            except Exception:
                pass

        self._process = None
        self._thread = None

    def get_message(
        self,
        timeout: float | None = None,
    ) -> ChatMessage | None:
        """
        Pobiera następną wiadomość.
        """

        try:
            if timeout is None:
                return self._messages.get()

            if timeout <= 0:
                return self._messages.get_nowait()

            return self._messages.get(
                timeout=timeout
            )

        except queue.Empty:
            return None

    def drain_messages(self) -> list[ChatMessage]:
        """
        Pobiera wszystkie wiadomości, które już czekają w kolejce.
        """

        result: list[ChatMessage] = []

        while True:
            message = self.get_message(timeout=0)

            if message is None:
                break

            result.append(message)

        return result

    def iter_messages(
        self,
        timeout: float = 0.5,
    ) -> Iterator[ChatMessage]:
        """
        Generator wiadomości działający do stop().
        """

        while not self._stop_event.is_set():
            message = self.get_message(
                timeout=timeout
            )

            if message is not None:
                yield message

    def stats(self) -> dict[str, Any]:
        return {
            "running": self.is_running,
            "queue_size": self._messages.qsize(),
            "total_lines": self.total_lines,
            "total_callbacks": self.total_callbacks,
            "total_chat_pushes": self.total_chat_pushes,
            "total_emitted": self.total_emitted,
            "total_duplicates": self.total_duplicates,
            "total_filtered": self.total_filtered,
            "total_parse_errors": self.total_parse_errors,
            "total_lua_mentions": self.total_lua_mentions,
            "total_websocket_mentions": self.total_websocket_mentions,
            "total_push_mentions": self.total_push_mentions,
            "total_unity_lines": self.total_unity_lines,
            "total_json_candidates": self.total_json_candidates,
            "other_commands": dict(self.other_commands),
            "last_error": self.last_error,
        }

    def parse_logcat_line(
        self,
        line: str,
    ) -> ChatMessage | None:
        """
        Parsuje pojedynczą linię logcat.

        Można używać także bez uruchamiania wątku, np. w testach.
        """

        if not isinstance(line, str):
            return None

        # Some Unity/Last Z builds prepend counters or spacing to the
        # callback marker. Keep a narrow fallback for explicit chat JSON.
        lower_line = line.lower()
        is_callback = bool(re.search(r"\[lua\]\s*\[callback\]", line, re.I))
        is_chat_push = any(command in lower_line for command in CHAT_PUSH_COMMANDS)
        if "[lua]" in lower_line:
            self.total_lua_mentions += 1
            if len(self.lua_samples) < self.lua_samples.maxlen:
                self.lua_samples.append(line.rstrip()[:500])
        if "websocket push" in lower_line:
            self.total_websocket_mentions += 1
        if is_chat_push:
            self.total_push_mentions += 1
        if "unity" in lower_line:
            self.total_unity_lines += 1
        if not (is_callback or is_chat_push):
            return None

        if is_callback:
            self.total_callbacks += 1

        # Do not count arbitrary stack traces mentioning a chat callback
        # as successfully parsed chat events.
        if not is_callback and not re.search(
            r'"cmd"\s*:\s*"push\.chat\.(?:up\.)?room"', line, re.I
        ):
            return None

        # Android/Unity may append text after a JSON object. raw_decode
        # extracts only the object, unlike json.loads on the whole suffix.
        decoder = json.JSONDecoder()
        payload = None
        marker_end = line.lower().find("[callback]")
        fragment = line[marker_end + len("[callback]"):] if is_callback and marker_end >= 0 else line
        for pos, char in enumerate(fragment):
            if char != "{":
                continue
            self.total_json_candidates += 1
            try:
                candidate, _ = decoder.raw_decode(fragment[pos:])
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(candidate, dict) and "cmd" in candidate:
                payload = candidate
                break
        if payload is None:
            self.total_parse_errors += 1
            self.diagnostic_lines.append(line.rstrip()[:2000])
            return None

        cmd = str(payload.get("cmd", ""))
        if cmd not in CHAT_PUSH_COMMANDS:
            cmd = cmd[:100]
            if cmd:
                self.other_commands[cmd] = self.other_commands.get(cmd, 0) + 1
            return None

        self.total_chat_pushes += 1

        data = payload.get("data")

        if not isinstance(data, dict):
            self.total_parse_errors += 1
            self.diagnostic_lines.append(line.rstrip()[:2000])
            return None

        group = _as_optional_str(
            data.get("group")
        ) or ""

        if (
            self.config.allowed_groups
            and group not in self.config.allowed_groups
        ):
            self.total_filtered += 1
            return None

        sender_uid = _as_optional_str(
            data.get("sender")
        ) or ""

        if (
            self.config.incoming_only
            and self.config.self_uid
            and sender_uid == str(self.config.self_uid)
        ):
            self.total_filtered += 1
            return None

        room_id = _as_optional_str(
            data.get("roomId")
        ) or ""

        seq_id = _as_optional_int(
            data.get("seqId")
        )

        if not room_id:
            self.total_parse_errors += 1
            self.diagnostic_lines.append(line.rstrip()[:2000])
            return None

        # A ``custom`` label alone is not proof of a direct private chat.
        # The room must be the observed custom_<uidA>_<uidB>_vN format and
        # name both this PEva account and the sending player.  This keeps
        # public/custom service rooms out of the PM conversation history.
        if (
            group == "custom"
            and self.config.self_uid
            and sender_uid
            and not is_direct_private_room(
                room_id,
                self_uid=str(self.config.self_uid),
                peer_uid=sender_uid,
            )
        ):
            self.total_filtered += 1
            return None

        sender_info = data.get(
            "senderInfo"
        )

        if not isinstance(
            sender_info,
            dict,
        ):
            sender_info = {}

        extra = data.get("extra")

        if not isinstance(
            extra,
            dict,
        ):
            extra = {}

        extra_param = extra.get(
            "extraParam"
        )

        if not isinstance(
            extra_param,
            dict,
        ):
            extra_param = {}

        text = data.get("msg")

        if text is None:
            text = ""

        if not isinstance(text, str):
            text = str(text)

        original_lang = (
            _as_optional_str(
                extra_param.get(
                    "originalLang"
                )
            )
            or _as_optional_str(
                data.get(
                    "originalLang"
                )
            )
        )

        return ChatMessage(
            room_id=room_id,
            group=group,

            sender_uid=sender_uid,
            sender_name=_as_optional_str(
                sender_info.get("userName")
            ),

            text=text,
            seq_id=seq_id,

            server_time=_as_optional_int(
                data.get("serverTime")
            ),
            send_time=_as_optional_int(
                data.get("sendTime")
            ),

            sender_alliance=_as_optional_str(
                sender_info.get("abbr")
            ),
            sender_server_id=_as_optional_int(
                sender_info.get("serverId")
            ),

            message_type=_as_optional_int(
                data.get("type")
            ),
            post_type=_as_optional_int(
                extra.get("post")
            ),
            original_lang=original_lang,

            pushseq=_as_optional_str(
                payload.get("pushseq")
            ),

            raw_data=data,
        )

    def _reader_loop(self) -> None:
        command = [
            self.config.adb_path,
            "-s",
            self.config.device,
            "logcat",
            "-v",
            "brief",
            # Test nie ogranicza sie do Unity:I. Różne wersje gry
            # mogą wypisywać ten sam callback pod innym tagiem/priorytetem.
            # Parsowanie odrzuca linie niebedace [LUA][callback].
            "*:V",
        ]

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            if self._process.stdout is None:
                raise RuntimeError(
                    "Cannot read adb logcat stdout"
                )

            for line in self._process.stdout:

                if self._stop_event.is_set():
                    break

                self.total_lines += 1

                message = self.parse_logcat_line(
                    line
                )

                if message is None:
                    continue

                if not self._remember_message(
                    message
                ):
                    self.total_duplicates += 1
                    continue

                self._messages.put(
                    message
                )

                self.total_emitted += 1

            # Samoczynne zakonczenie strumienia logcat nie oznacza,
            # ze nasluch dzialal do konca testu.
            if not self._stop_event.is_set():
                rc = self._process.wait(timeout=2) if self._process is not None else None
                self.last_error = f"Proces adb logcat zakonczyl sie przed czasem (exit={rc})."

        except Exception as exc:
            self.last_error = (
                f"{type(exc).__name__}: {exc}"
            )

        finally:
            process = self._process

            if (
                process is not None
                and process.poll() is None
            ):
                try:
                    process.terminate()
                except Exception:
                    pass

            self._process = None

    def _remember_message(
        self,
        message: ChatMessage,
    ) -> bool:
        """
        True  -> nowa wiadomość
        False -> duplikat
        """

        key = message.source_message_key

        if message.seq_id is None:
            key = (
                f"{message.room_id}:"
                f"{message.sender_uid}:"
                f"{message.server_time}:"
                f"{message.text}"
            )

        with self._lock:

            if key in self._seen:
                return False

            self._seen.add(key)
            self._seen_order.append(key)

            while (
                len(self._seen_order)
                > self.config.dedupe_limit
            ):
                old_key = (
                    self._seen_order.popleft()
                )

                self._seen.discard(
                    old_key
                )

        return True


def _as_optional_str(
    value: Any,
) -> str | None:

    if value is None:
        return None

    return str(value)


def _as_optional_int(
    value: Any,
) -> int | None:

    if value is None:
        return None

    try:
        return int(value)

    except (
        TypeError,
        ValueError,
    ):
        return None


def _parse_groups(
    value: str,
) -> set[str]:

    value = value.strip().lower()

    if value == "all":
        return {
            "custom",
            "alliance",
            "country",
        }

    return {
        item.strip()
        for item in value.split(",")
        if item.strip()
    }


def main() -> None:
    """
    Samodzielny test live capture.

    Prywatne wiadomości:
        py chat_capture.py --clear

    Wszystkie główne kanały:
        py chat_capture.py --groups all

    Zapis JSONL:
        py chat_capture.py --clear --jsonl captured_chat.jsonl
    """

    parser = argparse.ArgumentParser(
        description=(
            "Live Last Z chat capture "
            "from patched Unity logcat."
        )
    )

    parser.add_argument(
        "--adb",
        default=DEFAULT_ADB_PATH,
    )

    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
    )

    parser.add_argument(
        "--self-uid",
        default=DEFAULT_SELF_UID,
    )

    parser.add_argument(
        "--groups",
        default="custom",
        help=(
            "custom, alliance, country, "
            "comma-separated values, or all"
        ),
    )

    parser.add_argument(
        "--include-self",
        action="store_true",
    )

    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear logcat before starting.",
    )

    parser.add_argument(
        "--jsonl",
        default=None,
        help="Optional JSONL output file.",
    )

    args = parser.parse_args()

    config = ChatCaptureConfig(
        adb_path=args.adb,
        device=args.device,
        self_uid=args.self_uid,
        allowed_groups=_parse_groups(
            args.groups
        ),
        incoming_only=not args.include_self,
    )

    capture = ChatCapture(
        config
    )

    output_file = None

    if args.jsonl:
        path = Path(
            args.jsonl
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_file = path.open(
            "a",
            encoding="utf-8",
        )

    print("=" * 60)
    print("LAST Z CHAT CAPTURE")
    print("=" * 60)
    print(
        "Device:",
        config.device,
    )
    print(
        "Groups:",
        sorted(
            config.allowed_groups
        ),
    )
    print(
        "Incoming only:",
        config.incoming_only,
    )
    print(
        "Self UID:",
        config.self_uid,
    )
    print()
    print(
        "Waiting for messages... "
        "Ctrl+C to stop."
    )
    print()

    try:
        capture.start(
            clear_logcat=args.clear
        )

        for message in capture.iter_messages():

            rendered = json.dumps(
                message.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            )

            print(
                rendered,
                flush=True,
            )

            if output_file is not None:
                output_file.write(
                    rendered + "\n"
                )

                output_file.flush()

    except KeyboardInterrupt:
        print()
        print("[INFO] Stopping...")

    finally:
        capture.stop()

        if output_file is not None:
            output_file.close()

        print(
            "[STATS]",
            json.dumps(
                capture.stats(),
                ensure_ascii=False,
            ),
        )


if __name__ == "__main__":
    main()
