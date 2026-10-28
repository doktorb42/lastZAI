# PEva Agency Pilot V4 — raport końcowy

## Wynik

**V4 ukończony, bezpieczny technicznie, ale nie jest kandydatem do
automatycznego włączenia w grze.**

Powstał osobny plik testowy GGUF Q6_K oraz adapter V4.  Zamrożony benchmark
potwierdza poprawny format, bezpieczeństwo akcji i poprawę granicy
`DO_NOTHING` względem V1.  Jednak trzy prywatne, wcześniej niewykorzystane
przykłady społeczne nadal otrzymały decyzję `IGNORE` (3/3).  V4 nie rozwiązuje
samodzielnie problemu zbyt częstego milczenia.

Nie uruchamiano kolejnego pilota: byłaby to kolejna mała korekta tych samych
danych bez nowego dowodu, że zmienia zachowanie.  Model gry, konfiguracja oraz
automatyczne wysyłanie wiadomości pozostały nietknięte.

## Granice i pochodzenie danych

- Baza: `Qwen/Qwen2.5-7B-Instruct`, rewizja
  `a09a35458c702b33eeacc393d103063234e8bc28`.
- Zamrożony benchmark: `benchmarks/peva_agency_v0.json`, SHA-256
  `f11903411e2677d523e396c1e934965fa6f2da248a40aed7199b5ccf04157938`.
- V4 rozpoczęto od czystej bazy, nie od adaptera V1, V2 ani V3.
- Prywatne wiadomości wykorzystano wyłącznie do zanonimizowanego,
  offline'owego sprawdzenia.  Ich treść nie została dopisana do danych
  treningowych ani nie jest kopiowana do tego raportu.
- Pierwsza wersja danych V4 została zachowana jako dowód po tym, gdy kontrola
  wykryła dwie dosłowne kolizje z benchmarkiem.  **Nie była trenowana.**
- Użyto wyłącznie poprawionej, odseparowanej od benchmarku rewizji R2:
  `data/training/agency/synthetic/peva_agency_corrective_v4_r2.jsonl`.
  Jej SHA-256: `2c2cdf01b416a41650420fd5967926eeace7bc2984421a902fb34325d5463699`.

R2 zawiera 54 syntetyczne, ręcznie ułożone sytuacje: 36 zwykłych społecznych
odpowiedzi, 6 przykładów uziemienia w kontekście oraz 12 kontrastów ciszy,
reakcji i próśb o akcję.  Audyt danych przeszedł: brak duplikatów, brak
przecieku rodzin scenariuszy między zbiorem uczącym i walidacją, brak
kolizji z benchmarkiem, brak nieprawdziwych deklaracji akcji i błędnych UID.

## Trening V4

| Pole | Wynik |
|---|---|
| Adapter | `data/training/runs/peva-qwen2.5-7b-agency-pilot-v4/adapter` |
| Dane uczące / walidacyjne | 270 / 48 |
| Mikro-kroki / kroki optymalizatora | 270 / 17 |
| Epoki | 1 |
| Utrata: pierwsza / ostatnia / minimalna | 4.24721 / 0.79718 / 0.30853 |
| Metoda | QLoRA NF4, double quant, r=8, alpha=16, dropout=0.05 |
| Karta | RTX 3050 8 GB, BF16 |
| Twardy limit treningu | 45 minut |

Metryki treningu: `data/training/runs/peva-qwen2.5-7b-agency-pilot-v4/metrics.json`.
Proces zakończył się prawidłowo i po nim pamięć karty została zwolniona.

## Ten sam zamrożony benchmark

| Miara | BASE | V1 | V2 | V3 | V4 |
|---|---:|---:|---:|---:|---:|
| Poprawny format decyzji | 47.37% | 78.95% | **94.74%** | **94.74%** | **94.74%** |
| Poprawność reaktywna | 33.33% | 25.00% | 33.33% | 33.33% | 33.33% |
| Poprawny format proactive | 0% | 85.71% | **100%** | **100%** | **100%** |
| Poprawny UID celu proactive | N/A | 100% | **100%** | **100%** | **100%** |
| Poprawne `DO_NOTHING` | 0% | 0% | **25%** | **25%** | **25%** |
| Spójność z kontekstem | 21.05% | 31.58% | **42.11%** | **42.11%** | **42.11%** |
| Użycie pamięci | **33.33%** | **33.33%** | 0% | 0% | 0% |
| Spójność tożsamości PEva | 50% | 0% | **100%** | **100%** | **100%** |
| Niezależność od preferencji gracza | 50% | 0% | **100%** | **100%** | **100%** |
| Bezpieczeństwo deklaracji akcji | 100% | 100% | **100%** | **100%** | **100%** |
| Fałszywe deklaracje akcji | 0% | 0% | **0%** | **0%** | **0%** |
| Nieprawidłowy UID celu | N/A | 0% | **0%** | **0%** | **0%** |

Dokładny raport V4:
`data/training/agency/benchmarks/peva_agency_v0_pilot_v4_20261024T175605Z.json`.

## Odizolowany test na realnych sytuacjach

Test użył trzech wcześniejszych kandydatów w trybie tylko do odczytu, z limitem
32 tokenów odpowiedzi.  Nie użyto ADB, gry, sieci gry ani ścieżki wysyłania
wiadomości.

| Miara | Wynik V4 |
|---|---:|
| Poprawny format | 3/3 |
| Bezpieczne deklaracje akcji | 3/3 |
| Fałszywe deklaracje akcji | 0 |
| Decyzje `IGNORE` | 3/3 |

To najważniejszy wynik behawioralny: V4 pozostaje zbyt zachowawczy w normalnej
rozmowie.  Raport offline (z danymi źródłowymi ograniczonymi do lokalnego
pliku): `data/training/agency/real_situation_tests/peva_v4_real_situation_smoke_20261024T180021Z.json`.

## Testowy GGUF

Na wyraźną prośbę użytkownika adapter V4 został scalony z tą samą przypiętą
bazą i wyeksportowany jako osobny **testowy** model Q6_K:

- GGUF: `data/training/exports/peva-agency-pilot-v4-test/peva-agency-pilot-v4-test-Q6_K.gguf`
- Rozmiar: 6,254,198,784 B (około 5.82 GiB)
- SHA-256: `a0ece77943c7125260e886b44a8f8791814568fb0fd4d0a173059338b6f8f5ed`
- Raport eksportu: `data/training/exports/peva-agency-pilot-v4-test/export_report.json`

Pierwsza próba wykryła błąd przekazania ścieżki między WSL i narzędziem
Windows.  Poprawiono wyłącznie tłumaczenie ścieżki i wznowiono kompresję z
już gotowego pliku F16 — bez powtarzania treningu ani scalania.  Tymczasowe
pliki F16 i scalonego modelu zostały następnie usunięte po pomyślnej kontroli.

Krótki lokalny test ładowania GGUF zakończył się powodzeniem: model załadował
się na RTX 3050 i wygenerował odpowiedź testową z prędkością około 24.2
tokena/s.  Tryb interaktywny narzędzia został celowo zamknięty zaraz po teście;
nie działa żaden pozostawiony serwer ani proces modelu.

## Kontrole końcowe i decyzja

- Pełny zestaw testów lokalnych: **232/232 OK** w 7.931 s.
- Testy V4 danych i polityki odpowiedzi: **10/10 OK**.
- Zachowano twarde limity czasu dla treningu, benchmarku, przeglądu i
  eksportu.
- Nie zmieniono `config/models.json`, nie wybrano GGUF jako runtime, nie
  włączono automatycznego wysyłania i nie wykonano żadnej akcji w Last Z.

GGUF jest poprawnym plikiem do **ręcznego, odizolowanego testowania**, ale nie
powinien być obecnie ustawiany jako model decyzyjny w grze.  Najbardziej
uzasadniony następny krok to użycie istniejącej polityki podglądu odpowiedzi
społecznych (bez auto-wysyłania) i zebranie większej, ręcznie ocenionej próbki
przed kolejnym treningiem.  To rozdziela błąd decyzji „milcz” od jakości samej
wypowiedzi i nie tworzy kolejnej pętli syntetycznych danych.
