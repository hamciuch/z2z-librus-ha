Z2Z Librus v0.3.11 – wywiadówki w terminarzu z godziną, naprawa sortowania

Problem:
1. Filtr szumu mógł ukryć wydarzenie (np. wywiadówkę), gdy w jego widocznym
   tekście trafiła się fraza z listy szumu, np. „Nauczyciel:”.
2. Wydarzenia z terminarza z linią „Czas: 17:00 - 18:00” pokazywały się jako
   całodniowe, a librus-apix odczytywał godzinę jako numer lekcji (17).
3. Kalendarz „Wydarzenia szkolne” mógł zgłosić błąd sortowania, gdy w jednym
   zakresie dat były wydarzenia całodniowe i wydarzenia z godziną
   (np. kartkówka przypisana do lekcji + wycieczka).

Poprawka:
- wpisy ze słowami: zebranie, wywiadówka, rodzic(e), konsultacje nigdy nie są
  odfiltrowywane jako szum,
- godzina z „Czas: HH:MM - HH:MM” jest używana jako czas wydarzenia
  (sam początek = 1 godzina); wydarzenia trwające 6 godzin lub dłużej
  (np. wycieczka 08:00-16:00) zostają całodniowe,
- tytuł wydarzenia pomija linie „Czas:” i „Nauczyciel:”,
- sortowanie wydarzeń całodniowych i z godziną działa poprawnie,
- kartkówki i klasówki nadal są dopasowywane do lekcji jak w v0.3.9,
- odwołane lekcje (v0.3.10) działają bez zmian.

Ograniczenie: gdy Librus podaje godzinę w osobnej linii obok pogrubionego
tytułu, biblioteka librus-apix pomija tę linię – wydarzenie pokaże się wtedy
jako całodniowe (nadal widoczne).

Zmienione pliki:
custom_components/z2z_librus/api.py
custom_components/z2z_librus/calendar.py
custom_components/z2z_librus/manifest.json

Po aktualizacji zrestartuj Home Assistant.
