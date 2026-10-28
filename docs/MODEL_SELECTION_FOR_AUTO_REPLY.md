# Wybór modelu do automatycznych odpowiedzi PEvy

Data przeglądu: 2026-10-28.

## Decyzja

**Żaden obecny model nie przeszedł jeszcze docelowej bramki jakościowej do
szerokiej automatyzacji.** To pozostaje ważniejsze niż sam fakt, że adapter był
trenowany. Na wyraźne życzenie użytkownika dostępny jest jednak bardzo wąski,
odwracalny pilotaż: jedna otwarta prywatna rozmowa, tylko z ręcznie zatwierdzonym
aliasem, kontrola nagłówka tuż przed wpisaniem i automatyczny SAFE przy braku
wizualnego potwierdzenia własnego dymka.

| Kandydat | Wynik niezależnej oceny rozmownej | Decyzja |
|---|---:|---|
| Qwen2.5-3B-Instruct base Q6_K (obecnie wybrany do podglądu) | 3/6 | tylko podgląd/test |
| `peva-voice-pilot-20261027` | 2/6 | odrzucony |
| `peva-voice-contextual-20261027` | 1/6 | odrzucony |
| `peva-voice-social-repair-20261027` | 3/6 | remis z bazą, odrzucony |
| `lastz-qwen2.5-3b-action-v1-full-Q6_K` | 24/24 dla formatu akcji | nie jest modelem rozmownym; nie używać do czatu |

W szczególności adapter `social-repair` jest wytrenowany, ale nie jest
poprawą nad bazą w realistycznych rozmowach. Przełączenie na niego dałoby
pozorny postęp, a ryzyko powtórzeń, ciszy albo nienaturalnych odpowiedzi
pozostałoby takie samo.

## Bramka przed pierwszym automatycznym wysłaniem

1. Nowy kandydat ma poprawić niezależny zestaw wieloturowych rozmów, a nie
   tylko własne dane treningowe.
2. Nie może mieć fałszywej deklaracji wykonanej akcji ani wycieku promptu.
3. Osiąga ustalony wcześniej próg jakości (co najmniej 80% na poszerzonym
   zestawie) oraz jest lepszy od bieżącej bazy.
4. Jedna osoba przechodzi ręczną ścieżkę: znajomość → unikalny alias ustawiony
   ręcznie w Last Z → osobny przycisk zgody na automatyczne odpowiedzi.
   PEva nie udaje, że zna niedostępny UID gracza.
5. Dopiero wtedy można zrobić jeden kontrolowany test dostarczenia. Brak
   pewnego potwierdzenia po wysłaniu oznacza stop, nigdy automatyczną ponowną
   próbę.

Obecna baza może działać jako lokalny **podgląd** oraz w opisanym pilotażu z
dobrowolnie zatwierdzoną osobą. Nie jest to równoznaczne z osiągnięciem progu
80% ani z pozwoleniem na rozmowy z innymi graczami.
