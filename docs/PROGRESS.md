# PEva — postęp przebudowy

Ten plik jest trwałym punktem kontynuacji pracy. Aktualizujemy go po każdym
zakończonym etapie, zanim rozpoczniemy kolejną większą zmianę.

## Stan na 2026-10-25

**Etap 5 — nowy bezpieczny start i testowanie Oczu: w toku.**

Oczy, Charakter, Emocje, Mózg, SAFE, panel i zwykły start są już wydzielone.
Jedyne faktyczne przeniesienie kodu sterującego to zachowany dawny `main.py`
do `archive/legacy_runtime/main_legacy.py`; żaden model, adapter, benchmark,
baza danych ani dane treningowe nie zostały usunięte.

## Ustalony cel

- `main.py` uruchamia normalny tryb PEva jako jedną całość.
- Model jest ładowany przy starcie normalnego trybu; Oczy i Mózg są gotowe.
- Oczy pracują oszczędnie i aktywują OCR tylko po nowym sygnale.
- Mózg uruchamia cięższą analizę tylko po otrzymaniu poprawnej paczki danych.
- Ręce dla akcji gry są przygotowane od startu, ale zawsze respektują globalny
  `SAFE`. Oczy mają oddzielony, wąski gest tylko do otwarcia jednego
  potwierdzonego wiersza PM; nie potrafi on wpisać ani wysłać tekstu.
- Panel pozwala włączać i wyłączać moduły wyłącznie do testów.
- Projekt pozostaje przede wszystkim Pythonem. Androidowy lokalny OCR jest
  wyjątkiem, bo musi działać wewnątrz Androida.
- Trening zachowuje tylko dwa zatwierdzone modele: `current` i `previous`.

## Docelowe katalogi

```text
main.py
oczy/        # ekran, UI, OCR, wiadomości, zmiany
mozg/        # decyzje, model, pamięć, koordynacja
rece/        # ADB, kliknięcia, wpisywanie, wysyłanie, SAFE
emocje/      # bieżący stan wpływający na odpowiedź
charakter/   # stała osobowość i styl PEva
trening/     # dane, kandydaci, benchmarki, current/previous
testy/       # testy wyłącznie poza kodem produkcyjnym
assets/      # obrazy, szablony UI i inne trwałe zasoby
config/      # mała liczba aktywnych ustawień
logs/        # czytelny przebieg działania
docs/        # architektura, postęp, krótkie instrukcje
archive/     # tylko elementy jednoznacznie zastąpione
```

## Kolejność przebudowy

1. **Audyt:** aktywne wejścia, importy, ścieżki, modele i duże katalogi.
2. **Wspólne kontrakty:** małe struktury `VisionResult`, `BrainInput`,
   `Decision` i `Action`; bez frameworka i bez kolejki usług.
3. **Oczy:** przenieść obecny działający odczyt prywatnego czatu bez zmiany
   jego zachowania.
4. **Ręce i SAFE:** wydzielić ruchy/ADB oraz centralną blokadę każdej akcji.
5. **Mózg, Charakter, Emocje:** podłączyć do wspólnych kontraktów.
6. **Panel lokalny:** status, przełączniki testowe, SAFE/Resume i czytelny
   przepływ „zobaczyła → zrozumiała → zdecydowała → wykonała”.
7. **Trening:** jeden kontrolowany pipeline z walidacją, benchmarkiem,
   GGUF oraz decyzją Accept/Reject.
8. **Czyszczenie:** dopiero po testach przenieść jednoznacznie zastąpione
   pliki do archiwum i ograniczyć modele do `current`/`previous`.

## Zasady bezpieczeństwa migracji

- Nie usuwamy plików tylko na podstawie nazwy lub wieku.
- Każde przeniesienie musi mieć wpis w `docs/MIGRATION_MANIFEST.md`.
- Po większej zmianie uruchamiamy odpowiednie testy i krótki test startu.
- Żaden etap nie włącza automatycznego wysyłania wiadomości bez osobnego
  potwierdzenia i przejścia testów bezpieczeństwa.

## Ostatnia weryfikacja

- Lokalny moduł Oczu potrafi wykrywać prywatny czat i lokalnie odczytać
  aktualny ekran na żądanie.
- Zbieranie paczek wiadomości, ograniczone przewijanie oraz zatrzymanie przy
  popupie/nieznanym ekranie są gotowe jako kod bieżącej wersji.
- Nie ma aktywnej usługi Androidowego OCR ani pozostawionego obrazu źródłowego.
  Po technicznej kontroli provider został ręcznie zatrzymany; nie zostaje w
  pamięci emulatora między odczytami.
- Testy nowego fundamentu, centralnego runtime, panelu, zwykłego okna startu,
  parsera nagłówka i bezpiecznego cyklu modelu: **133/133 OK** (`testy/`).
- Pełny zestaw zachowanej warstwy: **243/243 OK** (`tests/`), również z
  `ResourceWarning` traktowanym jako błąd. Nie wymaga instalowania `pytest`.
- Import starej i nowej ścieżki Oczu: **OK**.
- Testy zachowania pakietów prywatnego czatu po migracji: **OK**.

## Wyniki pierwszego audytu

- Obecny `main.py` obsługuje starszy runtime oparty o `ChatCapture`, logcat i
  `PlayerLoop`; nie jest jeszcze punktem wejścia dla nowych Oczu.
- Nowe Oczy (`private_chat_eyes`, `private_chat_ocr`,
  `private_chat_packets`) są samodzielne i obecnie uruchamiane przez małe okno
  `peva_eyes_ui.py`.
- Nie wolno przenosić plików Oczu bez jednoczesnego uporządkowania ich zależności
  z `copy_vision`, `screenDetector`, `adb_controll` i Androidowym bridge'em.
- Katalogi modeli i archiwów zawierają wiele niezależnych eksperymentów oraz
  kopii narzędzi. Będą oceniane osobno; nie są jeszcze kandydatami do usunięcia.

## Wykonane w tym etapie

- Dodano `contracts.py`: niewielkie kontrakty `VisionResult`, `BrainInput`,
  `Decision` i `Action`.
- Dodano `rece/safety.py`: centralny `SafetyGate`, który startuje w SAFE.
- Dodano niezależny test fundamentu w `testy/test_contracts.py`.
- Przeniesiono działający kod Oczu do `oczy/`; stare nazwy są tymczasowymi
  importami zgodności i nie zawierają już logiki.
- Przeniesiono Charakter do `charakter/`; aktywne JSON-y pozostały wspólną,
  czytelną konfiguracją w `config/`.
- Dodano `emocje/`: mały stan runtime z ograniczonymi wartościami i kontekstem
  dla Mózgu; nie podejmuje sam decyzji ani nie wykonuje akcji.
- Dodano `mozg/`: `BrainContextBuilder` składa w jednym miejscu wynik Oczu,
  pamięć, Charakter i Emocje. Model zostanie podłączony dopiero w następnym
  etapie, bez zmiany formatu tych danych.
- Dodano `mozg/local_model.py`: jedyny klient lokalnego `llama-server`.
  Model uruchamia się wyłącznie po świadomym starcie PEva, a jego stan jest
  widoczny bez ujawniania rozmów.
- Przeniesiono zarządzanie cyklem życia `llama-server` do `mozg/`. Dawna
  nazwa w katalogu głównym jest tylko mostkiem zgodności; sprawdzenie obu
  importów i pełny zestaw testów: **32/32 OK**, bez uruchamiania GPU.
- Przeniesiono właściwy klient modelu `lastz_ai.py` do `mozg/lastz_ai.py`.
  Stara ścieżka pozostaje lekkim mostkiem zgodności, a test potwierdza, że
  obie nazwy wskazują te same typy i nie uruchamiają modelu przy imporcie.
- Przeniesiono walidację ustawień do `config/runtime_settings.py`, obok
  aktywnych plików JSON. Poprawiono mostek starego menedżera serwera, aby
  zgodność obejmowała także jego wewnętrzny test portu. Nowy zestaw testów:
  **32/32 OK**; dawny zestaw ustawień: **15/15 OK**.
- Przeniesiono wykrywanie ekranów i czerwonych odznak PM do
  `oczy/screen_detection.py`. Usunięto tam nieużywany import starego zrzutu
  ADB; dawna nazwa jest mostkiem zgodności. Testy Oczu i nawigacji: **30/30
  OK**, a pełny nowy zestaw: **34/34 OK**.
- Przeniesiono mały, tylko-odczytowy skrót `game_state.py` do `oczy/`, aby
  wszystkie informacje o tym, co PEva widzi na ekranie, były w jednym
  miejscu. Z dawnymi testami wizji, nawigacji i ustawień: **45/45 OK**.
- Przeniesiono wspólne stałe środowiska do `config/runtime_config.py` oraz
  loader promptów do `config/prompt_manager.py`. Zmostkowano stare nazwy,
  więc nie powstały drugie źródła ścieżek ani promptów. Pełny nowy zestaw:
  **36/36 OK**; sprawdzone stare moduły: **63/63 OK**. Test starych Rąk
  wypisał `SEND BLOCKED`, czyli zgodnie z oczekiwaniem nic nie zostało wysłane.
- Oczyszczono fixture starszych testów z nieużywanych baz Agency/humanizacji.
  Zestaw zgodności przechodzi **63/63 OK** także z `ResourceWarning` jako
  błędem; nie zmieniono produkcyjnych opcji PEva.
- Ręczny tester Android UIAutomator przeniesiono do `testy/manual_ui_probe.py`.
  Nie uruchamia się w normalnym starcie i nadal wymaga ręcznego potwierdzenia
  każdego zrzutu. Pełny nowy zestaw: **37/37 OK**.
- Osobne okno samych Oczu przeniesiono do `oczy/eyes_window.py`; normalny
  start nadal używa pełnej PEva, a skrót Oczu jest wyłącznie narzędziem testowym.
  Pełny nowy zestaw: **39/39 OK**.
- Stary test `peva_start` odwoływał się do usuniętego API wcześniejszego
  mechanizmu patcha. Zachowano go w `archive/legacy_tests/`, a obecny launcher
  ma własny test symulacyjny bez ADB, gry i czekania. Pełny nowy zestaw:
  **39/39 OK**.
- Dodano test zamykania zwykłego okna PEva: panel oraz runtime są zamykane
  dokładnie raz, nawet gdy metoda zamykania zostanie wywołana ponownie. To
  obejmuje zatrzymanie własnego modelu i czuwania. Pełny nowy zestaw:
  **40/40 OK**.
- Przeniesiono silnik pamięci długoterminowej do `mozg/memory_engine.py`.
  Zachowuje on dawną nazwę importu, nie wykonuje ADB i nie ładuje modelu przy
  imporcie. Pełny nowy zestaw: **42/42 OK**.
- Przeniesiono trwałą historię rozmów i fakty pamięci do
  `mozg/conversation_store.py`, bez przenoszenia plików SQLite. Testy danych,
  humanizacji i polityk społecznych użyły wyłącznie baz tymczasowych. Pełny
  nowy zestaw: **44/44 OK**; sprawdzone istniejące moduły: **93/93 OK**.
- Domknięto ostatnią nieużywaną bazę Agency w fixture testowym. Wszystkie
  sprawdzone testy przechodzą z `ResourceWarning` jako błędem.
- Do `mozg/` przeniesiono także konserwatywne wykrywanie kandydatów pamięci i
  kolejkę ich przeglądu. Nie uruchamiają dodatkowego modelu ani nie wykonują
  akcji w Last Z. Pełny nowy zestaw: **46/46 OK**; sprawdzone istniejące
  moduły: **96/96 OK**.
- Dodano test przekazywania pamięci do Mózgu. Runtime przyjmuje ją wyłącznie
  jako kontekst dostarczony przez przyszły resolver potwierdzonego UID; nigdy
  nie pobiera pamięci po nicku z OCR. Usunięto przy tym wykrytą w teście pętlę
  importów — moduły pamięci nie są ładowane automatycznie przy każdym imporcie
  `mozg`. Pełny nowy zestaw: **47/47 OK**.
- Kontekst relacji i styl gracza przeniesiono do `charakter/`; ograniczoną
  kontrolę naturalności do `mozg/`, a niewstrzymujący harmonogram odpowiedzi
  do `emocje/`. Te elementy nie wykonują ADB ani wysyłania. Pełny nowy zestaw:
  **48/48 OK**; sprawdzone istniejące moduły: **98/98 OK**.
- Dodano bramkę strukturalną: normalny start, panel i Mózg nie mogą importować
  `PlayerLoop`, starego agenta, capture ani executora akcji. Ręce potrzebne
  Oczom nie mogą zawierać symboli wpisywania lub wysyłania. Pełny nowy zestaw:
  **52/52 OK**. Po przerwanym dłuższym przebiegu sprawdzono i zamknięto jego
  dokładnie zidentyfikowane procesy Python; nie został model ani OCR helper.
- Test zachowanego, starego launchera capture jest teraz jednoznacznie opisany
  jako test zgodności i wycisza wyłącznie swój symulowany komunikat. Nie zmienia
  to normalnego startu przez `PEva.cmd`; pełny zestaw po tej korekcie:
  **52/52 OK**.
- Nieużywany plan dawnej nawigacji PM przeniesiono z katalogu głównego do
  `archive/legacy_runtime/` bez zmiany ani utraty treści (SHA-256 zapisano w
  manifeście migracji). Prowadził on do starego kroku `send_ready`, dlatego
  nie był częścią aktywnego odczytu Oczu.
- Główny `README` pokazuje teraz tylko normalne użycie PEva i prostą mapę
  modeli. Dawne komendy Review Studio/QLoRA zachowano bez zmian w
  `docs/archive/LEGACY_TRAINING_COMMANDS.md`, więc nie mieszają się z
  jednoplikowym startem `PEva.cmd`.
- Dwie nieodwoływane instrukcje starych testów (`README.txt` i
  `INSTRUKCJA.txt`) przeniesiono do `docs/archive/` z zapisanymi sumami
  SHA-256. Katalog główny nie pokazuje ich już jako alternatywnych sposobów
  uruchamiania PEva.
- W katalogu głównym pozostał jeden skrót uruchomieniowy: `PEva.cmd`.
  Samodzielne Oczu/OCR/UI testy i ręczny serwer modelu przeniesiono do
  `archive/legacy_launchers/` z zapisanymi sumami SHA-256. Nowy test pilnuje,
  by dodatkowy `.cmd` ani `.bat` nie wrócił do normalnego widoku.
- Ujednoznaczniono diagram architektury: pokazuje teraz rzeczywistą drogę
  `PEva.cmd` → okno PEva → SAFE/model/Oczy → `Action NONE`, a przyszłe Ręce
  i archiwum są wyraźnie odłączone.
- Skorygowano opis Charakteru, Emocji i pamięci tak, aby odpowiadał kodowi:
  zwykły runtime dodaje tylko Charakter i Emocje. Relacje oraz trwała pamięć
  nie są wyszukiwane po nazwie OCR i czekają na osobny resolver UID.
- Do `archive/legacy_training_scripts/` trafiły dwa nieużywane narzędzia V1
  do generowania/przeglądu danych. Nie zmieniono ich danych wyjściowych ani
  benchmarków; sumy SHA-256 są w manifeście.
- Starszy zestaw zgodności ujawnił import planu PM przez zachowany
  `main_legacy.py`. W katalogu głównym przywrócono wyłącznie trzy-symbolowy
  mostek importu; właściwy plan nadal mieszka w archiwum i nie jest dostępny
  dla nowego runtime.
- Po tej korekcie wykonano pełną świeżą regresję: **52/52 OK** w `testy/`
  oraz **98/98 OK** w sprawdzonych modułach starej warstwy, z
  `ResourceWarning` traktowanym jako błąd. Nie został proces Python ani
  `llama-server`. Bramka strukturalna zakazuje też importu mostka starej
  nawigacji przez nowy start, panel i Mózg.
- Wcześniejsze zatrzymanie łączonej regresji wynikało z historycznego testu
  tworzącego kopie baz wewnątrz aktywnego `data/`. Po naprawie pełny, łączony
  przebieg znów jest bezpieczny: **98/98 OK** z `ResourceWarning` jako błędem
  i bez procesu Python, modelu ani Oczu w tle.
- Zdiagnozowano zawieszanie `test_agency_data_cleanup` przy kontroli
  `ResourceWarning`: tworzył kopie testowych baz wewnątrz aktywnego `data/`.
  Test korzysta teraz z systemowego katalogu tymczasowego, nadal kopiuje
  wyłącznie archiwalne bazy i nie zapisuje do danych PEva.
- Raport konserwatywnego czyszczenia baz obsługuje też izolowaną kopię poza
  katalogiem projektu. Nadal weryfikuje wszystkie sumy kontrolne; zmienia się
  jedynie zapis ścieżki w raporcie testowym.
- Dodano `docs/ROOT_SURFACE_AUDIT.md`: prostą klasyfikację elementów
  widocznych w katalogu głównym na zwykły start, mostki zgodności, stary
  runtime i trening. Jest to punkt odniesienia dla kolejnych bezpiecznych
  przenosin.
- Przeniesiono definicje stanu dostarczenia do `rece/delivery_state.py`.
  To dane bezpieczeństwa Rąk, nie wykonawca ADB; dawny import działa przez
  mały mostek zgodności. Dodano test wspólnego źródła i zachowania fail-closed.
- Poprawiacz niepotwierdzonych deklaracji akcji przeniesiono do
  `mozg/action_claim_rewriter.py`. Pozostaje wyłącznie tekstową, fail-closed
  funkcją Mózgu i nie może wykonywać akcji w grze; stary import nadal działa.
- Magazyn profili relacji, stylu i zatwierdzonych wspomnień przeniesiono do
  `charakter/humanization_store.py`. Działa nadal przez dawny import, ale
  właściwy kod Charakteru nie ma zależności od ADB ani wysyłania.
- Polityka inicjatywy społecznej przeniesiona do
  `charakter/social_initiative.py` zachowuje limity, wyciszenie i wymóg
  prawdziwego UID/roomu. Nadal tylko wybiera kandydaturę — nie generuje ani
  nie wysyła wiadomości.
- Polityka podglądu odpowiedzi społecznej przeniesiona do
  `charakter/social_reply_policy.py` zachowuje najważniejszą granicę:
  zamknięcie rozmowy i żądanie akcji nie powodują podglądu odpowiedzi.
- Polityka społeczna wyboru odpowiedzi i priorytetu przeniesiona do
  `charakter/social_policy.py`. Nadal nie wykonuje Rąk; zwykły PM nie jest
  tłumiony przez ograniczenia przeznaczone dla czatu sojuszu.
- Analizator intencji wiadomości przeniesiono do `mozg/message_intent.py`.
  Charakter korzysta bezpośrednio z tej implementacji, a dawny import jest
  wyłącznie mostkiem dla starego runtime. Analizator nie wykonuje akcji.
- Krótkotrwały kontekst prywatnej rozmowy przeniesiono do
  `mozg/private_context.py`. Test sprawdza rozdzielenie rozmów po prawdziwym
  UID i roomie, nie po nazwie wyświetlonej przez OCR.
- Parser poleceń przeniesiono do `mozg/command_parser.py`. Rozpoznaje on
  tylko intencję i poziom ryzyka; test potwierdza, że groźne polecenie wymaga
  potwierdzenia, ale nie uruchamia wykonawcy akcji.
- Kolejka oczekujących poleceń przeniesiona do `mozg/pending_commands.py`
  utrzymuje wysokie ryzyko w stanie oczekiwania na potwierdzenie i nie ma
  drogi do wykonawcy akcji.
- Magazyn ocen odpowiedzi przeniesiono do `trening/feedback_store.py`.
  Nadal można go użyć przez dawny import, lecz zwykły start PEva nie ładuje
  go ani nie zapisuje ocen bez świadomego przeglądu.
- Budownik danych po przeglądzie przeniesiono do `trening/dataset_builder.py`.
  Wyklucza on niezatwierdzone odpowiedzi i deklaracje niepotwierdzonych akcji;
  nie uruchamia treningu ani nie jest częścią zwykłego startu PEva.
- Anonimowe podsumowanie korpusu prywatnych wiadomości przeniesiono do
  `trening/private_message_corpus_summary.py`. Narzędzie pozostaje wyłącznie
  do audytu: nie zapisuje tekstu rozmów ani identyfikatorów i nie tworzy
  automatycznie danych treningowych.
- Ręczny przegląd kandydatów rozmów przeniesiono do
  `trening/conversation_review.py`. Jest offline i nie uruchamia modelu,
  Oczu, nawigacji ani wysyłania wiadomości.
- Magazyn historycznych decyzji Agency przeniesiono do
  `trening/agency_store.py`. Nadal działa dla starego runtime przez mały
  mostek, ale sama implementacja wyłącznie zapisuje decyzje i oceny do
  późniejszego przeglądu — nie wykonuje akcji w Last Z.
- Kontrakty historycznego formatu Agency przeniesiono do
  `trening/agency_types.py`. Nadal walidują granicę decyzja/wiadomość dla
  benchmarków, ale nie wywołują modelu, ADB ani wysyłania.
- Historyczny rdzeń Agency przeniesiono do `trening/agency_core.py`. Nadal
  służy benchmarkom i starej zgodności, lecz nowy runtime go nie importuje;
  bez completion wybiera bezpieczne `WAIT` lub `DO_NOTHING`.
- Kod runnera zamrożonego benchmarku Agency przeniesiono do
  `trening/agency_benchmark.py`. Nie zmieniono benchmarku, modeli, adapterów
  ani raportów; zwykła PEva go nie importuje ani nie ładuje modelu.
- Wspólną, konserwatywną politykę kwalifikacji danych Agency przeniesiono do
  `trening/agency_dataset_policy.py`. Nadal wyklucza nieprzejrzane dane,
  testowe profile i niepotwierdzone deklaracje akcji jako pozytywne przykłady.
- Audyt danych Agency przeniesiono do `trening/agency_dataset_audit.py`.
  Sprawdza on strukturę i rozdzielenie danych bez ładowania modelu lub
  rozpoczynania treningu; manifest można zapisać wyłącznie ręcznie.
- Budownik historycznego, izolowanego pilota Agency przeniesiono do
  `trening/agency_dataset_builder.py`. Zwykła PEva go nie importuje, a jego
  ręczny zapis odmawia nadpisania istniejących artefaktów.
- Trzy niezmienne, syntetyczne źródła korekt Agency V2–V4 przeniesiono do
  `trening/`. Zwykła PEva ich nie importuje, a każde zachowuje blokadę
  nadpisania istniejących artefaktów.
- Trzy budowniki historycznych pilotów Agency V2–V4 przeniesiono do
  `trening/`. Nadal utrzymują grupowanie rodzin i bramki bezpieczeństwa, lecz
  zwykła PEva ich nie importuje ani nie uruchamia treningu.
- Eksport kandydatów z realnych rozmów przeniesiono do
  `trening/agency_real_candidate_export.py`. Tworzy tylko kolejkę do ręcznego
  przeglądu, nie pozytywne dane treningowe i nie jest częścią zwykłej PEva.
- Edytorialne źródło syntetycznego zestawu bazowego Agency przeniesiono do
  `trening/agency_seed_dataset.py`. Istniejący zestaw danych pozostał
  nietknięty; kod nie czyta rozmów, nie ładuje modelu i nie uruchamia treningu.
- Deterministyczny evaluator benchmarku prywatnego czatu przeniesiono do
  `trening/benchmark_evaluator.py`. Nie wywołuje modelu, nie otwiera gry i nie
  wysyła wiadomości; domyślna ścieżka benchmarku nie zależy od terminala.
- Ręczne, lokalne Review Studio Agency przeniesiono do
  `trening/agency_review_studio.py`. Zachowuje atomowy zapis i kopię poprzedniej
  oceny, ale nie jest uruchamiane przez zwykłą PEvę, nie otwiera gry i nie wysyła.
- Konserwatywne czyszczenie danych Agency przeniesiono do
  `trening/agency_cleanup.py`. Mostek starej nazwy przekazuje wyłącznie
  kontrolowane podmiany ścieżek dla testów izolowanych kopii baz; zwykła PEva
  go nie importuje.
- Ręczne runnery historycznych pilotów QLoRA Agency V2–V4 przeniesiono do
  `trening/`. Zachowują kontrolę sumy zamrożonego benchmarku, lecz zwykła PEva
  ich nie importuje ani nie uruchamia.
- Ręczny test realnych sytuacji adaptera Agency przeniesiono do
  `trening/agency_real_situation_test.py`. Ma wyłącznie tryb offline, nie
  importuje ADB/Last Z/wysyłania i weryfikuje źródło kandydatów przed oraz po.
- Audyt starego transportu potwierdził ścieżkę `PlayerLoop → ActionExecutor →
  adb_controll → ADB`. Wzmocniono automatyczną granicę: zwykły start, panel,
  Mózg, Charakter i Emocje nie mogą importować tej warstwy ani `trening/`.
- Test granicy ujawnił historyczny import `GoalPriority` w Charakterze.
  Zastąpiono go lokalnym `SocialPriority`; stary `PlayerLoop` jawnie zamienia
  wartość liczbową z powrotem na swój typ celu, więc jego zachowanie zostaje
  zgodne bez wciągania go do nowego runtime.
- Właściwy kod formatowania logów starego transportu, zdarzeń dawnego
  `PlayerLoop` i jego menu terminalowego przeniesiono do
  `archive/legacy_runtime/`. Dawne nazwy w katalogu głównym są tylko małymi
  mostkami zgodności; żaden z tych modułów nie jest częścią `PEva.cmd`.
- Naprawiono historyczny odczyt Capture po zmianie nazwy callbacku Last Z:
  parser akceptuje teraz ograniczoną listę `push.chat.room` oraz
  `push.chat.up.room`. Dla kanału `custom` odrzuca też pokój, który nie
  potwierdza pary UID PEva/nadawca. To korekta samego odczytu logcat; nie
  podłącza Rąk, modelu ani automatycznej wysyłki.
- Test mostka Copy nie wymaga już zewnętrznego `pytest`: zachowuje te same
  przypadki odczytu, ograniczonego nasłuchu i paczkowania, ale działa przez
  standardowy `unittest` używany w całym projekcie.
- Rozszerzono `docs/ARCHITECTURE.md` o aktualną granicę między nową PEvą a
  zachowanym transportem: zwykły start kończy się na `Action NONE`, a dawna
  ścieżka Capture → PlayerLoop → ActionExecutor → ADB jest odłączona.
- Audyt danych oraz ręczny runner przyszłego Qwen przeniesiono do `trening/`.
  Dawne nazwy w katalogu głównym zachowano jako mostki dla starych instrukcji;
  zwykły start PEva ich nie importuje ani nie może przez nie rozpocząć treningu.
- Inferencyjny benchmark Qwen przeniesiono do `trening/model_benchmark.py`.
  Może odczytać zamrożony benchmark bez modelu lub serwera; historyczna nazwa
  w katalogu głównym pozostała wyłącznie mostkiem zgodności.
- Eksport lokalnych baz przeniesiono do `trening/export_databases.py` i
  zmieniono na jawne, tylko-odczytowe narzędzie. Sam import nie tworzy już
  eksportu, a uchwyt SQLite jest zamykany przed zakończeniem działania.
- Ręczny pilot OCR przeniesiono do `testy/manual_ocr_test.py`. OpenCV, NumPy
  i RapidOCR są ładowane dopiero po świadomym starcie pilota, więc zwykły
  import i normalne uruchomienie PEva nie rezerwują jego zasobów.
- Ręczny eksport testowego GGUF V4 przeniesiono do `trening/`. Zachowany
  skrypt WSL nadal używa dawnej nazwy przez mostek, ale zwykły runtime nie
  importuje ani nie uruchamia eksportu, merge ani kwantyzacji.
- Wspólną bazę QLoRA historycznych pilotów Agency V1–V4 przeniesiono do
  `trening/`. Runnery V2–V4 odwołują się już bezpośrednio do tej wersji;
  dawna nazwa V1 pozostała tylko dla zgodności raportów i instrukcji.
- Stan ekranu, kolejkę celów i ręczne decyzje starego PlayerLoop przeniesiono
  do `archive/legacy_runtime/`. Nie wykonują ADB ani wysyłania; nazwy w
  katalogu głównym są krótkimi mostkami dla zachowanych testów i starej pętli.
- Planowanie decyzji oraz nawigacji starego PlayerLoop przeniesiono do
  `archive/legacy_runtime/`. Te moduły tylko tworzą i kontrolują plan;
  wykonanie fizyczne pozostaje wyłącznie w zachowanym `ActionExecutor`.
- Właściwy Capture starego runtime przeniesiono do
  `archive/legacy_runtime/chat_capture.py`. Zachowany `chat_capture.py` w
  katalogu głównym jest wyłącznie mostkiem zgodności. Capture nie jest częścią
  zwykłego startu PEva i nie wysyła wiadomości; zachowuje parser obu znanych
  nazw callbacku Last Z oraz walidację prywatnego pokoju.
- Z Oczu wydzielono `oczy/screen_capture.py`: pojedynczy PNG jest pobierany
  wyłącznie do pamięci przez stałe polecenie odczytu. Dotychczasowe wejście w
  `oczy.vision` pozostało zgodne. Adapter nie zapisuje obrazu, nie uruchamia
  pętli i nie ma dostępu do tekstu ani wysyłania.
- Właściwy dawny `agent.py` przeniesiono do `archive/legacy_runtime/`.
  Przygotowuje on kolejkę odpowiedzi tylko dla zachowanego `PlayerLoop`; nowy
  runtime go nie importuje. Nazwa w katalogu głównym jest mostkiem zgodności
  dla historycznego startu i testów.
- Historyczny launcher patcha i ręcznego testu Capture przeniesiono do
  `archive/legacy_runtime/peva_start.py`. Krótka nazwa w katalogu głównym
  przekazuje nawet tymczasowe podmiany używane przez test, lecz zwykły start
  PEva go nie importuje.
- Ręczny silnik patchowania Capture (`update_patch.py`) również ma teraz
  właściwy kod w `archive/legacy_runtime/`. Jego krótka nazwa w głównym
  katalogu zachowuje świadome wywołanie i punkty zgodności, ale nie jest
  drogą uruchamiania PEva.
- Stałe współrzędne starego transportu ADB przeniesiono do
  `archive/legacy_runtime/coordinates.py`. Nowe Oczy korzystają z własnych,
  ograniczonych obliczeń gestów, a nazwa w głównym katalogu jest wyłącznie
  mostkiem zgodności dla dawnego transportu.
- Usunięto 11 katalogów `__pycache__` (547 odtwarzalnych plików `.pyc`) tylko
  z folderów kodu projektu. Nie usuwano modeli, baz, treningów, danych gry ani
  kopii zapasowych.
- Właściwy `ActionExecutor` starego transportu przeniesiono do
  `archive/legacy_runtime/action_ex.py`. Nowy runtime nadal go nie importuje;
  nazwa główna jest wyłącznie przeźroczystym mostkiem dla zachowanej pętli i
  testów, wraz z ich kontrolowanymi podmianami ustawień.
- Właściwy, duży `PlayerLoop` również przeniesiono do
  `archive/legacy_runtime/`. Nowa PEva go nie importuje; główna nazwa jest
  przeźroczystym mostkiem zachowującym stare importy oraz kontrolowane podmiany
  używane przez testy.
- Audyt `decision_engine.py` potwierdził, że pozostaje częścią starego
  PlayerLoop: tworzy plany nawigacji i wysyłki. Nie został pozornie przeniesiony
  do nowego Mózgu; granicę opisano w mapie katalogu i audycie Rąk.
- Aktualna pełna regresja po migracjach Rąk, Mózgu, Charakteru i Treningu:
  **142/142 OK** w `testy/` oraz **243/243 OK** w całym `tests/`,
  obejmującym polityki relacji, Capture, Copy Bridge, inicjatywę, podglądy
  odpowiedzi i budowanie danych. Kontrola po przebiegu: brak Python,
  `llama-server` i helpera Oczu.
- Dodano `mozg/decisions.py`: odpowiedź jest wyłącznie podglądem, gdy
  tożsamość rozmówcy jest potwierdzona. Nieznana tożsamość daje `WAIT`.
- Przeniesiono właściwą implementację filtra jakości odpowiedzi i blokady
  niepotwierdzonych deklaracji akcji do `mozg/`. Stare pliki w katalogu
  głównym są krótkimi importami zgodności dla dawnego `player_loop`.
  Sprawdzenie tożsamości starych i nowych importów oraz pełne testy: **27/27
  OK**.
- Dodano `peva_runtime.py`: składa Model, Oczy, Mózg, Charakter, Emocje i SAFE
  jako jedną PEvę. W obecnej fazie wynik każdej decyzji to techniczne
  `Action(NONE)` — nie ma podpiętej ścieżki wysyłania.
- Dodano `peva_panel.py`: lokalny panel `127.0.0.1` do statusu oraz
  przełączników testowych SAFE/Oczy/modelu, bez treści czatu i bez akcji w
  grze. Model można zatrzymać lub uruchomić ponownie bez zatrzymywania PEva;
  SAFE pozostaje aktywny.
- Dodano `peva_app.py`: zwykłe okno startowe, które jednym kliknięciem ładuje
  model oraz Oczy w tle i uruchamia panel; zamknięcie zatrzymuje czuwanie oraz
  tylko serwer modelu będący własnością PEva.
- Rozszerzono kontrakt OCR o opcjonalny, osobny nagłówek rozmowy. Parser nie
  zgaduje autora z bąbelka: bez rozpoznanego nagłówka nadal wybiera `WAIT`.
  Nowy APK pomocnika Androida przeszedł lokalną kompilację offline i został
  zaktualizowany w BlueStacks (25.10.2026). Odczyt przez lokalny provider
  potwierdził nowe, oddzielne pole `header_b64`; pozostaje krótki test na
  rzeczywistym otwartym PM.
- Wykonano jeden techniczny odczyt całej ścieżki na emulatorze: lokalny OCR
  zwrócił wynik, jednorazowy obraz został usunięty, a proces pomocnika
  zatrzymany. Aktualny ekran nie był potwierdzonym PM (`header_b64` był pusty),
  więc nie odczytano ani nie zapisano żadnej tożsamości rozmówcy.
- Bufor krótkiej serii wiadomości sprawdza teraz potwierdzony nagłówek przy
  każdym odczycie. Zmiana rozmowy lub brak poprawnego nagłówka odcina starą
  paczkę, zamiast przypisać jej tekst nowemu rozmówcy.
- Wydzielono z Oczu do `rece/read_only_navigation.py` jedyne ruchy wymagane
  do odczytu: kliknięcie środka jednego, już potwierdzonego wiersza PM oraz
  ograniczone przewinięcie środkiem tej rozmowy. Moduł nie importuje pola
  rozmowy, klawiatury ani wysyłania.
- Poprzedni `main.py` przeniesiono do
  `archive/legacy_runtime/main_legacy.py`. Nowy `main.py` otwiera tylko nowe
  okno PEva; stare nazwy są ładowane leniwie wyłącznie dla zgodności testów.
- `PEva.cmd` jest teraz tylko jednoplikowym, dwuklikowym startem nowego
  `main.py`, bez parametrów dla użytkownika.
- Wzmocniono cykl życia lokalnego modelu: drugi Start nie może utracić
  własności uruchomionego `llama-server`, a nieudana kontrola zdrowia zamyka
  wyłącznie proces PEva utworzony przed błędem. Testy potwierdzają oba przypadki.
- Naprawiono dwie wąskie ścieżki zgodności po migracji Oczu, aby stary zestaw
  testów nadal widział pomocnicze parsery metadanych.
- Zapisano audyt rozmiaru, modeli, treningów i zależności w
  `docs/AUDIT_2026-10-25.md`.

## Punkt wznowienia

1. Czekać wyłącznie na sygnał użytkownika „czat otwarty”. Wtedy wykonać jeden
   skan nowego APK Oczu na otwartym PM; nie uruchamiać modelu, nie wysyłać
   wiadomości ani nie zapisywać treści czatu. Potwierdzić tylko: nagłówek,
   odrzucenie/akceptację parsera, brak obrazu po odczycie i brak procesu
   pomocnika po teście.
2. Dopiero po niezależnym, prawdziwym źródle UID zaprojektować resolver UID
   oddzielony od nazwy. Nie wolno wyszukiwać osoby po treści rozmowy ani
   uznawać podobnej nazwy za tożsamość po zmianie nicku.
3. Kontynuować przenoszenie aktywnej logiki małymi porcjami. Audyt i
   przeniesienie `lastz_ai.py`, ustawień, promptów oraz detekcji ekranu są
   zakończone z testem zgodności. Do `trening/` przeniesiono magazyny, polityki,
   audyty, korekty, budowniki, Review Studio, benchmarki, runnery i narzędzia
   ręcznego przeglądu Agency. Dawne nazwy są tylko małymi mostkami zgodności.
   Właściwe źródła `adb_controll.py`, `action_ex.py` i `player_loop.py` są już
   w archiwum. Oczy i Ręce mają własne, minimalne adaptery odczytu PNG oraz
   gestów. Dalsze etapy dotyczą tylko rzeczywistego resolvera UID po sygnale
   użytkownika; nie wolno łączyć go mechanicznie ze starym wysyłaniem. Audyt skrótów
   uruchomieniowych potwierdza: normalnym startem jest wyłącznie `PEva.cmd`;
   pozostałe narzędzia są opisane w `docs/PEVA_ONE_CLICK.md`.

## Ostatni stan zweryfikowany

- Androidowy pomocnik OCR 0.4 jest zainstalowany w BlueStacks. Techniczny
  skan ekranowy przeszedł, wynik wrócił, obraz tymczasowy zniknął i proces
  pomocnika został zatrzymany. Nie był to ekran PM, dlatego `header_b64` był
  pusty i test znaczenia nagłówka pozostaje otwarty.
- Model lokalny nie był uruchamiany do tych testów, a nowy runtime nie ma
  ścieżki wpisywania lub wysyłania do Last Z.
- Pełny zestaw `testy/`: **142/142 OK** po wydzieleniu Rąk, Mózgu, magazynu
  ocen, budownika danych, anonimowego audytu, ręcznego przeglądu oraz warstwy
  Agency Treningu, a także dodaniu niezależnych przełączników Oczu/modelu.
- V4 nadal pozostaje kandydatem testowym, nie jest modelem runtime i nie
  wolno go automatycznie promować.

Audyt Rąk zapisano w `docs/HANDS_AUDIT.md`: stary transport pozostaje
odłączony od nowego runtime, ponieważ sama nazwa w nagłówku nie jest UID.
Uzupełniająca mapa tej granicy jest w `docs/LEGACY_ACTION_BOUNDARY.md`.

- Po przeniesieniu źródeł dużego `PlayerLoop` oraz pełnego historycznego ADB
  do `archive/legacy_runtime/` przeprowadzono świeżą pełną regresję:
  **142/142 OK** w `testy/` oraz **243/243 OK** w `tests/`. Nowe
  `oczy/android_capture.py` i `rece/read_only_adb.py` zastępują ich jedyne
  potrzebne, minimalne operacje: obraz PNG, tap oraz ograniczony swipe.
  Testy uruchomiono z normalnym dostępem do lokalnych baz PEva, bez modelu,
  BlueStacks, OCR i bez procesu w tle. Wszystkie nieużywane launchery i
  właściwe stare aplikacje są w archiwum.
- Dziewięć historycznych skryptów WSL V2–V4 (`run_peva_v*_*.sh`) przeniesiono
  do `archive/legacy_launchers/`, obok wcześniejszych skrótów diagnostycznych.
  Katalog główny nie oferuje już drugiego startu treningu ani modelu; test
  pilnuje, że jedynym normalnym launcherem pozostaje `PEva.cmd`.
- Jednorazową kopię `docs.zip` przeniesiono do `archive/snapshots/`; nie była
  używana przez kod ani zwykły start PEva, a została zachowana bez zmian.
- Uporządkowano katalog główny: **88** dawnych plików Pythona przeniesiono
  bez usuwania do `archive/legacy_root_bridges/`. Zostało tylko pięć modułów
  zwykłego runtime (`contracts.py`, `main.py`, `peva_app.py`, `peva_panel.py`,
  `peva_runtime.py`) oraz jedyny skrót `PEva.cmd`. Aktywne importy wskazują
  teraz bezpośrednio na foldery części PEva lub `archive/legacy_runtime/`.
  Ręczny skrypt naprawy capture nadal działa, ponieważ jego awaryjny silnik
  jest teraz wskazywany przez `archive/legacy_runtime/update_patch.py`.
  Świeża regresja po tej zmianie: **143/143 OK** w `testy/` oraz
  **243/243 OK** w `tests/`; bez uruchamiania modelu, OCR, BlueStacks lub gry.

## Voice pilot — 27.10.2026

- Dodano lokalny trening i ocenę **PEva Voice** oddzielone od Agency oraz
  zwykłego runtime. Pierwszy adapter `peva-voice-pilot-20261027` trenował z
  czystej, lokalnej bazy Qwen2.5-3B przez 3 epoki na 308 oryginalnych,
  syntetycznych przykładach. Nie pobierał modelu ani danych, nie dotykał gry i
  nie otworzył odłożonego testu podczas treningu.
- Adapter jest **zachowany, ale odrzucony do użycia**. Techniczny filtr
  odłożonych 28 rekordów poprawił się do 28/28 i nie znalazł fałszywej
  deklaracji akcji, lecz ręczny podgląd wykazał monotonne, miejscami
  nieuziemione odpowiedzi. Nie wolno go merge'ować, eksportować, wybierać w
  runtime ani podłączać do wysyłania.
- Dodano `evaluate_chat_adapter.py` dla lokalnej, deterministycznej oceny
  czystej bazy i adaptera, bez ADB, Last Z lub sieci. Dodatkowo test jakości
  odrzuca widoczny wrapper modelu typu "Sure, here's my reply:".
- Zabezpieczono lokalność modelu: runtime odrzuca endpoint LLM spoza
  `localhost`/`127.0.0.1` zanim może wysłać żądanie.
- Aktualna regresja po tych zmianach: **143/143** w `testy/`, **247/247** w
  `tests/`, **18/18** w testach treningu i **7/7** w testach benchmarku.
- Kolejny eksperyment wymaga nowego, kontekstowo różnorodnego zestawu danych
  i startu od czystej bazy, plus przejścia sześciu niezależnych testów rozmowy
  wieloturowej. Pierwsza próba ich uruchomienia nie wystartowała, bo WSL nie
  mógł utworzyć VM przy działającym BlueStacks; emulator nie został zamknięty
  ani zmieniony. Szczegóły: `docs/PEVA_VOICE_PILOT_2026-10-27.md`.
- Przygotowano taki zestaw: **330/330** oryginalnych, syntetycznych rekordów
  z jednym targetem na każdy z **330** różnych promptów. Wszystkie 11 rodzin
  ma po 30 przykładów; test wycieków do 31 zamrożonych przypadków, walidacja,
  split i dry-run przeszły bez odrzuceń. Nowa konfiguracja zachowuje QLoRA,
  ale ma jedną epokę jako kontrolowaną odpowiedź na kolaps pierwszego pilota.
  Nie rozpoczęto treningu: najpierw potrzebny jest dostępny WSL i trudniejszy
  benchmark zachowania rozmownego.

## Voice pilots — wynik końcowy 27.10.2026

- Po zamknięciu emulatora wykonano dwa kontrolowane, jednoprzebiegowe treningi
  od tej samej czystej lokalnej bazy 3B. Adapter kontekstowy jest odrzucony:
  technicznie osiągnął 33/33 na własnym teście, lecz tylko **1/6** w niezależnej
  ocenie realistycznych rozmów.
- Ostatni adapter `peva-voice-social-repair-20261027` został wytrenowany od
  czystej bazy z nowym, 330-rekordowym materiałem i niższym LR `2e-5`.
  Zyskał 29/33 wobec 26/33 bazy na własnym teście oraz zachował **0** fałszywych
  deklaracji ukończonej akcji, ale na teście rozmownym ma tylko **3/6** — remis
  z bazą. Dlatego **nie jest kandydatem runtime** i nie wolno go wdrażać,
  merge'ować, eksportować ani podłączać do wysyłania.
- Wszystkie adaptery i raporty są zachowane jako dowód. Nic nie działa w tle:
  końcowy check WSL nie znalazł treningu ani ewaluacji. Świeże testy: **143/143**
  (`testy`), **247/247** (`tests`), **22/22** testów treningu i **7/7**
  testów benchmarku.
- Pełna decyzja, hashe, ścieżki oraz bezpieczny następny krok są w
  `docs/PEVA_VOICE_FINAL_2026-10-27.md`.

## Lokalny podgląd odpowiedzi — 27.10.2026

- Potwierdzono test normalnego runtime: Oczy odczytały kompletny pakiet
  widocznej rozmowy, Mózg wybrał `REPLY`, a globalny SAFE zachował
  `Action(NONE)`. Nie wysłano wiadomości do Last Z.
- Dodano do lokalnego panelu PEvy osobny endpoint i widok propozycji
  odpowiedzi. Pokazuje on tylko tekst wygenerowany przez PEvę, wyłącznie na
  `127.0.0.1` i tylko w pamięci aktualnej sesji; ani tekst rozmowy, ani
  podgląd nie są utrwalane w plikach. Zwykły endpoint statusu nadal nie
  ujawnia treści czatu.
- Regresja po zmianie: **144/144 OK** w `testy/` oraz **247/247 OK** w
  `tests/`. Uruchomiona już PEva używa poprzednio załadowanego kodu, dlatego
  trzeba ją zwyczajnie wyłączyć i uruchomić ponownie przez `PEva.cmd`, aby
  zobaczyć nowy podgląd.

## Ciągłość prywatnej rozmowy — 27.10.2026

- Runtime otrzymał krótką pamięć wyłącznie w RAM: maksymalnie 10 ostatnich
  pewnie rozpoznanych tur dla maksymalnie czterech aktualnie widzianych okien
  PM. Nie zapisuje treści do pliku, usuwa ją przy zamknięciu PEvy i nie łączy
  rozmów po zmianionym nicku.
- Jednorazowy Android OCR został rozszerzony w źródłach o pozycje rozpoznanych
  linii z tego samego skanu. Pozwala to rozróżnić lewy dymek gracza od prawego
  dymka PEvy i przekazać modelowi uporządkowaną historię zamiast całego ekranu
  jako jednej wiadomości. Starszy pomocnik nadal działa w trybie zgodności,
  lecz nie dostarczy historii, dopóki nowa APK nie zostanie zbudowana i
  zainstalowana.
- Prompt odpowiadania doprecyzowano: ma odpowiadać na najnowszą wiadomość,
  nie otwierać ponownie aktywnej rozmowy powitaniem oraz nie uciekać w ogólne
  pytanie o pomoc, jeśli istnieje konkretny temat.
- Regresja Python: **149/149 OK** w `testy/`, **247/247 OK** w `tests/`.
- Kod pomocnika Android został następnie skompilowany lokalnie przez dostępny
  Gradle 8.14.3 i Android Studio JBR 21: `:app:compileDebugJavaWithJavac` oraz
  `:app:assembleDebug` zakończyły się powodzeniem. Gotowy, ale **niezainstalowany**
  APK to `android/peva-copy-bridge/app/build/outputs/apk/debug/app-debug.apk`
  (46 359 066 B, SHA-256
  `F9CF87F5371FFA85D2BEB9AC2792EE10210D0273BD9E49B49E5602EA76A7CC4A`).
  Emulator, jego aplikacje oraz już uruchomiona PEva pozostały bez zmian.

## PEva Helper — 27.10.2026

- Pomocnik Android zmienił widoczną nazwę z technicznego „PEva Copy Bridge” na
  **PEva Helper**. To nadal ten sam lokalny pakiet (`com.peva.copybridge`),
  więc przyszła aktualizacja nie zmieni sparowania z runtime PEva.
- Panel Helpera jasno pokazuje trzy bezpieczne funkcje: odebranie jednej kopii,
  odebranie krótkiej serii do 12 kopii oraz pojedynczy OCR widocznego czatu.
  Pokazuje tylko stan i liczbę oczekujących rekordów, nigdy treść wiadomości.
- Dodano „Zatrzymaj i usuń tymczasowe dane PEva”. Zatrzymuje oba lokalne
  odczyty, czyści prywatną kolejkę i wynik OCR, ale nie nadpisuje bieżącego
  schowka użytkownika. Odczytana przez runtime seria Copy jest od teraz
  przekazywana jednokrotnie i usuwana natychmiast po lokalnym odbiorze.
- Helper nie ma funkcji wysyłania wiadomości, klikania w Last Z, ciągłego
  nagrywania ekranu ani dostępu do Internetu. Pełny opis: 
  `android/peva-copy-bridge/README.md`.
- Wersja `0.5-peva-helper` została zbudowana lokalnie przez Gradle jako debug
  APK. Kompilator zgłosił tylko standardowe ostrzeżenia o docelowym Java 8;
  build zakończył się powodzeniem. APK nie został zainstalowany, a emulator
  pozostał bez zmian. Artefakt ma 46 338 426 B i SHA-256
  `F2C0FE5DEE81623D412C29CA965DAA416FD864DFAB547BD1FCA9B7609BD92ED3`.

## Propozycje znajomości i aliasy — 27.10.2026

- Dodano lokalną, ręcznie zatwierdzaną książkę relacji PEva. Po co najmniej
  sześciu turach gracza i czterech turach PEva może powstać kandydatura, ale bez
  treści czatu: przechowuje tylko widziany nick, sojusz, daty, stan i
  proponowany alias.
- Panel lokalny prowadzi kolejno przez: propozycję do ręcznego wysłania,
  ręczne potwierdzenie znajomości, ustawienie aliasu w Last Z oraz lokalne
  potwierdzenie aliasu. Dostępne są też **Nie teraz** (30 dni ciszy) i
  **Zły wpływ — blokuj** (bez kolejnych propozycji).
- Żaden przycisk panelu nie wysyła wiadomości, nie akceptuje znajomości ani
  nie zmienia aliasu w grze. Każdy tylko zapisuje krok wykonany ręcznie przez
  użytkownika; globalne Ręce nadal mają `Action(NONE)`.
- Weryfikacja po zmianie: **158/158 OK** w `testy/` oraz **247/247 OK** w
  `tests/`.
- Odczyt PM rozpoznaje od teraz prosty tytuł aliasu dopiero po jego dokładnym,
  ręcznym potwierdzeniu. Kod Android Helpera podniesiono do wersji
  `0.6-peva-helper-friends`; APK został zbudowany lokalnie, ale nie jest
  zainstalowany. Ma 46 338 434 B i SHA-256
  `172521442C88B054154C7B004DE0EC9C7E6C74F5549048DF0E4A76EB5F1513E4`.
  Emulator wymaga świadomej instalacji i restartu PEva przed testem.
- **Instalacja potwierdzona, 27.10.2026:** aktualizacja została zainstalowana
  do jedynego podłączonego emulatora `emulator-5554`. System potwierdził
  pakiet `com.peva.copybridge`, `versionCode=6`,
  `versionName=0.6-peva-helper-friends`. Instalacja nie uruchomiła Helpera,
  PEva, modelu ani żadnej akcji w Last Z.

## Uproszczone sterowanie Helperem — 27.10.2026

- Normalna ścieżka PEva to automatyczne Oczy, nie ręczne Copy: lekki skan
  wizualny najwyżej raz na sekundę potwierdza zmianę, po czym zleca jeden
  lokalny OCR widocznej prywatnej rozmowy. Obraz jest chwilowy i kasowany po
  odczycie; nie ma stałego OCR ani archiwum czatu.
- Ekran Helpera został odpowiednio przebudowany w źródłach: domyślnie wyjaśnia,
  że PEva na komputerze korzysta z Helpera automatycznie. Copy i ręczny OCR są
  schowane w sekcji diagnostycznej, a więc nie są już mylone ze zwykłą obsługą.
- Wersja `0.8-peva-helper-automatic-eyes` (`versionCode=8`) została zbudowana
  bez błędów (wyłącznie standardowe ostrzeżenia Java 8), ma 46 338 438 B i
  SHA-256 `9D7B104218B45C9EFA1B8A27E4319BEFE0F1953480CF7FCBC0C4E1052037E11F`.
  Została zainstalowana jako aktualizacja w `emulator-5554`; system potwierdził
  poprawny numer wersji. Nie uruchamiano PEva, modelu ani Last Z.

## Czytanie dymków OCR — 28.10.2026

- Ustalono końcowy podział: **OCR jest normalną metodą Oczu**, a ręczne Copy
  pozostaje tylko awaryjnym potwierdzeniem pojedynczego fragmentu, którego OCR
  nie odczytał pewnie. Nigdy nie jest wymagane do zwykłego działania PEva.
- Odczyt rozmowy wykorzystuje geometrię zwróconą przez ten sam lokalny OCR:
  lewy dymek oznacza gracza, prawy dymek PEva, a blisko ułożone linie jednego
  dymka są łączone w jedną wypowiedź.
- Dodano konkretne zabezpieczenia z ekranów testowych: **Headquarters**,
  współrzędne i stan bazy, **Give gifts**, cytaty `Reply …` wraz z kolejnymi
  liniami oraz znaczniki czasu nie mogą już wejść do historii jako słowa
  gracza ani PEva. Dłuższe, wielowierszowe wiadomości są zachowywane jako
  jeden turn.
- Weryfikacja: **160/160 OK** w `testy/`, **247/247 OK** w `tests/`.
  Ręce pozostają odłączone (`Action(NONE)`). Zmiana dotyczy Pythona; przed
  testem należy uruchomić PEvę ponownie, aby bieżący proces załadował kod.
# Automatyczny test rozmowy — 28.10.2026

- Potwierdzono, że uruchomiona PEva i Oczy działają: lokalny Helper dostarczył
  jednorazowy OCR widocznego prywatnego czatu kilka razy, bez wysyłania akcji.
- Test zatrzymał się bezpiecznie na `WAIT`, ponieważ górny nagłówek aktywnego
  czatu był zwykłym `DOKTORB`, a dotychczasowy parser przyjmował wyłącznie
  nagłówki z poziomem/tagiem sojuszu albo wcześniej potwierdzony alias.
- Dodano bezpieczny wyjątek tylko dla wydzielonego, wycentrowanego nagłówka
  dostarczonego przez Android Helper. Taki nick tworzy wyłącznie tymczasową
  sesję RAM: nie daje UID, nie scala historii po zmianie nicku i nie może
  zostać uzyskany z tekstu bąbelka.
- Dodano testy dla zwykłego nagłówka, pakietu i historii. Zmiana wymaga
  ponownego uruchomienia PEva przed następnym testem.

## Potwierdzenie po restarcie

- Test end-to-end przeszedł po restarcie: Oczy odebrały widoczną prywatną
  rozmowę automatycznie, zbudowały 6 krótkich tur tylko w RAM, a Mózg zwrócił
  lokalny podgląd odpowiedzi (`REPLY`).
- Prosty test pytania rachunkowego otrzymał poprawną, krótką odpowiedź.
- `last_action=NONE` i `physical_actions_connected=false`: PEva nie wysłała
  ani nie wpisała niczego do Last Z.

## Zatwierdzone rozmowy i wiedza o grze — 28.10.2026

- Dodano osobną, odwracalną zgodę użytkownika na przyszłe automatyczne
  odpowiedzi. Osoba musi przejść ręczny proces znajomości i mieć ustawiony
  stały, unikalny alias. Dopiero potem pojawia się przycisk „Zatwierdzam
  automatyczne odpowiedzi”; zgodę można jednym kliknięciem cofnąć.
- Panel pozwala też bezpiecznie uruchomić ten proces dla osoby, która już jest
  znajomym: użytkownik najpierw ustawia alias ręcznie w Last Z, a potem
  wpisuje tylko ten alias lokalnie. Nie ma potwierdzonego sposobu pozyskania
  UID innego gracza, więc PEva nie wymaga ani nie zapisuje pozornego ID.
  Taki wpis nadal nie daje PEvie możliwości wysłania wiadomości; tworzy tylko
  warunek do późniejszego, kontrolowanego testu.
- To jest wyłącznie bramka zgody — nie podłącza jeszcze Rąk, nie wpisuje i nie
  wysyła wiadomości. Bieżące modele rozmowne nie przeszły jakościowej bramki,
  dlatego byłoby nieuczciwe uruchomić automatyczną wysyłkę tylko dlatego, że
  kod technicznie mógłby ją posiadać. Szczegóły porównania są w
  `docs/MODEL_SELECTION_FOR_AUTO_REPLY.md`.
- Dodano małą, lokalną bazę ostrożnej wiedzy Last Z w
  `assets/knowledge/last_z_basics.json`. Nie ma zapytań internetowych podczas
  działania PEvy; maksymalnie dwie pasujące notatki trafiają do modelu tylko
  przy pytaniu o grę. Prompt nakazuje traktować je jako społecznościowe,
  potencjalnie nieaktualne wskazówki i sprawdzać dokładne wymagania w grze.
  Źródła oraz ograniczenia opisuje `docs/PEVA_GAME_KNOWLEDGE.md`.
- Testy po zmianie: **170/170 OK** (`testy/`) oraz **247/247 OK** (`tests/`).
  Historyczna warstwa testów nadal wypisuje znane ostrzeżenia o niezamkniętych
  połączeniach SQLite, mimo że proces kończy się kodem 0; nie zostały ukryte
  ani zignorowane jako sukces jakościowy.

## Precyzyjne propozycje znajomości — 28.10.2026

- Panel pokazuje teraz jawnie adresata jako `Do: <nick>`, a stały tekst
  propozycji brzmi tylko: `Hey, would you like to be friends?`.
- Usunięto błędny rekord `Survivor Profile`. To był tytuł ogólnego ekranu,
  nie gracz. Parser odrzuca teraz ten i inne ogólne tytuły, a tymczasowy,
  zwykły nagłówek OCR nie może już sam tworzyć trwałej propozycji znajomości.
- Testy nowej warstwy i pełne `testy/`: **173/173 OK**. Podczas ponownej
  kontroli zachowanego `tests/` stara warstwa `PlayerLoop` zatrzymała się przed
  asercjami na istniejącym `data/databases/humanization.db` (`sqlite3:
  unable to open database file`). To nie jest ścieżka nowego runtime ani tej
  zmiany, ale trzeba to naprawić przed kolejną pełną regresją/treningiem.

## Kwarantanna okna profilu nad czatem — 28.10.2026

- Potwierdzono przyczynę błędnego wpisu `Survivor Profile`: otwarte okno
  profilu zasłaniało prywatną rozmowę, lecz jej bąbelki były nadal widoczne w
  tle. Jednorazowy OCR poprawnie zobaczył tytuł *Survivor Profile*, ale starsza
  ścieżka nie rozumiała, że jest to modal, a nie rozmówca.
- Oczy rozpoznają teraz ten tytuł jako blokujące okno Last Z. Skan zostaje
  odrzucony przed utworzeniem paczki, hasha lub propozycji znajomości; nie
  trafia do Mózgu ani do historii. Jeśli okno otworzy się w trakcie zbierania
  kilku ekranów jednej wiadomości, cała niedokończona paczka jest porzucana i
  PEva nie wykonuje dodatkowego przewinięcia.
- Runtime usuwa też oczekującą krótką serię wiadomości, aby po zasłonięciu
  czatu nie przygotować później odpowiedzi do niepewnego ekranu.
- Weryfikacja po zmianie: **174/174 OK** w `testy/` i **247/247 OK** w
  `tests/`. Zachowane historyczne testy nadal wypisują ostrzeżenia o
  niezamkniętych połączeniach SQLite, ale kończą się powodzeniem; nowa ścieżka
  Oczu nie używa tej bazy.

## Animowane emotki Last Z — 28.10.2026

- Rozpoznano kolejny typ fałszywej wiadomości: ilustrowane, gotowe emotki
  Last Z — na przykład teksty zawierające `fire buddy`, `flame tamer` lub
  `fight your flames`. Są wyświetlane po stronie rozmówcy, ale nie są jego
  zwykłą wypowiedzią i PEva nie powinna na nie odpowiadać.
- Oczy łączą bliskie linie jednego lewego dymka i oznaczają tylko te
  charakterystyczne frazy jako animowaną emotkę. Zwykła rozmowa o ogniu nie
  jest blokowana. Gdy emotka jest najnowszym elementem, PEva nie wraca już do
  wcześniejszego `hello` ani nie generuje powtórnej odpowiedzi.
- Dla starszego odczytu bez geometrii istnieje ostrożny filtr awaryjny tych
  samych charakterystycznych fraz. Nie ma tu wywołania modelu ani akcji.
- Weryfikacja: **176/176 OK** w `testy/`; **247/247 OK** w `tests/`.

## Odświeżanie kropek Prywatnego czatu — 28.10.2026

- Dodano wąskie, tylko-odczytowe odświeżenie dla błędu interfejsu Last Z,
  w którym czerwona odznaka zakładki istnieje, lecz wiersz rozmowy na liście
  nie dostaje jeszcze kropki. PEva dotyka wtedy tylko dwóch górnych zakładek:
  `Friend Post`, a po 0,35 s `Private Chat`; następny zwykły skan musi nadal
  potwierdzić dokładnie jeden oznaczony wiersz, zanim otworzy rozmowę.
- Bez odznaki odświeżenie może nastąpić dopiero po 45 sekundach nieruchomej,
  już widocznej listy i najwyżej raz na 90 sekund. Nie wychodzi z otwartego
  PM, nie klika w pole tekstu ani nie wysyła wiadomości. Android nie dostarcza
  wiarygodnego sygnału aktywności dotyku użytkownika, więc definicja
  „bezczynności” jest jawnie ograniczona do stabilnej listy prywatnych czatów.
- Weryfikacja: **178/178 OK** w `testy/`; **247/247 OK** w `tests/`.

## Ograniczona automatyczna odpowiedź PM — 28.10.2026

- Na wyraźne życzenie użytkownika dodano pierwszy, bardzo wąski kanał Rąk.
  Nie korzysta on ze starego `adb_controll`, logcat ani UID rozmówcy i nie
  potrafi wybierać kontaktu z listy.
- Kanał może działać wyłącznie po dwóch osobnych zgodach: osoba ma ręcznie
  ustawiony, stały alias i przy jej karcie zaznaczono automatyczne odpowiedzi;
  następnie użytkownik ręcznie wyłącza SAFE przyciskiem „Włącz odpowiedzi
  zatwierdzonym osobom”. Domyślnie PEva dalej startuje w SAFE.
- Przed wpisaniem wiadomości Ręce zlecają nowy, jednorazowy OCR nagłówka i
  wymagają dokładnego aliasu aktualnie otwartego PM. Po kliknięciu Send zlecają
  jeszcze jeden OCR i uznają wynik tylko po odnalezieniu własnego prawego
  dymka z wygenerowanym tekstem. Niepowodzenie przed wpisaniem nie dotyka gry;
  niepewność po wpisaniu lub kliknięciu zatrzymuje SAFE i nigdy nie uruchamia
  automatycznej ponownej próby.
- Wiadomość jest ograniczona do jednej linii i 280 znaków. Brak ścieżki do
  World, Alliance, prośby o znajomość, zmiany aliasu ani endpointu panelu,
  który mógłby przyjąć dowolny tekst do wysyłki.
- Dodano kontrolne testy transportu: odmowa bez właściwego nagłówka, dokładny
  alias, ograniczenie długości oraz zatrzymanie po niepotwierdzonym Send.

## Prywatny nagłówek bez VIP/HQ — 28.10.2026

- Poprawiono rozpoznawanie nazwy rozmówcy. Linie nad dymkami Last Z, takie jak
  `V8 LV.27 ♂ DOKTORB`, nie są już traktowane jako pełna nazwa osoby:
  `V8` to VIP, `LV.27` to HQ, a symbol płci jest metadanymi. Parser zachowuje
  wyłącznie `DOKTORB` (oraz opcjonalny tag sojuszu).
- Androidowy PEva Helper najpierw szuka zwykłego, wycentrowanego tytułu na
  górze otwartego prywatnego czatu. Nie używa już nagłówka pojedynczego dymka
  jako tożsamości rozmowy. Jeśli górnego tytułu nie da się odczytać, zwraca
  brak tożsamości, a PEva czeka — nie zgaduje odbiorcy ani nie odpowiada.
- Zbudowano i zainstalowano `PEva Helper 0.9-peva-helper-private-title`
  (`versionCode=9`) jako aktualizację istniejącej aplikacji. Użyto tego samego
  lokalnego klucza debug co wersja 0.8, więc aktualizacja nie wymagała
  odinstalowania Helpera ani utraty jego lokalnego parowania.
- Weryfikacja po zmianie: **188/188 OK** w `testy/` oraz **247/247 OK** w
  zachowanym `tests/`. Historyczny zestaw nadal wypisuje znane ostrzeżenia o
  zamknięciu połączeń SQLite, ale kończy się kodem 0.

## Kontroler rozmowy i ochrona ręcznego szkicu — 28.10.2026

- Oczy zbierają teraz krótką serię nowych wiadomości przez **2 sekundy ciszy**
  (z limitem 6 sekund), zanim Mózg wygeneruje odpowiedź. Dzięki temu `hey`
  wysłane tuż przed właściwym pytaniem nie dostaje osobnej, przerywającej
  odpowiedzi.
- Nowy, ulotny kontroler rozmowy zapamiętuje jedynie hash ostatnio obsłużonej
  kombinacji nagłówka, historii i najnowszej wiadomości. Ten sam widok OCR nie
  może wygenerować drugiej odpowiedzi; nowa wiadomość nadal może przejść od
  razu. Tekst nie jest zapisywany na dysku.
- PEva Helper **1.0-peva-helper-draft-guard** (`versionCode=10`) został
  zbudowany i zainstalowany. Przy automatycznej odpowiedzi ten sam jednorazowy
  OCR obejmuje dolne pole pisania. Brak pewnego odczytu albo dowolny tekst
  inny niż pusty placeholder blokuje PEvę przed tapnięciem pola.
- Potwierdzenie wysłania porównuje teraz geometrię własnych prawych dymków
  przed i po Send. Stary identyczny dymek nie wystarczy jako dowód nowej
  wysyłki; wątpliwość nadal kończy się SAFE bez ponawiania.
- Weryfikacja po zmianie: **195/195 OK** w `testy/` oraz **247/247 OK** w
  zachowanym `tests/`. Historyczny zestaw nadal wypisuje ostrzeżenia o
  niezamkniętych połączeniach SQLite, ale kończy się powodzeniem.

