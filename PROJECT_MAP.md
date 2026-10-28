# Mapa projektu LastZAI

Ten plik jest krótkim przewodnikiem po projekcie.  Najpierw zacznij od
[`README.md`](README.md) — zawiera normalne komendy uruchamiania PEva.
Jeżeli szukasz konkretnego pliku w katalogu głównym, użyj też
[`docs/ROOT_SURFACE_AUDIT.md`](docs/ROOT_SURFACE_AUDIT.md).

## Najczęstsze rzeczy

| Chcę… | Gdzie szukać |
|---|---|
| Zobaczyć bieżący plan i stan przebudowy | `docs/PROGRESS.md`, `docs/ARCHITECTURE.md` |
| Sprawdzić co można bezpiecznie porządkować | `docs/AUDIT_2026-10-25.md` |
| Uruchomić PEva normalnie | `PEva.cmd` lub `main.py` |
| Zobaczyć starszy runtime lub ustawienia zgodności | `archive/legacy_runtime/`, `config/` |
| Zmienić ustawienia albo model runtime | `config/` |
| Zobaczyć bieżące dane, logi i zrzuty | `data/` |
| Znaleźć wyniki treningów Agency | `data/training/` |
| Przeczytać raporty i instrukcje | `docs/INDEX.md` |
| Zobaczyć stare wersje projektu | `archive/` oraz `docs/archive/` |

## Co jest w głównych folderach

- `config/` — aktywne ustawienia, wspólne ścieżki środowiska, ich walidacja, profile i prompty.
- `oczy/` — lekki odczyt ekranu, wąski adapter jednorazowego zrzutu PNG w
  pamięci, detekcja widoku/odznak, lokalny OCR i paczki prywatnego czatu.
- `mozg/` — klient modelu lokalnego, historia rozmów, pamięć długoterminowa,
  jej przegląd, analiza intencji, kontekst prywatnej rozmowy, decyzje/podglądy
  oraz rozpoznawanie i kolejka poleceń, a także bezpieczne poprawki deklaracji
  akcji.
- `charakter/` i `emocje/` — stały styl PEva, relacje, profile graczy,
  zatwierdzone wspomnienia, ograniczona inicjatywa społeczna, preferowana
  długość odpowiedzi, decyzja o podglądzie i potrzebie odpowiedzi społecznej
  oraz jej chwilowy stan runtime. Priorytet społeczny jest lokalny dla
  Charakteru i nie łączy tej części ze starym mechanizmem celów.
- `rece/` — globalny SAFE, tylko-odczytowe gesty Oczu oraz stan potwierdzenia
  dawnego transportu; fizyczne Ręce są dopiero przygotowywane.
- `trening/` — prosty widok modeli, benchmarków i danych oraz narzędzia
  świadomego przeglądu ocen odpowiedzi; nie uruchamia się przez `PEva.cmd`.
- `testy/` — automatyczne testy nowego rdzenia oraz świadomie uruchamiane,
  ręczne narzędzia diagnostyczne (np. `manual_ui_probe.py` i
  `manual_ocr_test.py`).
- `peva_runtime.py` — nowy rdzeń PEva w fazie SAFE.
- `peva_panel.py` — panel stanu tylko na tym komputerze.
- `peva_app.py` — zwykłe okno uruchamiające nowy rdzeń jednym kliknięciem.
- `data/` — dane lokalne projektu.  Najważniejsze podfoldery:
  - `databases/` — aktywne lokalne bazy PEva;
  - `captures/`, `screenshots/`, `logs/`, `player-loop/` — dowody i diagnostyka;
  - `training/` — dane, benchmarki, adaptery i raporty treningowe;
  - `exports/` — ręczne eksporty baz, które można łatwo skopiować;
  - `backups/` — kopie zapasowe i starsze paczki;
  - `legacy/` — zachowane stare dane pomocnicze, nie używane przez normalny runtime.
- `docs/` — aktualne raporty i przewodniki; użyj `docs/INDEX.md` zamiast
  przeglądać całą listę plików. `docs/archive/` zawiera dawne instrukcje
  capture/OCR i QLoRA, których nie potrzeba do normalnego uruchomienia.
- `archive/` — kompletne, historyczne migawki kodu. `legacy_runtime/` jest
  zachowaną zgodnością; bieżący start go nie importuje. Jest tam także dawny,
  tylko-odczytowy Capture logcat, dawny agent odpowiedzi, historyczny
  launcher patcha/Capture oraz sam silnik patchowania, plan prywatnej
  nawigacji, formatery logów, współrzędne starego ADB, wykonawca starych
  akcji, sam PlayerLoop, zdarzenia PlayerLoop, menu terminalowe, stan ekranu,
  kolejka celów i ręczne decyzje, zachowane zamiast usunięcia. Jest tam także
  planowanie decyzji i nawigacji starego PlayerLoop.
  Krótkie objaśnienie zawiera `archive/legacy_runtime/README.md`.
  Dawne nazwy z katalogu głównego są zachowane w
  `archive/legacy_root_bridges/`; aktywny kod importuje docelowe moduły
  bezpośrednio.
  `legacy_launchers/` zawiera dawne skróty diagnostyczne i ręczny start
  serwera oraz ograniczane czasowo skrypty WSL V2–V4, których nie należy
  używać do zwykłego uruchomienia.
  `legacy_training_scripts/` zachowuje nieużywane generatory danych V1;
  nie zawiera modeli ani nie zmienia aktywnego modelu.
  `snapshots/` przechowuje pojedyncze archiwa projektu, które nie są potrzebne
  przy codziennym uruchomieniu.
- `lastz_model_benchmark/` — osobny obszar eksperymentów/modeli; zostawiony
  w całości, aby nie zerwać jego własnych narzędzi.
- `cache/` — zachowane techniczne cache i ślady wcześniejszych narzędzi.
  Nie są ładowane przez zwykłą PEvę. Część jest potrzebna do odtworzenia
  historycznych eksperymentów treningowych, więc nie jest czyszczona razem z
  odtwarzalnym `__pycache__/` aktywnego kodu.
- `tools/` — ręczne narzędzia audytu i patchowania. Nie są drugim sposobem
  uruchamiania PEva ani elementem zwykłego startu.
- `apk/` i `patch/` — wejścia oraz wyniki patchowania APK.  Nie kasuj ich,
  jeśli chcesz zachować możliwość odtworzenia patcha.

## Dlaczego katalog główny jest mały?

W katalogu głównym zostają tylko normalny start PEva, jego okno, panel,
runtime i wspólny kontrakt. Właściwy kod znajduje się w folderach części
PEva, a wszystkie dawne nazwy Pythona są w `archive/legacy_root_bridges/`.
Właściwe źródła dawnego `PlayerLoop`, wykonawcy akcji i pełnego ADB są w
`archive/legacy_runtime/`. Oczy mają własny adapter PNG w
`oczy/android_capture.py`, a Ręce odczytu dwa ograniczone gesty w
`rece/read_only_adb.py`; żaden z nich nie importuje starego transportu.

## Czego nie usuwać ręcznie

- `config/`, `data/databases/`, `data/training/`, `apk/`, `patch/`;
- `.venv-agency/` — środowisko do treningu QLoRA w WSL;
- `lastz_model_benchmark/` — niezależne narzędzia i modele benchmarkowe.

Cache Pythona (`__pycache__/`) jest odtwarzalny i może być usuwany.  Stare
eksporty i raporty są archiwami: przenosimy je zamiast kasować.
