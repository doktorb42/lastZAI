# Trening PEva — prosta mapa

Ten folder nie kopiuje modeli. Pliki wskazują ich rzeczywiste, sprawdzone
położenie, dzięki czemu nie mnożymy gigabajtów ani nazw `v1`, `v2`, `v3` w
zwykłym widoku projektu.

- `models/current/` — dokładnie jeden model wybrany obecnie przez PEva.
- `models/previous/` — jedno miejsce na poprzedni zatwierdzony model, aby
  można było szybko wrócić. Jest puste, dopóki nie ma bezpiecznego awansu.
- `candidates/` — model testowy, nigdy nie jest uruchamiany przez zwykły start.
- `datasets/` i `benchmarks/` — krótkie wskaźniki do źródeł i wyników, nie ich
  duplikaty.

Zmiana `current` lub `previous` wymaga najpierw testu, raportu oraz świadomej
decyzji. Zwykłe uruchomienie `PEva.cmd` nie korzysta z kandydatów.

`feedback_store.py` przechowuje oceny kandydatów odpowiedzi wyłącznie podczas
świadomego przeglądu danych. Nie jest częścią normalnego startu PEva.

`dataset_builder.py` tworzy małe, przefiltrowane zbiory z zatwierdzonych ocen.
Nie uruchamia treningu ani nie zmienia modelu `current`.

`private_message_corpus_summary.py` zapisuje wyłącznie anonimowe liczniki do
audytu materiału. Nie zapisuje tekstu rozmów, identyfikatorów graczy ani pokoi.

`conversation_review.py` jest świadomie ręcznym przeglądem kandydatów. Nie
uruchamia modelu, Oczu, nawigacji ani wysyłania.

`agency_store.py` przechowuje decyzje Agency oraz ich ręczne oceny. To baza
przeglądu danych, a nie wykonawca akcji w Last Z.

`agency_types.py` opisuje sprawdzalny, historyczny format decyzji używany przez
benchmarki. Nie jest kontraktem wykonawczym zwykłej PEva.

`agency_core.py` to historyczny rdzeń formatowania decyzji dla benchmarków i
warstwy zgodności. Bez modelu wybiera bezpieczne milczenie, nie wykonuje akcji.

`agency_benchmark.py` odczytuje zamrożony benchmark Agency dla ręcznej oceny
modelu. Zwykłe uruchomienie PEva nie ładuje go, modelu ani adaptera.

`agency_dataset_policy.py` decyduje, które dane mogą zostać ręcznie
zatwierdzone do treningu. Domyślnie wyklucza dane nieprzejrzane oraz deklaracje
niepotwierdzonych akcji.

`agency_dataset_audit.py` sprawdza rozdzielenie danych, liczbę przykładów i
bezpieczne granice benchmarku. Nie ładuje modelu ani nie rozpoczyna treningu.

`agency_dataset_builder.py` przygotowuje historyczny, izolowany pilot danych.
Nie jest uruchamiany przez PEva; przy zapisie odmawia nadpisania artefaktów.

`agency_corrective_dataset_v2.py`–`v4.py` zachowują małe, syntetyczne korekty
historycznych pilotów. Nie są ładowane przez zwykłą PEvę i odmawiają nadpisania
istniejących artefaktów.

`agency_dataset_builder_v2.py`–`v4.py` zachowują historyczne bramki danych i
rodzinowy podział zbiorów. Nie są uruchamiane przez PEva i nie nadpisują danych.

`agency_real_candidate_export.py` tworzy kolejkę kandydatów wyłącznie do
ręcznego przeglądu. Nigdy sam nie uznaje realnej rozmowy za przykład pozytywny.

`agency_seed_dataset.py` to edytorialne, syntetyczne źródło zestawu bazowego.
Nie czyta historii rozmów, nie ładuje modelu i nie uruchamia treningu.

`benchmark_evaluator.py` sprawdza deterministycznie benchmark prywatnego czatu.
Nie wywołuje modelu, nie otwiera gry i nie wysyła wiadomości.

`agency_review_studio.py` jest ręcznym, lokalnym przeglądem danych Agency.
Zapisuje oceny atomowo z kopią poprzedniego pliku; zwykła PEva go nie otwiera.

`agency_cleanup.py` to ręczne, idempotentne czyszczenie historycznych danych.
Testy uruchamiają je wyłącznie na izolowanych kopiach baz, nigdy na bieżących.

`train_agency_pilot_v2.py`–`v4.py` są ręcznymi runnerami historycznych QLoRA
pilotów z kontrolą zamrożonego benchmarku. Zwykła PEva nigdy ich nie uruchamia.

`agency_real_situation_test.py` jest ręcznym, offline testem adaptera na
kandydatach przeglądu. Nie importuje ADB, Last Z ani żadnej ścieżki wysyłania.

`training_dataset_audit.py` ocenia przyszły zbiór Qwen wyłącznie do odczytu.
Nie ładuje modelu, nie uruchamia treningu i nie zmienia danych źródłowych.

`train_qwen7b.py` jest zachowanym, ręcznym runnerem przyszłego QLoRA Qwen.
Uruchomienie przez dawną nazwę `train_peva_qwen7b.py` nadal działa, lecz
zwykła PEva go nie importuje ani nie może go uruchomić.

`model_benchmark.py` wykonuje wyłącznie ręczny benchmark już uruchomionego,
lokalnego modelu. Nie startuje serwera, nie zmienia modelu `current` i nie ma
dostępu do gry. Dawna nazwa `model_benchmark_peva.py` pozostaje mostkiem.

`export_databases.py` tworzy kopię wybranych lokalnych baz tylko po jawnym
wywołaniu. Czyta SQLite w trybie tylko do odczytu i po zakończeniu zamyka plik;
sam import niczego nie eksportuje. Dawna nazwa `export_peva_databases.py`
pozostaje mostkiem.

`export_agency_v4_gguf.py` jest ręcznym, oddzielnie nazwanym eksportem
testowego GGUF V4. Nie zmienia `config/models.json`, modelu `current` ani
ustawień wysyłki. Dawna nazwa w katalogu głównym pozostaje mostkiem.

`train_agency_pilot_base.py` to wspólna, historyczna baza QLoRA dla pilotów
Agency V1–V4. Właściwe V2–V4 importują ją bezpośrednio z `trening/`; dawna
nazwa `train_peva_agency_pilot.py` pozostaje wyłącznie mostkiem dla raportów i
starych instrukcji.
