# Z2Z Librus for Home Assistant

Eksperymentalna integracja Home Assistant/HACS dla LIBRUS Synergia korzystająca z prywatnego `gateway/api/2.0` zamiast parsera HTML.

## Cel
- wiele uczniów: każde konto Synergia dodajesz jako osobny wpis integracji i osobne urządzenie HA;
- zachowanie wartości ocen 1:1 (`5+`, `4-`, `+`, `-`, `bz`, `np` itd.);
- pełny log ocen w atrybucie `grades`;
- sensory ocen per przedmiot;
- frekwencja i zadania, jeśli moduł szkoły udostępnia endpoint;
- kalendarz zadań jako natywne `calendar.*`.

## Instalacja przez HACS
1. Wrzuć zawartość tego repo na GitHub.
2. HACS → Integrations → Custom repositories.
3. Dodaj URL repo, typ `Integration`.
4. Pobierz `Z2Z Librus`, uruchom HA ponownie.
5. Settings → Devices & services → Add integration → `Z2Z Librus`.
6. Dodaj konto każdego ucznia osobno.

## Ważne
LIBRUS nie publikuje dokumentacji tego API. Endpointy `Me`, `Grades`, `Grades/Categories/{id}`, `Grades/Comments`, `Attendances` i `HomeWorkAssignments` są prywatne i mogą się zmieniać. Logowanie także zmieniło się w marcu/kwietniu 2026. Integracja nie obchodzi CAPTCHA ani 2FA.

### Status 0.1.0
Ta wersja jest przygotowana do testu na prawdziwym koncie. Najważniejszy pierwszy test to logowanie + `Me` + `Grades`. Po zebraniu zanonimizowanego DEBUG JSON można dopiąć bez zgadywania: terminarz, pełny plan lekcji, zastępstwa, dni wolne i wszystkie warianty ocen.

## Odpowiadanie na wiadomości (od 0.4.0, domyślnie wyłączone)
Wysyła **prawdziwe wiadomości** do nauczycieli, dlatego trzeba to włączyć świadomie:
Ustawienia → Urządzenia i usługi → Z2Z Librus → Konfiguruj → „Włącz odpowiadanie na wiadomości”.

Po włączeniu pojawiają się encje na urządzeniu Librus:
- `select` **Odbiorca wiadomości** – „↩ Odpowiedz: nadawca – temat” (5 ostatnich wiadomości) albo „✉ imię i nazwisko” (dowolny odbiorca z listy Librusa); na początku jest pusta opcja „— wybierz odbiorcę —”;
- `text` **Temat wiadomości** (przy odpowiedzi puste = „RE: temat”) i **Treść wiadomości** (limit 255 znaków – to limit stanu HA);
- `button` **Wyślij wiadomość**;
- `sensor` **Status wysyłki**: `brak` / `wysyłanie` / `wysłano` / `błąd` / `niepewne`.

Usługi (dłuższe teksty, automatyzacje; zwracają odpowiedź ze statusem):
```yaml
action: z2z_librus.reply_message      # odpowiedź do nadawcy wiadomości
data:
  message_id: "1234567"               # opcjonalnie: atrybut message_id sensora „Wiadomość 1/2/3”; puste = najnowsza
  title: ""                           # opcjonalnie; puste = „RE: temat”
  content: "Dziękuję, będziemy na zebraniu."
---
action: z2z_librus.send_message       # nowa wiadomość do osoby z listy odbiorców
data:
  recipient: "Jan Nowak"
  title: "Zwolnienie z WF"
  content: "Proszę o zwolnienie ..."
```
Zabezpieczenia: adresat odpowiedzi jest ustalany po nazwisku nadawcy na liście odbiorców Librusa i **gdy nie jest jednoznaczny, nic nie jest wysyłane**; ta sama wiadomość nie zostanie wysłana dwa razy w ciągu 2 minut; odstęp między wysyłkami min. 10 s; wysyłka nie jest nigdy ponawiana automatycznie; treść wiadomości nie trafia do logów.

## Debug
```yaml
logger:
  logs:
    custom_components.z2z_librus: debug
```

Nie publikuj loginu, hasła, cookies ani pełnych odpowiedzi zawierających dane dziecka.
