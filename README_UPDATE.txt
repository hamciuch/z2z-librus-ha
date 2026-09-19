Z2Z Librus v0.3.12 – przypomnienia z terminarza (Szczegóły wpisu)

Problem:
Wpisy typu „Przypomnienie” z terminarza Librusa (np. WF, lekcja 2:
„Wyjazd na pływalnię, ul. Gładka 18 (strój kąpielowy, ręcznik, klapki,
okulary, czepek)”) nie miały pełnych danych albo mogły zostać odfiltrowane
jako szum, gdy w ich widocznym tekście było „Nauczyciel:”.

Poprawka:
- dla wpisów terminarza integracja pobiera stronę „Szczegóły” z Librusa
  (Data, Nr lekcji, Nauczyciel, Rodzaj, Przedmiot, Opis),
- przypomnienie pojawia się w kalendarzu „Wydarzenia szkolne” jako
  „Przypomnienie – Wychowanie fizyczne”, na godzinach właściwej lekcji
  (numer lekcji ze Szczegółów), a w opisie jest pełna treść z pola „Opis”,
- opis z pola „Opis” (Szczegóły) jest używany także dla pozostałych
  wydarzeń, jeśli jest dostępny,
- wpisy oznaczone przez Librus jako „Przypomnienie” nigdy nie są
  odfiltrowywane; gdy jedynym powodem podejrzenia szumu jest fraza
  „Nauczyciel:”, o zachowaniu wpisu decyduje pole „Rodzaj” ze Szczegółów,
- strony Szczegółów są zapamiętywane na 6 godzin (nieudane pobranie na
  30 minut), a przy jednym odświeżeniu pobieranych jest najwyżej 25 nowych
  stron – nie obciąża to Librusa; najpierw pobierane są wpisy przyszłe,
- błąd pobrania Szczegółów nie psuje kalendarza – wpis pokazuje się tak jak
  dotychczas,
- kartkówki, klasówki, odwołane lekcje i wywiadówki działają bez zmian.

Zmienione pliki:
custom_components/z2z_librus/api.py
custom_components/z2z_librus/calendar.py
custom_components/z2z_librus/manifest.json

Po aktualizacji zrestartuj Home Assistant.
