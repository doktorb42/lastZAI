# PEva — granica starego sterowania grą

Ten dokument oddziela nową, bezpieczną PEvę od zachowanego starego runtime.
Nie jest instrukcją włączania automatycznych akcji.

## Co może nowy rdzeń

`peva_runtime.py` łączy Oczy, Mózg, Charakter, Emocje oraz `SafetyGate`.
Końcowy wynik jest zawsze `Action(NONE)`. Nie importuje starego
`ActionExecutor`, `PlayerLoop` ani mechanizmu wpisywania/wysyłania tekstu.

Oczy mają tylko dwa ograniczone ruchy w `rece/read_only_navigation.py`:

- kliknięcie lewej części jednego wiersza PM, już potwierdzonego wizualnie;
- krótkie przewinięcie środkiem otwartego PM, wyłącznie dla odczytu uciętego
  bąbelka.

Żaden z tych gestów nie zna pola wpisywania, przycisku Send ani treści
odpowiedzi.

## Co pozostaje odłączonym starym runtime

| Moduł | Odpowiedzialność | Dlaczego nie jest podłączony |
|---|---|---|
| `archive/legacy_runtime/adb_controll.py` | ADB, tap, swipe, wpisywanie tekstu, Send, logcat i weryfikacja echo | Zawiera funkcje `input_text`, `write_message`, `send_message` oraz `send_private_reply_verified`; główna nazwa jest wyłącznie mostkiem zgodności. |
| `action_ex.py` | Planowanie i wykonanie odpowiedzi prywatnej | Wywołuje otwarcie PM, cofanie oraz wysyłanie przez stare ADB. |
| `player_loop.py` | Duża pętla starego agenta, pamięć, polityki i kolejki | Wiąże decyzję modelu z `ActionExecutor`; nie ma jeszcze małego, niezależnego kontraktu. |
| `navigation_manager.py` | Stary plan nawigacji | Ma osobny model stanu i zależności ze starym cyklem. |
| `decision_engine.py` | Stare decyzje i plany nawigacji/wysyłki | Nie wykonuje ich sam, lecz jego decyzje są przeznaczone dla `PlayerLoop`, więc nie jest częścią nowego bezpiecznego Mózgu. |
| `goal_manager.py`, `user_decision.py`, `world_state.py` | Stan i koordynacja starego cyklu | Są zależnościami dawnych planów, nie kontraktem nowego runtime. |

## Reguła kolejnego etapu

Nie przenosimy żadnego z tych plików w całości do `rece/`. Jeżeli kiedyś będzie
potrzebna pojedyncza funkcja, najpierw powstaje nowa mała funkcja z konkretnym
kontraktem, testem oraz trzema barierami:

1. potwierdzona tożsamość rozmówcy przez niezależne UID, nie sam nick;
2. globalny `SAFE` aktywny domyślnie i sprawdzany tuż przed ruchem;
3. użytkownik świadomie włącza daną akcję w teście.

Do czasu spełnienia tych warunków nowych elementów PEva nie wolno łączyć ze
starym wysyłaniem. Stare moduły są zachowane tylko jako materiał do audytu i
punkt powrotu.

Automatyczny test strukturalny `testy/test_new_runtime_boundaries.py` pilnuje,
aby normalny start, panel, Mózg, Charakter oraz Emocje nie importowały
`adb_controll`, starego transportu ani Treningu. Pilnuje również, aby
ograniczone Ręce odczytu nie uzyskały symboli wpisywania lub wysyłania
wiadomości.

Dotyczy to także krótkiego `private_chat_navigation.py` w katalogu głównym:
to mostek wymagany wyłącznie przez historyczny `main_legacy.py`, a nie część
nowego runtime PEva.
