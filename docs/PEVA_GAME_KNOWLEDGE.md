# Podstawowa wiedza Last Z dla PEvy

PEva ma małą, lokalną bazę notatek w `assets/knowledge/last_z_basics.json`.
Nie pobiera stron podczas gry i nie zapisuje treści prywatnych rozmów. Notatki
pojawiają się wyłącznie przy jasnym pytaniu dotyczącym gry, maksymalnie dwie
naraz, jako kontekst dla modelu — nie jako twarda prawda.

## Zasada odpowiedzi

- Ekran gry, aktywny event i raport gracza mają pierwszeństwo.
- PEva mówi naturalnie i krótko, może powiedzieć „check the requirement panel”
  zamiast wymyślać poziom albo koszt.
- PEva nigdy nie twierdzi, że wysłała pomoc, wojsko lub wykonała ruch, jeśli
  runtime nie potwierdził takiej akcji.

## Źródła i ograniczenia

Notatki zostały ręcznie sprawdzone 2026-10-28 w społecznościowych poradnikach:

- https://lastzsurvival.com/resources
- https://www.lastz.guide/basics/start-here/
- https://lastz.gg/basics

Te serwisy nie są oficjalną dokumentacją gry, a zawartość gry może się zmienić.
Dlatego w każdej podpowiedzi obowiązuje zasada „zweryfikuj w aktualnym ekranie
gry”. Aktualizacje są ręczne i wymagają testu `testy/test_game_knowledge.py`.
