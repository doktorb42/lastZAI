# Last Z / PEva

PEva jest przebudowywana jako jedna lokalna aplikacja: Model, Oczy, Mózg,
Charakter, Emocje i globalny SAFE uruchamiają się razem w jednym oknie.
Aktualna wersja potrafi załadować lokalny model, oszczędnie czuwać nad
prywatnym czatem oraz ma bardzo wąskie Ręce: mogą odpowiedzieć tylko w już
otwartym PM do ręcznie zatwierdzonego aliasu, dopiero po wyłączeniu SAFE przez
użytkownika.

Nie wiesz, gdzie czego szukać? Zobacz krótką [mapę projektu](PROJECT_MAP.md)
oraz [indeks dokumentacji](docs/INDEX.md).

## Uruchamianie — jeden klik

Uruchom [PEva.cmd](PEva.cmd) dwuklikiem. Otworzy się zwykłe okno PEva; jednym
przyciskiem uruchomisz model i Oczy. Model ładuje się tylko po tym kliknięciu,
a przy zamknięciu PEva zatrzymuje wyłącznie własny serwer modelu oraz czuwanie.

PEva zaczyna zawsze w `SAFE`. Lokalny panel pokazuje stan modelu, Oczu,
rozmowy i ograniczonej ścieżki automatycznej odpowiedzi. Szczegóły aktualnej przebudowy są w
[postępie](docs/PROGRESS.md) i [architekturze](docs/ARCHITECTURE.md).

## Kontrola działania

W oknie PEva widać trzy proste statusy: model, Oczy i SAFE. Gdy Oczy nie mają
pewnego nagłówka otwartego PM, PEva wybiera `WAIT` zamiast zgadywać rozmówcę.
To celowe: nazwa nie jest UID, więc nie może później wskazać odbiorcy akcji.

## Charakter, emocje i pamięć

Nowy runtime przekazuje do Mózgu stały charakter PEva oraz jej chwilowy stan
emocji. Profile relacji, zatwierdzone wspomnienia i inicjowanie rozmów są
zachowane jako osobne elementy projektu, lecz **nie są jeszcze automatycznie
dołączane**. Najpierw potrzebny jest niezależnie potwierdzony UID rozmówcy;
sam nick z OCR nie wystarcza i nie jest kluczem do pamięci.

W tej fazie model pozostaje podglądem dla każdej niezatwierdzonej osoby. Dla
osobno zatwierdzonego aliasu możliwy jest wyłącznie ograniczony pilotaż
odpowiedzi PM; nie ma automatycznej inicjatywy. Szczegóły planowanych elementów znajdują się w
[humanizacji](docs/PEVA_HUMANIZATION.md) i [polityce inicjowania](docs/PEVA_SOCIAL_INITIATIVE.md).

## Odbiór czatu po aktualizacji gry

Starszy mechanizm capture i patchowania pozostaje zachowany do audytu oraz
powrotu, ale nie jest uruchamiany przez nowy start PEva. Dotychczasowy
`main.py` został zachowany jako `archive/legacy_runtime/main_legacy.py`.
Nie usuwamy go przed zakończeniem testów Rąk.

Dane runtimeu pozostają w `data/`: lokalna historia rozmów, diagnostyka oraz
źródła treningowe. Nie musisz ich otwierać, aby uruchomić PEva.

## Trening i modele

Zwykły start używa wyłącznie modelu wskazanego jako **current** w
[`trening/`](trening/README.md). Kandydat testowy nie uruchamia się sam i nie
może zostać modelem PEva bez testów oraz świadomej decyzji. Surowe dane,
benchmarki i wcześniejsze narzędzia są zachowane, ale nie zaśmiecają zwykłego
startu.

Historyczne komendy techniczne do Review Studio oraz QLoRA zostały zachowane
w [archiwum dokumentacji](docs/archive/LEGACY_TRAINING_COMMANDS.md). Nie są
potrzebne do codziennego używania aplikacji.
