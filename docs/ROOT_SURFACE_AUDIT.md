# PEva — prosty katalog główny

Katalog główny nie jest miejscem na moduły funkcji. Zawiera tylko normalny
start PEva oraz jego bezpośrednie elementy.

## Co zostaje w katalogu głównym

| Rzecz | Rola |
|---|---|
| `PEva.cmd` | Jedyny skrót uruchomieniowy. Otwiera zwykłe okno PEva. |
| `main.py` | Lekki punkt wejścia. |
| `peva_app.py` | Okno startu i zatrzymania całej PEva. |
| `peva_runtime.py` | Łączy Model, Oczy, Mózg, Charakter, Emocje i SAFE; nie wysyła wiadomości. |
| `peva_panel.py` | Lokalny panel statusu. |
| `contracts.py` | Wspólny, mały kontrakt danych nowego runtime. |
| `apply_lastz_chat_capture_patch.ps1` | Ręczna konserwacja historycznego capture; nie jest drogą startu PEva. |
| `README.md`, `PROJECT_MAP.md` | Krótka instrukcja i mapa projektu. |

Normalne użycie to wyłącznie dwuklik w `PEva.cmd`. Nie uruchamiaj pojedynczych
plików `.py` z katalogów części PEva.

## Gdzie jest właściwy kod

- `oczy/` — oszczędny odczyt ekranu, detekcja i lokalny OCR.
- `mozg/` — model lokalny, historia, pamięć, analiza intencji i kontekst.
- `charakter/`, `emocje/` — styl, relacje i tempo odpowiedzi.
- `rece/` — SAFE oraz ograniczone, tylko-odczytowe gesty wymagane przez Oczy.
- `config/` — ustawienia i prompty.
- `trening/` — benchmarki, dane, narzędzia treningowe i ręczne eksporty.
- `testy/`, `tests/` — testy automatyczne oraz ręczne diagnostyki.

## Co przeniesiono do archiwum

`archive/legacy_root_bridges/` zawiera 88 dawnych plików Pythona, które
wprowadzały stare nazwy lub ręczne narzędzia do głównego katalogu. Zostały
przeniesione bez zmiany zawartości, aby można było je odzyskać lub porównać,
ale aktywna aplikacja, narzędzia i testy nie importują ich już z katalogu
głównego.

`archive/legacy_runtime/` zawiera oddzielony stary łańcuch:

```text
Capture → PlayerLoop → ActionExecutor → ADB / BlueStacks / Last Z
```

Nowy runtime nie łączy się z tym łańcuchem: kończy się na SAFE i `Action(NONE)`.
Szczegóły granicy bezpieczeństwa są w
[LEGACY_ACTION_BOUNDARY.md](LEGACY_ACTION_BOUNDARY.md).

Historyczny silnik naprawy capture pozostał w
`archive/legacy_runtime/update_patch.py`. Ręczny skrypt PowerShell w katalogu
głównym odwołuje się do niego bezpośrednio, więc nadal może działać bez
odtwarzania dawnych plików głównych.

## Reguła na przyszłość

Nowy kod trafia do jednej z części PEva, nie do katalogu głównego. Przed
archiwizacją należy przepiąć importy, uruchomić pełne testy i dodać wpis do
`docs/MIGRATION_MANIFEST.md` oraz `docs/PROGRESS.md`.
