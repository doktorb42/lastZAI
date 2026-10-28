# Manifest migracji PEva

Każdy wpis oznacza rzeczywiste przeniesienie kodu lub danych. Plik źródłowy
pozostaje jako krótki import zgodności wyłącznie tak długo, jak istnieją stare
wywołania. Nie kopiujemy modeli ani dużych danych w ramach tej migracji.

## Planowane: Oczy — prywatny czat

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `copy_bridge.py` | `oczy/android_bridge.py` | przeniesione | Most do lokalnego Androidowego pomocnika. |
| `screen_ocr_bridge.py` | `oczy/local_ocr.py` | przeniesione | Jednorazowy lokalny OCR. |
| `copy_vision.py` | `oczy/vision.py` | przeniesione | Wizualne sygnały bez OCR. |
| `private_chat_eyes.py` | `oczy/private_chat.py` | przeniesione | Jednoklatkowa obserwacja PM. |
| `private_chat_ocr.py` | `oczy/private_reader.py` | przeniesione | Bezpieczne otwarcie i odczyt. |
| `private_chat_packets.py` | `oczy/packets.py` | przeniesione | Długie wiadomości i paczki; nigdy nie miesza paczek po zmianie/utracie potwierdzonego nagłówka. |
| `peva_eyes_watch.py` | `oczy/watch.py` | przeniesione | Pętla czuwania bez modelu. |
| `peva_eyes_ui.py` | `oczy/eyes_window.py` | przeniesione | Osobne, ręcznie uruchamiane okno testowe samych Oczu. |
| `screenDetector.py` | `oczy/screen_detection.py` | przeniesione | Rozpoznawanie ekranu i odznak PM; usunięto nieużywany import starego zrzutu ADB. |
| `game_state.py` | `oczy/game_state.py` | przeniesione | Mały, tylko-odczytowy skrót stanu widocznego na ekranie. |

## Zasada wycofania

Jeżeli po migracji testy importu lub odczytu nie przejdą, przywracamy plik z
archiwum migracji albo pozostawiamy działającą wersję zgodności. Nie usuwamy
oryginalnych funkcji przed potwierdzonym testem.

## Przeniesione: Charakter

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `peva_personality.py` | `charakter/personality.py` | przeniesione | Stały styl odpowiedzi; nadal czyta aktywny JSON z `config/`. |
| `peva_self.py` | `charakter/identity.py` | przeniesione | Tożsamość oraz nienaruszalne zasady PEva. |
| `social_context.py` | `charakter/social_context.py` | przeniesione | Profile relacji graczy i ograniczony kontekst rozmowy sojuszu. |
| `player_style.py` | `charakter/player_style.py` | przeniesione | Deterministyczny styl i długość odpowiedzi. |
| `humanization_store.py` | `charakter/humanization_store.py` | przeniesione | Trwałe profile relacji, styl i zatwierdzone wspomnienia są częścią Charakteru; stara nazwa pozostała małym mostkiem zgodności. Nie wykonuje ADB ani wysyłania. |
| `social_initiative.py` | `charakter/social_initiative.py` | przeniesione | Ocenia tylko kandydaturę inicjatywy z UID/roomem, relacją i limitami; nie generuje ani nie wysyła tekstu. Stara nazwa jest mostkiem zgodności. |
| `social_reply_policy.py` | `charakter/social_reply_policy.py` | przeniesione | Decyduje wyłącznie, czy można utworzyć podgląd zwykłej odpowiedzi społecznej; zachowuje ciszę dla zamknięć i żądań akcji. Stara nazwa jest mostkiem zgodności. |
| `social_policy.py` | `charakter/social_policy.py` | przeniesione | Wybiera potrzebę odpowiedzi i priorytet społeczny; nie wykonuje akcji. `SocialPriority` należy wyłącznie do Charakteru, więc nowy runtime nie importuje dawnego `GoalPriority`; stary `PlayerLoop` jawnie zamienia tylko wartość liczbową na swój typ celu. Stara nazwa jest mostkiem zgodności. |

## Przeniesione: zachowany stary runtime

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `message_logging.py` | `archive/legacy_runtime/message_logging.py` | przeniesione | Formatowanie logów przychodzących i wysyłki należy wyłącznie do zachowanego transportu. Nazwa w katalogu głównym jest małym mostkiem zgodności. |
| `player_events.py` | `archive/legacy_runtime/player_events.py` | przeniesione | Zdarzenia dawnego `PlayerLoop` nie są kontraktem nowego runtime. Nazwa w katalogu głównym jest małym mostkiem zgodności. |
| `settings_menu.py` | `archive/legacy_runtime/settings_menu.py` | przeniesione | Stare menu terminalowe z opcjami PlayerLoop i auto-send jest dostępne wyłącznie dla zgodności. Nie należy do `PEva.cmd`. |
| `world_state.py` | `archive/legacy_runtime/world_state.py` | przeniesione | Stan obserwacji ekranu i starej pętli; nie wykonuje ADB ani akcji. Nazwa w katalogu głównym jest mostkiem zgodności. |
| `goal_manager.py` | `archive/legacy_runtime/goal_manager.py` | przeniesione | Kolejka celów starego PlayerLoop. Nie wykonuje akcji; stara nazwa jest mostkiem zgodności. |
| `user_decision.py` | `archive/legacy_runtime/user_decision.py` | przeniesione | Magazyn ręcznych decyzji dla starego PlayerLoop. Nie wykonuje ADB ani wysyłania; stara nazwa jest mostkiem zgodności. |
| `decision_engine.py` | `archive/legacy_runtime/decision_engine.py` | przeniesione | Planowanie decyzji dla starego PlayerLoop. Tworzy wyłącznie opis akcji i czeka na wykonawcę; nie jest nowym Mózgiem i nie wykonuje ADB. |
| `navigation_manager.py` | `archive/legacy_runtime/navigation_manager.py` | przeniesione | Planowanie, obserwacja i odzyskiwanie nawigacji starego PlayerLoop. Nie klika samodzielnie; wykonawcą pozostaje zachowany `ActionExecutor`. |
| `chat_capture.py` | `archive/legacy_runtime/chat_capture.py` | przeniesione | Tylko-odczytowy parser logcat zachowanego runtime. Rozpoznaje oba znane callbacki Last Z, filtruje poprawny prywatny pokój i nie wysyła wiadomości. Nazwa w katalogu głównym jest małym mostkiem zgodności. |
| `agent.py` | `archive/legacy_runtime/agent.py` | przeniesione | Dawne przygotowanie odpowiedzi i kolejka oczekujących odpowiedzi dla `PlayerLoop`. Nie jest używane przez nowy `peva_runtime.py`; nazwa w katalogu głównym jest małym mostkiem zgodności. |
| `peva_start.py` | `archive/legacy_runtime/peva_start.py` | przeniesione | Historyczny launcher patcha i ręcznego testu Capture. Nie jest uruchamiany przez `PEva.cmd`; root nadal przekazuje punkty podmiany potrzebne testowi symulacyjnemu. |
| `update_patch.py` | `archive/legacy_runtime/update_patch.py` | przeniesione | Ręczny, zachowany silnik patchowania Capture. Nie jest importowany ani uruchamiany przez zwykłą PEvę; nazwa główna jest przeźroczystym mostkiem dla świadomego wywołania. |
| `coordinates.py` | `archive/legacy_runtime/coordinates.py` | przeniesione | Stałe współrzędne dawnego transportu ADB. Nie należą do nowych Oczu ani Rąk; nazwa główna jest wyłącznie importem zgodności. |
| `action_ex.py` | `archive/legacy_runtime/action_ex.py` | przeniesione | Wykonawca dawnych akcji i walidacji prywatnej odpowiedzi. Nowy runtime go nie importuje; główna nazwa jest przeźroczystym mostkiem dla starego łańcucha i testów. |
| `player_loop.py` | `archive/legacy_runtime/player_loop.py` | przeniesione | Duży koordynator dawnego agenta, pamięci i akcji. Nie jest zależnością nowego runtime; nazwa główna jest przeźroczystym mostkiem dla historycznych importów i testów. |
| `adb_controll.py` | `archive/legacy_runtime/adb_controll.py` | przeniesione | Pełny historyczny transport ADB. Główna nazwa przekazuje dokładnie ten sam obiekt modułu dla dawnych testów; nowe Oczy i Ręce go nie importują. |

## Dodane: nowy rdzeń bez fizycznych Rąk

| Ścieżka | Stan | Uwagi |
|---|---|---|
| `mozg/local_model.py` | dodane | Jedyny klient lokalnego modelu; nie ładuje GPU przy imporcie. |
| `lastz_ai.py` | `mozg/lastz_ai.py` | przeniesione | Właściwy klient i kontrakty lokalnego modelu należą do Mózgu; stara nazwa pozostała mostkiem zgodności. |
| `memory_engine.py` | `mozg/memory_engine.py` | przeniesione | Ekstrakcja i walidacja pamięci długoterminowej; nie steruje ADB ani nie tworzy odpowiedzi w czacie. |
| `conversation_store.py` | `mozg/conversation_store.py` | przeniesione | Trwała historia rozmów i fakty pamięci SQLite; pliki baz nie były przenoszone. |
| `memory_candidates.py` | `mozg/memory_candidates.py` | przeniesione | Konserwatywne wykrywanie jawnie podanych faktów, bez dodatkowego modelu. |
| `memory_review.py` | `mozg/memory_review.py` | przeniesione | Kolejka ręcznego przeglądu i promowania pamięci; używa wskazanej bazy SQLite. |
| `llama_server_manager.py` | `mozg/llama_server_manager.py` | przeniesione | Cykl życia lokalnego `llama-server`; stara nazwa jest wyłącznie importem zgodności. |
| `mozg/decisions.py` | dodane | Buduje wyłącznie decyzję/podgląd, nigdy akcję ADB. |
| `action_claim_guard.py` | `mozg/action_claim_guard.py` | przeniesione | Ostateczna blokada fałszywych deklaracji niepotwierdzonej akcji; stara nazwa jest wyłącznie importem zgodności dla legacy. |
| `action_claim_rewriter.py` | `mozg/action_claim_rewriter.py` | przeniesione | Poprawia niepotwierdzoną deklarację akcji albo zwraca bezpieczny fallback; nie wykonuje akcji. Stara nazwa jest mostkiem zgodności. |
| `reply_quality_gate.py` | `mozg/reply_quality_gate.py` | przeniesione | Odrzuca techniczne wycieki i surowy output modelu; stara nazwa jest wyłącznie importem zgodności dla legacy. |
| `reply_naturalness.py` | `mozg/reply_naturalness.py` | przeniesione | Ograniczona kontrola powtórzeń i asystentowego tonu odpowiedzi. |
| `message_intent.py` | `mozg/message_intent.py` | przeniesione | Deterministyczna analiza intencji wiadomości oraz ostrożny fallback lokalnego modelu. Stara nazwa jest mostkiem zgodności. |
| `private_context.py` | `mozg/private_context.py` | przeniesione | Krótkotrwały kontekst prywatnej rozmowy jest kluczowany wyłącznie przez UID i room; stara nazwa jest mostkiem zgodności. |
| `command_parser.py` | `mozg/command_parser.py` | przeniesione | Rozpoznaje tekst polecenia i poziom ryzyka, ale nie wykonuje żadnej akcji. Stara nazwa jest mostkiem zgodności. |
| `pending_commands.py` | `mozg/pending_commands.py` | przeniesione | Przechowuje wyłącznie status oczekującego polecenia i wymóg potwierdzenia; nie łączy się z wykonawcą akcji. Stara nazwa jest mostkiem zgodności. |
| `peva_runtime.py` | dodane | Łączy części PEva w SAFE i zwraca wyłącznie `Action(NONE)`. |
| `peva_panel.py` | dodane | Lokalny panel stanu, tylko `127.0.0.1`. |
| `peva_app.py` | dodane | Normalne okno startowe nowego runtime; nie startuje nic przy imporcie. |
| `oczy/identity.py` | dodane | Parser wyłącznie wydzielonego nagłówka PM; nazwa nie jest UID. |
| `oczy/android_capture.py` | dodane | Jedyny adapter Oczu do odczytu PNG przez ADB; nie zapisuje plików, nie dotyka ekranu i nie zna tekstu. |
| `rece/read_only_navigation.py` | dodane | Dwa ograniczone gesty potrzebne Oczom: otwarcie środka potwierdzonego wiersza PM i krótkie przewinięcie środkiem otwartego PM. Nie zna wpisywania ani wysyłania. |
| `rece/read_only_adb.py` | dodane | Wąski adapter dwóch gestów Androida: `tap` i `swipe`; nie importuje starego ADB i nie zawiera tekstu lub wysyłania. |
| `response_delay.py` | `emocje/response_delay.py` | przeniesione | Niewstrzymujący harmonogram odpowiedzi; nie wysyła wiadomości. |
| `delivery_state.py` | `rece/delivery_state.py` | przeniesione | Jedno źródło stanów potwierdzenia dla dawnych Rąk; stara nazwa jest mostkiem zgodności. Moduł nie wykonuje ADB ani wysyłania. |

`action_ex.py` i `adb_controll.py` są zachowane w całości wyłącznie w
`archive/legacy_runtime/`. Nie wolno ich podłączać do nowej PEva bez osobnego,
zweryfikowanego kontraktu wysyłki.

## Przeniesione: aktywne ustawienia

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `runtime_settings.py` | `config/runtime_settings.py` | przeniesione | Walidacja ustawień i rejestru modeli mieszka obok aktywnych JSON-ów; stara nazwa wskazuje tę samą implementację. |
| `runtime_config.py` | `config/runtime_config.py` | przeniesione | Wspólne ścieżki, ADB i stałe formatu PM mają jedno źródło prawdy; stara nazwa pozostała mostkiem. |
| `prompt_manager.py` | `config/prompt_manager.py` | przeniesione | Loader promptów mieszka obok `config/prompts/`; zachowano dawny import. |

Mostek `llama_server_manager.py` udostępnia dokładnie ten sam obiekt modułu co
`mozg.llama_server_manager`, więc istniejące testy mogą nadal bezpiecznie
podmieniać jego wewnętrzne sprawdzenie portu bez duplikowania stanu serwera.

## Przeniesione: punkt wejścia

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `main.py` (dawny runtime) | `archive/legacy_runtime/main_legacy.py` | przeniesione | Zachowane bez skracania i ładowane tylko dla zgodności. |
| `main.py` (nowy) | `main.py` | zastąpione | Otwiera `peva_app.py`; normalny start nie importuje starego ADB. |

`PEva.cmd` wskazuje teraz nowy `main.py`. Nie zastępuje gry, nie instaluje APK
i nie włącza automatycznych akcji.

## Przeniesione: dane treningowe

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `feedback_store.py` | `trening/feedback_store.py` | przeniesione | Lokalny magazyn ocen odpowiedzi dla świadomego przeglądu i budowania danych. Nie jest ładowany przez zwykły start PEva; dawna nazwa zachowuje ręczny import i `main()`. |
| `dataset_builder.py` | `trening/dataset_builder.py` | przeniesione | Buduje dane treningowe po przeglądzie ocen. Nie trenuje modelu i nie jest uruchamiany przez zwykłą PEvę; dawna nazwa zachowuje ręczny import i `main()`. |
| `private_message_corpus_summary.py` | `trening/private_message_corpus_summary.py` | przeniesione | Wyłącznie anonimowe, zbiorcze podsumowanie lokalnych danych do audytu treningu. Nie jest uruchamiane przez PEva i nie tworzy danych treningowych. |
| `conversation_review.py` | `trening/conversation_review.py` | przeniesione | Wyłącznie ręczny, terminalowy przegląd kandydatów i pamięci. Nie włącza OCR, modelu, nawigacji ani wysyłania; dawna nazwa zachowuje ręczny import i `main()`. |
| `agency_store.py` | `trening/agency_store.py` | przeniesione | Magazyn historycznych decyzji Agency i ich ocen. Sam nie podejmuje decyzji, nie wykonuje akcji i nie jest importowany przez zwykły runtime. |
| `agency_types.py` | `trening/agency_types.py` | przeniesione | Kontrakty historycznego formatu Agency dla danych i benchmarków. Walidują układ decyzji, lecz nie wywołują modelu, ADB ani wysyłania. |
| `agency_core.py` | `trening/agency_core.py` | przeniesione | Historyczny formatowy rdzeń Agency używany przez benchmarki i dawny PlayerLoop. Nie jest importowany przez zwykły runtime; bez completion wybiera bezpieczne `WAIT`/`DO_NOTHING`. |
| `agency_benchmark.py` | `trening/agency_benchmark.py` | przeniesione | Runner zamrożonego benchmarku Agency. Przeniesiono sam kod bez zmiany benchmarku, modeli, adapterów ani raportów; zwykła PEva go nie importuje. |
| `agency_dataset_policy.py` | `trening/agency_dataset_policy.py` | przeniesione | Wspólne, konserwatywne zasady kwalifikacji danych Agency. Chroni przed użyciem nieprzejrzanych danych i niepotwierdzonych deklaracji akcji jako przykładów pozytywnych. |
| `agency_dataset_audit.py` | `trening/agency_dataset_audit.py` | przeniesione | Audyt danych syntetycznych i kandydatów rzeczywistych. Nie ładuje modelu ani nie trenuje; ewentualny manifest jest zapisywany wyłącznie przy świadomym, ręcznym wywołaniu. |
| `agency_dataset_builder.py` | `trening/agency_dataset_builder.py` | przeniesione | Budownik historycznego, izolowanego pilota danych Agency. Zapis artefaktów jest wyłącznie ręczny i odmawia nadpisania; zwykła PEva go nie importuje. |
| `agency_corrective_dataset_v2.py`, `agency_corrective_dataset_v3.py`, `agency_corrective_dataset_v4.py` | `trening/agency_corrective_dataset_v2.py`, `trening/agency_corrective_dataset_v3.py`, `trening/agency_corrective_dataset_v4.py` | przeniesione | Niezmienne syntetyczne korekty zachowane jako historyczne źródła danych. Każdy zapis odmawia nadpisania, a zwykła PEva ich nie importuje. |
| `agency_dataset_builder_v2.py`, `agency_dataset_builder_v3.py`, `agency_dataset_builder_v4.py` | `trening/agency_dataset_builder_v2.py`, `trening/agency_dataset_builder_v3.py`, `trening/agency_dataset_builder_v4.py` | przeniesione | Historyczne budowniki kolejnych pilotów. Zachowują grupowanie rodzin, bramki bezpieczeństwa i blokadę nadpisania; zwykła PEva ich nie importuje. |
| `agency_real_candidate_export.py` | `trening/agency_real_candidate_export.py` | przeniesione | Eksportuje wyłącznie kandydatów do ręcznego przeglądu z realnych rozmów. Nie uruchamia modelu, nie tworzy automatycznie przykładów pozytywnych i nie jest częścią zwykłej PEva. |
| `agency_seed_dataset.py` | `trening/agency_seed_dataset.py` | przeniesione | Edytorialne źródło syntetycznego zestawu bazowego Agency. Nie czyta rozmów, nie ładuje modelu i nie uruchamia treningu; istniejący zestaw danych pozostał nietknięty. |
| `benchmark_evaluator.py` | `trening/benchmark_evaluator.py` | przeniesione | Deterministyczny evaluator benchmarku prywatnego czatu. Nie wywołuje modelu ani nie wysyła wiadomości; domyślna ścieżka benchmarku jest niezależna od bieżącego katalogu terminala. |
| `agency_review_studio.py` | `trening/agency_review_studio.py` | przeniesione | Ręczne, lokalne Review Studio Agency z atomowymi zapisami i kopiami poprzednich ocen. Nie jest uruchamiane przez zwykłą PEvę, nie otwiera gry ani nie wysyła wiadomości. |
| `peva_agency_cleanup.py` | `trening/agency_cleanup.py` | przeniesione | Konserwatywne, idempotentne czyszczenie historycznych danych Agency. Dawny mostek zachowuje kontrolowane podmiany ścieżek dla testów izolowanych kopii baz; zwykła PEva go nie importuje. |
| `train_peva_agency_pilot_v2.py`, `train_peva_agency_pilot_v3.py`, `train_peva_agency_pilot_v4.py` | `trening/train_agency_pilot_v2.py`, `trening/train_agency_pilot_v3.py`, `trening/train_agency_pilot_v4.py` | przeniesione | Ręczne runnery historycznych QLoRA pilotów. Zachowują bramkę sumy kontrolnej zamrożonego benchmarku; zwykła PEva ich nie importuje ani nie uruchamia. |
| `agency_real_situation_test.py` | `trening/agency_real_situation_test.py` | przeniesione | Ręczny test adaptera na kandydaturach do przeglądu. Ma wyłącznie tryb offline, nie ma importów ADB/Last Z/wysyłania i weryfikuje źródło przed i po przebiegu. |
| `training_dataset_audit.py` | `trening/training_dataset_audit.py` | przeniesione | Audyt przyszłego zbioru Qwen pozostaje tylko do odczytu: nie ładuje modelu, nie rozpoczyna treningu ani nie zmienia danych. Stara nazwa jest mostkiem zgodności. |
| `train_peva_qwen7b.py` | `trening/train_qwen7b.py` | przeniesione | Zachowany ręczny runner QLoRA Qwen jest częścią Treningu. Stara nazwa działa dla dokumentacji historycznej, lecz zwykła PEva go nie importuje ani nie uruchamia. |
| `model_benchmark_peva.py` | `trening/model_benchmark.py` | przeniesione | Ręczny benchmark już uruchomionego lokalnego modelu. Nie startuje serwera, nie zmienia `current` i nie wysyła wiadomości; stara nazwa działa jako mostek dla dokumentacji akceptacyjnej. |
| `export_peva_databases.py` | `trening/export_databases.py` | przeniesione i zabezpieczone | Eksport lokalnych baz wymaga teraz jawnego wywołania. Odczyt SQLite jest tylko do odczytu i po zakończeniu zamyka uchwyt; sam import nie tworzy eksportu. Stara nazwa jest mostkiem zgodności. |
| `export_peva_agency_v4_gguf.py` | `trening/export_agency_v4_gguf.py` | przeniesione | Ręczny eksport testowego GGUF V4. Nie zmienia modelu `current`, konfiguracji runtime ani ustawień wysyłki; stara nazwa pozostaje mostkiem dla zachowanego skryptu WSL. |
| `train_peva_agency_pilot.py` | `trening/train_agency_pilot_base.py` | przeniesione | Wspólna, historyczna baza QLoRA V1–V4. Właściwe runnery V2–V4 importują ją już bezpośrednio z Treningu; dawna nazwa została zachowana dla raportów i starych instrukcji. |

## Przeniesione: narzędzia testowe

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `peva_ui_probe.py` | `testy/manual_ui_probe.py` | przeniesione | Ręczny test UIAutomator zapisujący artefakty tylko po świadomym uruchomieniu; stara nazwa uruchamia ten sam tester. |
| `peva_ocr_test.py` | `testy/manual_ocr_test.py` | przeniesione i odciążone | Ręczny pilot OCR CPU. Import nie ładuje już OpenCV, NumPy ani RapidOCR; zależności są ładowane dopiero podczas świadomego uruchomienia testu. |
| `tests/test_peva_start.py` | `archive/legacy_tests/test_peva_start_pre_update_patch.py` | zarchiwizowane | Testował API wcześniejszego launchera opartego o zapisywany numer patcha, zastąpionego przez `update_patch.py`. |
| `README.txt` | `docs/archive/README_CAPTURE_LEGACY.txt` | zarchiwizowane | Nieodwoływana instrukcja starego testu capture. Zachowana identycznie (SHA-256 `5F2053CAA00B68304654BF9167498A35DB8268263F760D350ADF5150BFD36FE3`). |
| `INSTRUKCJA.txt` | `docs/archive/INSTRUKCJA_OCR_LEGACY.txt` | zarchiwizowane | Nieodwoływana instrukcja technicznego testu OCR/Oczu. Zachowana identycznie (SHA-256 `5ECF3662EF3C0E8107FA3245E0B39C60D8812F57D4B5A8A486F23B8ACF49CEE8`). |
| `PEva_Eyes.cmd` | `archive/legacy_launchers/PEva_Eyes.cmd` | zarchiwizowane | Samodzielne dawne czuwanie Oczu. SHA-256 `C4E3C739A6573EA956C9E80A28E7CB2F1AE56CF1A6221D903A6350EAD1B0EFD2`. |
| `PEva_OCR.cmd` | `archive/legacy_launchers/PEva_OCR.cmd` | zarchiwizowane | Dawny tester/przygotowanie OCR. SHA-256 `C791F5360764B7DF45D91C2962138C4DD8D876D9281AD2CBE6B5A962768C7F58`. |
| `PEva_UI_Test.cmd` | `archive/legacy_launchers/PEva_UI_Test.cmd` | zarchiwizowane | Dawny tester hierarchii Androida. SHA-256 `A369258E2EDA759AF39DC5F39B2361C3C8A0055D2808DD826750F71D2A8AFC24`. |
| `Start_Llama_Server.bat` oraz `Start_Llama_Server.ps1` | `archive/legacy_launchers/` | zarchiwizowane | Ręczny serwer poza cyklem życia PEva. SHA-256: `168B7AF469BD342CCF24963557BA3FE612E430326DE53DB33EB0B8DC164CDF39`, `549A033F14A9673906351D2F43219A6D325D1D1C92A0C9449AA312D2C9CB52D4`. |

## Zarchiwizowane: nieużywane narzędzia V1 danych

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `agency_seed_dataset_v1.py` | `archive/legacy_training_scripts/agency_seed_dataset_v1.py` | zarchiwizowane | Nieużywany generator drugiej, historycznej paczki syntetycznej. Dane wynikowe zostają nietknięte. SHA-256 `37E6D765CF35173E2C54DADAC2B680E01239DB09AC435823E0F6714F700B3A97`. |
| `review_agency_dataset.py` | `archive/legacy_training_scripts/review_agency_dataset.py` | zarchiwizowane | Nieużywany generator statycznej strony przeglądu V0/V1. SHA-256 `65C9810BD8B9022BE76AAEFA9B5997D4B2A1CEB170AAEB70FAE305D551014041`. |

Aktualny launcher `peva_start.py` ma test symulacyjny w
`testy/test_peva_start_current.py`; nie uruchamia ADB, gry, patcha ani
rzeczywistego oczekiwania.

## Zarchiwizowane: nieużywany plan dawnej nawigacji

| Stara ścieżka | Docelowa ścieżka | Stan | Uwagi |
|---|---|---|---|
| `private_chat_navigation.py` | `archive/legacy_runtime/private_chat_navigation.py` | przeniesione | Właściwy dawny plan prowadzący do kroku `send_ready` nie jest w aktywnym runtime. Zachowano identyczną zawartość (SHA-256 `6E23A589CA7A6865D4324FF359F2A5DA7D2B7E887DE11DFBAFD620F50740F117`); katalog główny ma tylko mały mostek importu wymagany przez `main_legacy.py`. |

## Zarchiwizowane: dawne nazwy z katalogu głównego

27 października 2026 r. przeniesiono **88** plików `.py` z katalogu głównego
do `archive/legacy_root_bridges/`, bez ich usuwania i bez zmiany ich zawartości.
Były to dawne mostki zgodności oraz ręczne entry pointy. Importy aktywnej
aplikacji, narzędzi i obu zestawów testów zostały przepisane na właściwe
moduły w `oczy/`, `mozg/`, `charakter/`, `emocje/`, `rece/`, `config/`,
`trening/` lub `archive/legacy_runtime/`.

W katalogu głównym pozostaje dokładnie pięć modułów zwykłego runtime:
`contracts.py`, `main.py`, `peva_app.py`, `peva_panel.py` i
`peva_runtime.py`. Historyczny skrypt `apply_lastz_chat_capture_patch.ps1`
pozostał jako ręczna konserwacja i korzysta bezpośrednio z
`archive/legacy_runtime/update_patch.py`.
