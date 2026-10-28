UWAGA: TEN PLIK JEST HISTORYCZNYM OPISEM TESTU CAPTURE.
Zwykły start PEva: kliknij PEva.cmd. Nie uruchamiaj opisanego niżej testu,
chyba że celowo diagnozujemy stary patch Last Z.

PEva - test nasluchu Last Z (BEZ AI)
==================================
Podmien pliki w D:\projekty\lastZAI, zachowujac podkatalog tools\patching.
Nie usuwaj baz danych, modeli, cache gry ani innych skryptow. Zrob kopie
podmienianych plikow przed instalacja. Brak plikow v1/v2: stale nazwy.

Uruchom przez dwukrotne klikniecie PEva.cmd. Nie trzeba wpisywac argumentow.
Zostaw BlueStacks wlaczony, poczekaj na mape Last Z, wyslij nowa wiadomosc
z drugiego konta, kiedy zobaczysz [TEST].

Silnik instalacji: update_patch.py. Nie ma dwoch roznych algorytmow patcha.
Adapter tools\patching\patch_active_lastz_chat_cache.py jest tylko zgodnosciowy.
Nie odinstalowuje gry, nie wysyla wiadomosci, nie uruchamia LLM.

Raporty w data\logs\:
  peva_chat_test.json         - PASS tylko po nowej wiadomosci
  peva_prestart_status.log   - sygnatura i SHA przed startem
  peva_poststart_status.log  - sygnatura i SHA po starcie
  peva_patch.log             - wynik wgrania, tylko gdy instalowano

WAZNE: nie testowano na prawdziwym BlueStacks w srodowisku ChatGPT.
Dzialanie patcha w grze wymaga potwierdzenia wyslana nowa wiadomoscia.
Instalacja moze byc niezgodna z nowa wersja Last Z. Narzedzie wtedy
powinno przerwac prace zamiast edytowac nieznany bytecode.

Silnik wymaga biblioteki UnityPy, tak jak poprzednie narzedzia projektu.
Jesli biblioteki brakuje, program zapisze blad w raporcie.
