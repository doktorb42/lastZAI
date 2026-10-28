# Raport uporządkowania projektu

Data reorganizacji: 2026-10-18

## Stan przed i po

| Miara | Przed | Po |
|---|---:|---:|
| Pliki rekurencyjnie | 130 194 | 130 251 |
| Rozmiar rekurencyjny | 105 398 657 813 B (98,16 GiB) | 109 176 387 189 B (101,68 GiB) |
| Pliki w katalogu głównym | 155 | 17 |
| Katalogi w katalogu głównym | — | 12 |

Wzrost rozmiaru wynika wyłącznie z nowego, pomyślnie zweryfikowanego buildu
`patch\builds\build_20261018_004319`, utworzonego przez końcowy updater.
Nie usunięto artefaktów użytkownika.

Pełny zapis stanu sprzed przeniesień jest w
[`FILE_INVENTORY.md`](FILE_INVENTORY.md): 130 194 plików, z pełnymi ścieżkami,
rozmiarami, rozszerzeniami i datami. Zawiera 11 grup duplikatów SHA-256 w
zakresie aktywnego rozwiązania. Checksumy modeli/cache'u zostały celowo
pominięte, ale ich wpisy pozostały w pełnym wykazie.

## Wykonane przeniesienia

Wykonano 169 bezpiecznych operacji przeniesienia w obrębie tego samego dysku:
166 zaplanowanych przeniesień, zrzut końcowego ekranu, przejściową bazę testu
agenta oraz odtworzony cache Pythona po testach.

- `apk_scan/` → `apk/current/` — trzy bieżące wejścia updatera.
- `lastz_chat_patch_builds/` → `patch/current/` — zachowane wcześniejsze
  zweryfikowane buildy.
- Skrypty końcowego patcha → `tools/patching/`; audyt → `tools/audit/`.
- `conversation.db` → `data/databases/conversation.db`; dane JSONL →
  `data/captures/`; log i backup urządzenia → odpowiednio `data/logs/` oraz
  `data/device-backups/`.
- Prompty i template'y UI → `config/prompts/` i `ui/templates/`.
- Zrzuty dowodowe → `docs/evidence/`; cache Pythona/Unsloth → `cache/`.
- 135 eksperymentów, canary, starych APK, wariantów `gameres`, skryptów
  inspekcyjnych i plików niepewnych →
  `archive/2026-10-18-project-cleanup/`.

Duplikaty `base.apk` i `split_install_time_pack.apk` z katalogu głównego
zostały przeniesione do `archive/2026-10-18-project-cleanup/duplicates/`.
Aktywne kopie pozostają wyłącznie w `apk/current/`.

`lastz_model_benchmark/` pozostał w katalogu głównym celowo: jest aktywnym
obszarem modelu/treningu używanym przez `lastz_ai.py`, a nie cachem ani
eksperymentem do usunięcia.

## Aktywne pliki produkcyjne

W katalogu głównym pozostało tylko 17 plików: końcowy updater,
`README.md`, dwa entrypointy serwera Llama oraz kompatybilne moduły runtime'u
`main`, `agent`, `chat_capture`, `adb_controll`, `action_ex`,
`conversation_store`, `memory_engine`, `lastz_ai`, `prompt_manager`,
`runtime_config`, `screenDetector`, `coordinates` i `game_state`.

Pozostałe aktywne części to `apk/current/`, `patch/current/`, `patch/builds/`,
`patch/reference-assets/`, `tools/patching/`, `config/prompts/`,
`ui/templates/`, `data/` oraz `docs/`. Nie są to kopie z archiwum.

## Archiwum eksperymentów

`archive/2026-10-18-project-cleanup/` zawiera osobno `canary-artifacts`,
`superseded-apk-builds`, `patch-workspace`, `legacy`, `legacy-apks`, `logs`,
`test-databases`, `duplicates` i `unknown`. Pod
`reverse-engineering/` zachowano m.in. `chatbase_*`, `chatnet_*`,
`chatmanager2_project`, wszystkie `l_slg_*`, `runtime_canary_*`,
`slgengine_*_inspect`, dry-runy updatera, warianty `gameres`, skrypty guardów
i zrzuty route-canary. To zachowane dane historyczne, nie część uruchamianego
patcha.

## Zmiany zgodności

- `apply_lastz_chat_capture_patch.ps1` rozwiązuje ścieżki względem własnego
  katalogu i używa `apk/current`, `tools/patching` oraz `patch/builds`.
- Runtime odczytuje prompty, template'y, screenshoty i SQLite z nowych
  katalogów. Zmienna `LASTZ_CONVERSATION_DB` pozwala nadpisać ścieżkę bazy.
- Test agenta zapisuje wyłącznie `data/test-artifacts/agent_self_test.db`.
- `CHAT_BUTTON` poprawiono na zweryfikowaną współrzędną `(350, 1510)` po
  aktualnym teście emulatora.
- Historyczny opis końcowego rozwiązania został przeniesiony do
  [`FINAL_WORKING_SOLUTION.txt`](FINAL_WORKING_SOLUTION.txt) i zaktualizowany
  do nowych ścieżek. Stary raport zachowano w `docs/archive/`.

## Weryfikacja

Pomyślnie wykonano po reorganizacji:

- `py -m py_compile` dla runtime'u i skryptów patcha;
- `py chat_capture.py --self-test` oraz `py adb_controll.py --self-test`;
- `py agent.py --self-test`, z bazą testową poza katalogiem głównym;
- odczyt aktywnej SQLite, promptów, template'ów oraz zachowanego JSONL
  `REMOTE_CODEX_FINAL_901` / `seq_id=310`;
- końcowy updater z `-Install`; nowy podpisany pack ma SHA-256
  `d8ad77f8f3b5d13984aa19394a79c84701f3bde85964105dfcee4e7281b84576`,
  identyczny z packiem odczytanym z urządzenia;
- uruchomienie gry do `HQ_BASE`, następnie `ALLIANCE_CHAT`, bez wysyłania
  wiadomości;
- skan 1200 ostatnich wpisów logcat bez fatalnych wyjątków AndroidRuntime,
  Lua/XLua ani AssetBundle.

## Zwykłe użycie

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\projekty\lastZAI\apply_lastz_chat_capture_patch.ps1" -Install
```

Po aktualizacji sklepowej podmień trzy źródłowe pliki w `apk/current/`; dla
pierwszej instalacji po zmianie podpisu użyj dodatkowo `-Reinstall`.

## Usunięcia

Brak celowego usuwania danych użytkownika. Narzędzia testowe mogą zastąpić
wyłącznie własny, regenerowany plik `data/test-artifacts/agent_self_test.db`.
