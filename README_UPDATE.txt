Z2Z Librus v0.3.10 – odwołane lekcje są przekreślone w planie lekcji

Problem:
Odwołane zajęcia widoczne w terminarzu Librusa („Odwołane zajęcia – nauczyciel
na lekcji nr: N (przedmiot)") były pomijane jako szum, więc w kalendarzu
„Plan lekcji" te lekcje wyglądały jak zwykłe.

Poprawka:
- wpisy „Odwołane zajęcia ... na lekcji nr: N" są rozpoznawane i dopasowywane
  do lekcji z planu po dacie i numerze lekcji,
- odwołana lekcja w kalendarzu „Plan lekcji" ma przekreślony tytuł
  (np. „1. Edukacja wczesnoszkolna") oraz opis „Odwołane zajęcia · nauczyciel",
- odwołana lekcja nie jest liczona jako bieżące/następne wydarzenie kalendarza,
  a sensor „Następna lekcja" ją pomija,
- lekcje oznaczone jako odwołane bezpośrednio w planie Librusa też są
  przekreślane,
- wpisy odwołań nadal nie trafiają do kalendarza „Wydarzenia szkolne",
- nowy klucz danych „cancelled_lessons" (data, numer lekcji, nauczyciel,
  przedmiot) – do użycia w automatyzacjach,
- odwołania bez numeru lekcji zachowują się jak dotychczas.

Zmienione pliki:
custom_components/z2z_librus/api.py
custom_components/z2z_librus/calendar.py
custom_components/z2z_librus/sensor.py
custom_components/z2z_librus/manifest.json

Po aktualizacji zrestartuj Home Assistant.
Wszystkie funkcje v0.3.9 pozostają bez zmian.
