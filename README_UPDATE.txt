Z2Z Librus v0.4.0 - odpowiadanie na wiadomości z Home Assistanta

Nowe (domyślnie WYŁĄCZONE - wysyła prawdziwe wiadomości do nauczycieli):
Ustawienia -> Urządzenia i usługi -> Z2Z Librus -> Konfiguruj -> zaznacz
"Włącz odpowiadanie na wiadomości i wysyłanie wiadomości". Integracja
przeładuje się sama.

Po włączeniu na urządzeniu "Librus - <uczeń>" pojawią się:
- select "Odbiorca wiadomości": "↩ Odpowiedz: nadawca - temat" (5 ostatnich
  wiadomości) lub "✉ imię i nazwisko" (dowolna osoba z listy odbiorców Librusa);
  na starcie wybrana jest pusta opcja "— wybierz odbiorcę —",
- text "Temat wiadomości" (przy odpowiedzi puste = "RE: temat"),
- text "Treść wiadomości" (max 255 znaków - limit stanu HA),
- button "Wyślij wiadomość",
- sensor "Status wysyłki" (brak / wysyłanie / wysłano / błąd / niepewne
  + atrybuty: adresat, temat, odpowiedź Librusa, zweryfikowane).

Usługi (dłuższe teksty i automatyzacje, zwracają status):
- z2z_librus.reply_message  (content, opcjonalnie message_id, title),
- z2z_librus.send_message   (recipient, title, content).

Zabezpieczenia:
- adresat odpowiedzi = nadawca dopasowany po nazwisku do listy odbiorców
  Librusa; gdy dopasowanie nie jest jednoznaczne - NIC nie jest wysyłane,
- ta sama wiadomość nie jest wysyłana dwa razy w ciągu 2 min, odstęp
  między wysyłkami min. 10 s,
- POST wysyłki nigdy nie jest ponawiany automatycznie,
- wynik jest oceniany po odpowiedzi Librusa (a nie po fladze z librus-apix,
  która zawsze mówi "błąd"); gdy odpowiedź jest niejasna, integracja sprawdza
  folder Wysłane i w razie braku pewności pokazuje status "niepewne",
- treść wiadomości nie trafia do logów.

Pierwszy test (na żywo): włącz opcję, wybierz "↩ Odpowiedz: ..." przy
wiadomości od wychowawcy, wpisz krótką treść, naciśnij "Wyślij wiadomość"
i sprawdź w Librusie folder Wysłane. Wyślij komuś, kto się nie zdziwi.
Jeśli status pokaże "niepewne" lub "błąd" - wklej mi atrybuty sensora
"Status wysyłki" (pole "message"), dostroję rozpoznawanie odpowiedzi.

Zmienione/nowe pliki:
custom_components/z2z_librus/api.py, reply.py, reply_base.py, select.py,
text.py, button.py, sensor.py, coordinator.py, __init__.py, config_flow.py,
const.py, services.yaml, strings.json, translations/pl.json, manifest.json,
README.md

Po aktualizacji zrestartuj Home Assistant.
