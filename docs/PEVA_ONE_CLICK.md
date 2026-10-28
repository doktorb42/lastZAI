# PEva — start jednym kliknięciem

## Jak uruchomić

1. Włącz BlueStacks i Last Z.
2. Otwórz folder projektu i dwukrotnie kliknij `PEva.cmd`.
3. W otwartym oknie kliknij **Uruchom PEvę**.
4. Zostaw otwarte okno PEva, gdy testujesz prywatny czat.

To wszystko. Model ładuje się tylko po kliknięciu startu, a Oczy czuwają
oszczędnie: jeden lekki skan ekranu na sekundę i OCR wyłącznie po nowym sygnale.

## Co jest bezpieczne

- Startuje zawsze z aktywnym `SAFE`.
- Ręce są odłączone, dopóki SAFE jest aktywny. Po jego ręcznym wyłączeniu
  mogą odpowiedzieć wyłącznie w aktualnie otwartym PM do dokładnego aliasu,
  którego automatyczne odpowiedzi zostały osobno zatwierdzone w panelu.
- Przed wpisaniem PEva ponownie sprawdza tytuł PM oraz to, że pole wiadomości
  jest puste. Nie dotknie Twojego ręcznie wpisanego szkicu.
- Po Send oczekuje nowego własnego prawego dymka; brak pewnego potwierdzenia
  zatrzymuje SAFE i nie wywołuje ponownej próby.
- Nie zgaduje rozmówcy z tekstu bąbelka. Bez pewnego nagłówka PM wybiera
  `WAIT`.
- Zamknięcie okna zatrzymuje czuwanie oraz tylko serwer modelu uruchomiony
  przez PEva. Test automatyczny sprawdza, że panel i runtime zamykają się przy
  tym dokładnie raz. Nie odinstalowuje gry i nie usuwa danych.

## Jak poznać, że wszystko działa

Po starcie widać proste statusy: **model**, **Oczy** oraz **SAFE**. Przycisk
„Otwórz panel” pokazuje ten sam stan w lokalnej przeglądarce, wyłącznie na tym
komputerze.

Jeśli model nie wystartuje, Oczy mogą nadal czuwać, ale PEva nie wygeneruje
podglądu. Zamknij okno po skończonym teście — nie zostawiaj modelu włączonego
na noc.

## Jeden zwykły skrót

W głównym katalogu znajduje się tylko `PEva.cmd`. To jedyny skrót do
normalnego uruchomienia. Dawne osobne skróty Oczu, OCR, testu interfejsu oraz
ręcznego serwera modelu są zachowane w `archive/legacy_launchers/`; nie są
potrzebne w codziennym użyciu i nie uruchamiają się automatycznie.
