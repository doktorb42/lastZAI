# PEva — architektura docelowa

To są małe diagramy, a nie jeden wielki rysunek. Każdy moduł zna tylko dane,
które dostaje i przekazuje dalej.

## Docelowy przepływ (jeszcze nieaktywny)

Ten rysunek pokazuje kierunek rozwoju. Strzałka z Rąk do gry **nie istnieje**
w obecnej wersji PEva i nie może zostać włączona zwykłym startem.

```mermaid
flowchart LR
    A[BlueStacks / Last Z] --> B[Oczy]
    B --> C[BrainInput]
    C --> D[Mózg]
    E[Charakter] --> D
    F[Emocje] --> D
    G[Pamięć] <--> D
    D --> H[Decision]
    H --> I[Ręce]
    I -. przyszłość, niepodłączone .-> J[Gra]
    K[SAFE] -. blokuje .-> I
```

## Faktycznie zbudowany rdzeń (ograniczona, zatwierdzana odpowiedź PM)

```mermaid
flowchart TD
    A[PEva.cmd] --> B[main.py]
    B --> C[Okno PEva]
    C --> D[SAFE aktywny]
    C --> E[LocalModel / llama-server]
    C --> F[Oczy: 1 skan na sekundę]
    F --> G{Nowy sygnał?}
    G -->|Nie| F
    G -->|Tak| H[Lokalny OCR: obraz jednorazowy]
    H --> I[Osobny tekst nagłówka i wiadomości]
    I --> J{Nagłówek rozmówcy potwierdzony?}
    J -->|Nie| K[WAIT: bez zgadywania]
    J -->|Tak| L{Ta sama rozmowa w całej paczce?}
    L -->|Nie lub brak nagłówka| M[Odrzuć starszą paczkę]
    L -->|Tak| N[Mózg: Charakter + Emocje + Model]
    N --> O[Decision i lokalny podgląd]
    O --> P{SAFE wyłączony i dokładny alias zatwierdzony?}
    P -->|Nie| Q[Action NONE]
    P -->|Tak| R[Oczy ponownie sprawdzają nagłówek]
    R -->|Niepewne| Q
    R -->|Dokładnie ten PM| S[Ręce: wpisanie i Send]
    S --> T{Własny prawy dymek widoczny?}
    T -->|Tak| U[Action SEND_MESSAGE potwierdzony]
    T -->|Nie| V[SAFE i brak automatycznej ponownej próby]
    R[Panel lokalny: testy] --> D
    R --> E
    R --> F
    S[Archiwum starych launcherów] -. nie jest startem .-> A
```

Nagłówek nie daje UID. Może wskazać cel automatycznej odpowiedzi tylko wtedy,
gdy odpowiada dokładnie ręcznie ustawionemu aliasowi znajomego, którego użytkownik
osobno zatwierdził w lokalnym panelu. Oczy mogą co najwyżej otworzyć jeden, wizualnie jednoznaczny
wiersz PM albo wykonać ograniczone przewinięcie środkiem potwierdzonego PM,
aby go przeczytać; nie znają wpisywania ani wysyłania tekstu. Wpisywanie należy
do osobnego adaptera Rąk i następuje dopiero po ponownej, jednorazowej kontroli
nagłówka. Brak własnego dymka po tapnięciu Send oznacza SAFE i brak retry.

## Granica nowej PEva i zachowanego starego transportu

```mermaid
flowchart LR
    subgraph NEW[Nowa PEva — jedyny zwykły start]
        A[PEva.cmd] --> B[main.py i okno PEva]
        B --> C[Oczy + Mózg + Charakter + Emocje]
        C --> D[SAFE / zgoda aliasu]
        D --> E[Podgląd Decision]
        E --> F[Action NONE albo ograniczony SEND_MESSAGE]
    end

    subgraph OLD[Zachowany stary transport — nie uruchamia się normalnie]
        G[ChatCapture: tylko odczyt logcat]
        H[PlayerLoop]
        I[ActionExecutor]
        J[adb_controll]
        K[BlueStacks / Last Z]
        G --> H --> I --> J --> K
    end

    B -. brak importu .-> G
    C -. brak importu .-> H
    F -. tylko zatwierdzony otwarty PM .-> K
```

`ChatCapture` przyjmuje obecny callback `push.chat.up.room` i historyczny
`push.chat.room`, lecz nadal znajduje się po stronie zachowanego transportu.
Nie jest częścią startu przez `PEva.cmd`. Stara ścieżka pozostaje wyłącznie do
audytu i zgodności, dopóki jej elementy nie będą osobno bezpiecznie wydzielone.

## Oczy

```mermaid
flowchart TD
    A[BlueStacks / ekran Last Z] --> A1[screen_capture: jeden PNG w pamięci]
    A1 --> B[screen_detection: widok i odznaka PM]
    B --> C{Nowy sygnał PM?}
    C -->|Nie| D[Oszczędne czuwanie]
    C -->|Odznaka zakładki bez kropki w wierszu| C1[Jedno odświeżenie: Friend Post → Private Chat]
    C1 --> C2{Dokładnie jeden wiersz oznaczony?}
    C2 -->|Tak| E
    C2 -->|Nie| G
    C -->|Jeden oznaczony wiersz| E[PrivateChatEyes: otwórz tylko ten wiersz PM]
    E --> F{Prywatna rozmowa i bąbelki?}
    F -->|Nie| G[Zatrzymanie bez zgadywania]
    F -->|Tak| H[Jednorazowy obraz do lokalnego OCR]
    H --> I[Obraz usunięty po odczycie]
    I --> J[Wycentrowany tytuł PM + tekst]
    J --> K{Górny tytuł prywatnej rozmowy odczytany?}
    K -->|Nie| L[WAIT bez tożsamości]
    K -->|Tak| M{Bąbelek ucięty u góry?}
    M -->|Tak| N[Maks. dwa przewinięcia środkiem PM]
    N --> O[OCR kolejnej strony i powrót do najnowszej]
    M -->|Nie| P[Bufor krótkiej serii]
    O --> P
    P --> Q{Nagłówek bez zmiany?}
    Q -->|Nie| R[Odrzuć starszą paczkę]
    Q -->|Tak| S[VisionResult]
    S --> T[BrainInput]
    C -->|Popup / event / kilka wierszy / nieznany ekran| G
```

Helper bierze tożsamość rozmowy wyłącznie z krótkiego, wycentrowanego tytułu
u góry otwartego PM. Linie nad dymkami (`V8`, `LV.27`, płeć, sojusz i nick)
opisują autora pojedynczej wiadomości i nie zastępują tego tytułu. Gdy tytuł
nie jest czytelny, PEva czeka bez odpowiedzi.

Pamięć może zostać dołączona do `BrainInput` dopiero przez przyszły resolver,
który ma niezależnie potwierdzony UID rozmówcy. Sama nazwa z nagłówka OCR nie
jest kluczem do bazy pamięci.

Odświeżenie zakładek jest dostępne wyłącznie, gdy lista **Prywatny czat** jest
już widoczna i przez 45 sekund nie ma nowego sygnału, albo natychmiast po
zaobserwowaniu czerwonej odznaki bez oznaczonego wiersza. PEva nie ma dostępu
do wiarygodnego systemowego sygnału dotknięcia użytkownika, dlatego nie udaje,
że zna jego aktywność; nie opuszcza też samodzielnie otwartej rozmowy.

## Mózg

```mermaid
flowchart LR
    A[BrainInput] --> B[Analiza sytuacji]
    C[Pamięć: tylko potwierdzony UID] --> B
    D[Emocje: chwilowy stan] --> B
    E[Charakter] --> B
    B --> F[Model]
    F --> G[Decision]
    G --> H[Podgląd / lokalny status]
    G --> I[Action NONE albo kontrolowany SEND_MESSAGE]
```

## Ręce

```mermaid
flowchart TD
    A[Action] --> B{SAFE aktywny?}
    B -->|Tak| C[Odrzuć akcję i zapisz status]
    B -->|Nie| D[Walidacja celu i kontekstu]
    D -->|Niepewne| C
    D -->|Dokładny zatwierdzony alias w otwartym PM| E[ADBKeyboard: wpisanie i kliknięcie Send]
    E --> F[Jednorazowe OCR: własny prawy dymek]
    F --> G[Status dla Mózgu i panelu]
```

## Proste kontrakty między modułami

```python
VisionResult(screen="PRIVATE_CONVERSATION", message="...", confidence=0.97)
BrainInput(vision=vision_result, memory=..., emotion=..., personality=...)
Decision(kind="REPLY", reason="new_private_message", text="...")
Action(kind="SEND_MESSAGE", target="verified_private_chat", text="...")
```

Sposób zdobycia tekstu — OCR, późniejszy parser UI lub inna metoda — pozostaje
wewnętrznym szczegółem Oczu. `BrainContextBuilder` tworzy z tego `BrainInput`,
który dopiero później trafi do modelu.
