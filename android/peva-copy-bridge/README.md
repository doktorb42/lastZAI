# PEva Helper

Mała aplikacja Android dla Oczy PEva. Pracuje wyłącznie lokalnie z emulatorem
BlueStacks i nie jest klientem automatyzującym Last Z.

## Co robi

- W zwykłym użyciu nie wymaga klikania: uruchomiona na komputerze PEva wykrywa
  tylko lekkie sygnały ekranu, a przy potwierdzonej nowej rozmowie zleca
  Helperowi jeden lokalny OCR widocznego czatu. Nie jest to stałe OCR ani
  nagrywanie ekranu.
- Ręczne **Copy** pozostaje wyłącznie awaryjnym narzędziem w ukrytej sekcji
  diagnostycznej, np. do odzyskania fragmentu, którego OCR nie odczytał.
- Czyta jeden widoczny ekran czatu przez lokalny OCR. OCR zwraca tekst, nagłówek
  i pozycje linii, dzięki czemu PEva rozróżnia lewy dymek gracza od prawego
  dymka PEvy.
- Przechowuje dane tylko tymczasowo, w prywatnej pamięci aplikacji. Kolejka
  Copy jest usuwana po lokalnym odbiorze przez PEvę; wynik OCR jest również
  odczytywany jednokrotnie i kasowany. Przycisk **Usuń tymczasowe dane PEva**
  czyści oba bufory bez zmieniania schowka użytkownika.

## Czego nie robi

- Nie wysyła wiadomości, nie naciska przycisków Last Z i nie uruchamia żadnych
  akcji w grze.
- Nie ma dostępu do Internetu, kontaktów ani plików użytkownika.
- Nie nagrywa ekranu i nie prowadzi stałego nasłuchu: usługa działa tylko przez
  krótki, ręcznie lub lokalnie uruchomiony odczyt i zatrzymuje się sama.

## Aliasy znajomych

Gdy użytkownik ręcznie potwierdzi znajomego i jego alias w lokalnym panelu
PEva, Helper może zwrócić prosty, wyśrodkowany tytuł PM, taki jak `DOKTORB`.
Sam Helper nie ufa jednak temu tytułowi jako nowej tożsamości: desktop PEva
akceptuje go wyłącznie wtedy, gdy dokładnie odpowiada aliasowi wcześniej
zatwierdzonemu przez użytkownika. Helper nie wysyła prośby o znajomość, nie
akceptuje zaproszeń i nie ustawia aliasów w Last Z.

Pakiet pozostaje `com.peva.copybridge`, aby aktualizacja zachowała lokalne
parowanie z runtime PEva. Nazwa widoczna w Androidzie to **PEva Helper**.
