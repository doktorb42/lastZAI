# Zachowany stary runtime PEva

Ten katalog przechowuje kod poprzedniej pętli PEva. **Nie jest uruchamiany
przez `PEva.cmd` ani przez nowy `main.py`.** Zostaje tu po to, aby dało się
odtworzyć starsze testy i bezpiecznie porównać zachowanie podczas przebudowy,
a nie jako drugi sposób zwykłego uruchamiania aplikacji.

## Co tu jest

- `main_legacy.py` — poprzedni punkt startu;
- `chat_capture.py` — tylko-odczytowy parser logcat;
- `agent.py` — dawne przygotowanie podglądu odpowiedzi i kolejka oczekujących;
- `peva_start.py` — dawny launcher patcha i ręcznego testu Capture;
- `update_patch.py` — zachowany silnik ręcznego patchowania Capture;
- `coordinates.py` — stałe współrzędne używane wyłącznie przez stary transport;
- `action_ex.py` — zachowany wykonawca dawnych, fizycznych akcji;
- `player_loop.py` — zachowana duża pętla, która łączy dawny model z transportem;
- `adb_controll.py` — pełny, historyczny adapter ADB; razem z
  `PlayerLoop → ActionExecutor` może prowadzić do fizycznej akcji w grze.
  Nowe Oczy oraz Ręce używają własnych, minimalnych adapterów odczytu PNG i
  gestów, nie tego modułu;
- moduły planowania, stanu, logowania i menu dawnego `PlayerLoop`.

Dawne nazwy z katalogu głównego zostały przeniesione do
`archive/legacy_root_bridges/`. Aktywne testy i `main_legacy.py` importują
ten katalog bezpośrednio, więc zwykły katalog główny nie wymaga mostków
zgodności. Normalna PEva używa wyłącznie `main.py` oraz folderów `oczy/`,
`mozg/`, `charakter/`, `emocje/`, `rece/` i `config/`.

## Ważna granica

Stary łańcuch ma postać:

```text
Capture → PlayerLoop → ActionExecutor → ADB / BlueStacks / Last Z
```

Nowy runtime kończy się na podglądzie decyzji i `Action(NONE)`. Nie łączy się
z tym łańcuchem. Szczegóły znajdują się w
[`docs/LEGACY_ACTION_BOUNDARY.md`](../../docs/LEGACY_ACTION_BOUNDARY.md) oraz
[`docs/HANDS_AUDIT.md`](../../docs/HANDS_AUDIT.md).
