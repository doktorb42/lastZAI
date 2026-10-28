"""PEva: one-click chat-capture patch verification and REAL message test. No AI.

One canonical patch engine: update_patch.py. Never deletes/reinstalls the game.
Only an actual incoming chat callback with text can earn PASS.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archive.legacy_runtime.chat_capture import ChatCapture, ChatCaptureConfig, DEFAULT_ADB_PATH, DEFAULT_DEVICE

# This file is archived below the project root.  Keep all patch inputs,
# reports and the compatibility updater anchored at the real project root.
ROOT = Path(__file__).resolve().parents[2]
UPDATER = ROOT / 'update_patch.py'
LOG_DIR = ROOT / 'data' / 'logs'
REPORT_PATH = LOG_DIR / 'peva_chat_test.json'
PATCH_LOG = LOG_DIR / 'peva_patch.log'
PRE_STATUS_LOG = LOG_DIR / 'peva_prestart_status.log'
POST_STATUS_LOG = LOG_DIR / 'peva_poststart_status.log'
PACKAGE = 'com.readygo.barrel.gp'
TEST_DURATION = 120
START_SETTLE_SECONDS = 25


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def cmd(*args: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(map(str, args)), capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout,
                              check=False, cwd=str(ROOT))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Proces {args[0]} nie zakonczyl sie poprawnie: {exc}') from exc


def write_log(path: Path, result: subprocess.CompletedProcess[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (result.stdout or '') + (('\n[stderr]\n' + result.stderr) if result.stderr else '')
    path.write_text(data, encoding='utf-8')
    return data


def find_adb() -> str:
    options = (os.getenv('PEVA_ADB', ''), DEFAULT_ADB_PATH,
               shutil.which('HD-Adb.exe') or '', shutil.which('adb') or '')
    for option in options:
        if option and (Path(option).is_file() or shutil.which(option)):
            return option
    raise RuntimeError('Nie znaleziono HD-Adb.exe. Uruchom BlueStacks.')


def find_device(adb: str) -> tuple[str, int]:
    r = cmd(adb, 'devices', '-l', timeout=15)
    online = [s[0] for line in r.stdout.splitlines() if len(s := line.split()) > 1 and s[1] == 'device']
    if not online:
        cmd(adb, 'connect', '127.0.0.1:5555', timeout=10)
        r = cmd(adb, 'devices', '-l', timeout=15)
        online = [s[0] for line in r.stdout.splitlines() if len(s := line.split()) > 1 and s[1] == 'device']
    chosen = os.getenv('PEVA_DEVICE', '').strip()
    if chosen:
        if chosen not in online:
            raise RuntimeError(f'PEVA_DEVICE={chosen} nie jest dostepne: {online}')
        online = [chosen]
    games = []
    for serial in online:
        dump = cmd(adb, '-s', serial, 'shell', 'dumpsys', 'package', PACKAGE, timeout=25)
        match = re.search(r'\bversionCode=(\d+)', dump.stdout or '')
        if dump.returncode == 0 and match:
            games.append((serial, int(match.group(1))))
    if not games:
        raise RuntimeError('Nie znaleziono Last Z na podlaczonym BlueStacks.')
    if len(games) == 1:
        return games[0]
    for favorite in (DEFAULT_DEVICE, '127.0.0.1:5555'):
        for game in games:
            if game[0] == favorite:
                return game
    raise RuntimeError('Wiele urzadzen Last Z: ustaw PEVA_DEVICE')


def patch_command(action: str, adb: str, device: str, log: Path) -> tuple[dict[str, Any] | None, str]:
    if not UPDATER.is_file():
        raise RuntimeError(f'Brakuje pliku silnika patcha: {UPDATER}')
    workspace = ROOT / 'data' / 'device-backups' / 'capture'
    workspace.mkdir(parents=True, exist_ok=True)
    result = cmd(sys.executable, '-B', str(UPDATER), action,
                 '--adb', adb, '--device', device, '--workspace', str(workspace), timeout=240)
    output = write_log(log, result)
    if result.returncode != 0:
        raise RuntimeError(f'{action}: blad silnika patcha ({result.returncode}). '
                           f'Zobacz {log}. Fragment: {output[-700:]}')
    summary = None
    for line in output.splitlines():
        if line.startswith('PEVA_STATUS_JSON='):
            try:
                summary = json.loads(line.split('=', 1)[1])
            except json.JSONDecodeError:
                raise RuntimeError('Status patcha zawiera niepoprawny JSON.')
    if action == 'status' and (not isinstance(summary, dict) or
                               'patch_present_in_asset_bundle' not in summary):
        raise RuntimeError(f'Stary lub niekompletny update_patch.py. Zobacz {log}.')
    return summary, output


def start_game(adb: str, serial: str) -> None:
    # Reload Lua from disk. The app is not uninstalled and its data is not touched.
    halted = cmd(adb, '-s', serial, 'shell', 'am', 'force-stop', PACKAGE, timeout=20)
    if halted.returncode != 0:
        raise RuntimeError('Nie udalo sie zatrzymac Last Z przed testem.')
    launch = cmd(adb, '-s', serial, 'shell', 'monkey', '-p', PACKAGE,
                 '-c', 'android.intent.category.LAUNCHER', '1', timeout=30)
    if launch.returncode or 'No activities found' in launch.stdout or 'aborted' in launch.stdout.lower():
        raise RuntimeError(f'Nie udalo sie uruchomic Last Z: {launch.stdout} {launch.stderr}')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        process = cmd(adb, '-s', serial, 'shell', 'pidof', PACKAGE, timeout=8)
        if process.stdout.strip():
            return
        time.sleep(1)
    raise RuntimeError('Gra Last Z nie uruchomila procesu w emulatorze.')


def changes(pre: dict[str, Any], post: dict[str, Any]) -> list[str]:
    diff = []
    a = pre.get('locations', {})
    b = post.get('locations', {})
    for key in sorted(set(a) | set(b)):
        before = (a.get(key) or {}).get('sha256')
        after = (b.get(key) or {}).get('sha256')
        if before != after:
            diff.append(f'{key}: {str(before)[:12]} -> {str(after)[:12]}')
    return diff


def listen(adb: str, device: str, report: dict[str, Any]) -> None:
    c = ChatCapture(ChatCaptureConfig(adb_path=adb, device=device,
                                     allowed_groups=set(), incoming_only=False))
    try:
        c.start(clear_logcat=True)
        print('[TEST] Logcat aktywny. Wyslij NOWA wiadomosc z drugiego konta Last Z.')
        print('[TEST] Testujemy prawdziwy callback czatu, nie sam komunikat PATCH_APPLIED.')
        deadline = time.monotonic() + TEST_DURATION
        next_log = time.monotonic() + 10
        while time.monotonic() < deadline:
            if c.last_error:
                raise RuntimeError(c.last_error)
            m = c.get_message(timeout=0.4)
            if m:
                complete = bool(m.room_id and m.text.strip() and (m.sender_name or m.sender_uid))
                # Include a bounded proof sample; may contain private chat text.
                item = {'timestamp': utc_now(), 'channel': m.group, 'room_id': m.room_id,
                        'seq_id': m.seq_id, 'sender_name': m.sender_name,
                        'sender_uid': m.sender_uid, 'text': m.text,
                        'complete': complete}
                report['messages'].append(item)
                print(f'[NOWY CZAT] {m.group} | {m.sender_name or m.sender_uid or "?"} | '
                      f'id={m.seq_id} | {m.text}', flush=True)
                if complete:
                    report['status'] = 'PASS'
                    report['detail'] = 'Zarejestrowano rzeczywista nowa wiadomosc callbacku czatu z trescia.'
                    break
            if not c.is_running:
                raise RuntimeError('Proces adb logcat samoczynnie zakonczyl sie.')
            if time.monotonic() >= next_log:
                stats = c.stats()
                print('[ZYCIE] logcat={total_lines}, Lua={total_lua_mentions}, '
                      'callback={total_callbacks}, push={total_chat_pushes}, '
                      'odebrane={total_emitted}'.format(**stats), flush=True)
                next_log += 10
        if report['status'] != 'PASS':
            stats = c.stats()
            if stats['total_push_mentions']:
                report['status'] = 'PARSE_PROBLEM'
                report['detail'] = 'Logcat zawiera znacznik callbacku czatu, ale brak poprawnej wiadomosci.'
            elif stats['total_callbacks']:
                report['status'] = 'NO_CHAT_PUSH'
                report['detail'] = 'Callbacki Lua sa aktywne, ale nie ma callbacku czatu.'
            elif stats['total_lua_mentions']:
                report['status'] = 'NO_CALLBACK'
                report['detail'] = 'Gra drukuje Lua, ale nie ma zadnych callbackow.'
            else:
                report['status'] = 'NO_LUA_EVENTS'
                report['detail'] = 'Nie ma nowych callbackow Lua w logcat.'
    finally:
        report['stats'] = c.stats()
        report['lua_samples'] = list(c.lua_samples)
        report['unparsed_samples'] = list(c.diagnostic_lines)
        c.stop()


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(errors='backslashreplace')
        except (OSError, ValueError):
            pass
    report: dict[str, Any] = {'started_utc': utc_now(), 'status': 'ERROR',
                              'detail': '', 'messages': []}
    print('=' * 65)
    print('PEva | JEDEN PATCH ENGINE + TEST PRAWDZIWYCH WIADOMOSCI | ZERO AI')
    print('=' * 65)
    try:
        adb = find_adb()
        device, version = find_device(adb)
        report.update(device=device, game_version=version)
        print(f'[ADB] {device} | Last Z versionCode={version}')
        before, _ = patch_command('status', adb, device, PRE_STATUS_LOG)
        assert before is not None
        report['before'] = before
        if not before['patch_present_in_asset_bundle']:
            print('[PATCH] Aktywny ChatNetManager nie ma sygnatury. Naprawiam jednym silnikiem...')
            patch_command('apply', adb, device, PATCH_LOG)
            before, _ = patch_command('status', adb, device, PRE_STATUS_LOG)
            assert before is not None
            report['before_after_apply'] = before
            if not before['patch_present_in_asset_bundle']:
                raise RuntimeError('Patch nie jest widoczny bezposrednio po instalacji!')
            print('[PATCH] Zapis i zgodnosc zasobu potwierdzone przed startem.')
        else:
            print('[PATCH] Sygnatura patcha juz obecna na dysku.')
        print('[GRA] Restart gry w celu zaladowania zasobow...')
        start_game(adb, device)
        print('[GRA] Czekam na zakonczenie poczatkowego odswiezania zasobow...')
        time.sleep(START_SETTLE_SECONDS)
        post, _ = patch_command('status', adb, device, POST_STATUS_LOG)
        assert post is not None
        report['after_start'] = post
        diff = changes(before, post)
        report['changed_locations_after_start'] = diff
        for row in diff:
            print('[ZMIANA ZASOBU] ' + row)
        if not post['patch_present_in_asset_bundle']:
            report['status'] = 'PATCH_LOST_AFTER_START'
            report['detail'] = ('Po uruchomieniu gry aktywny ChatNetManager nie zawiera patcha. '
                                'Nie ponawiam automatycznej instalacji w petli.')
            print('[STOP] Patch zniknal lub gra przelaczyla zasob po starcie. ' 
                  'Sprawdz peva_poststart_status.log.')
        else:
            print('[PATCH] Jest na dysku takze po starcie. To wciaz nie dowodzi wykonania Lua.')
            report['status'] = 'WAITING_FOR_CHAT'
            listen(adb, device, report)
    except (RuntimeError, OSError, KeyboardInterrupt) as exc:
        report['status'] = 'INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'ERROR'
        report['detail'] = str(exc)
        print('[BLAD] ' + str(exc))
    finally:
        report['finished_utc'] = utc_now()
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'[{report["status"]}] {report.get("detail", "")}')
        print('[RAPORT] ' + str(REPORT_PATH))
        print('[STATUS PRZED] ' + str(PRE_STATUS_LOG))
        print('[STATUS PO] ' + str(POST_STATUS_LOG))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
