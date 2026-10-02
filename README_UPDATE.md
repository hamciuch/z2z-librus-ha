# Z2Z Librus v0.8.1

Ważna poprawka dla kilku kont (np. dwoje dzieci):

- librus-apix domyślnie daje wszystkim klientom w procesie **jeden wspólny
  słoik ciasteczek**. Token API (`oauth_token`) jednego dziecka był więc
  wysyłany z zapytaniami drugiego. Oceny z API, oceny punktowe (WF) i uwagi
  pochodziły od tego dziecka, które ostatnio odświeżyło token. Stąd WF raz
  był, raz nie (od 0.8.0 token jest odświeżany rzadziej, więc znikał na
  stałe). Teraz każde konto ma własne ciasteczka.
- Ocena punktowa 0 pkt nie jest już pusta.
- Listy znanych ocen i uwag są po aktualizacji cicho zapamiętywane od nowa,
  więc poprawione dane nie wywołają powiadomień o starych ocenach.

Po aktualizacji zrestartuj Home Assistant (samo przeładowanie nie wystarczy,
bo stary słoik ciasteczek żyje w pamięci procesu).
