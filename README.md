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

## Oceny i symbole (od 0.4.1)
Każda ocena w atrybucie `grades` ma (poza `display_value`, `date`, `category_name`, `teacher`, `semester`):
- `kind`: `grade` (1–6 z +/-), `plus`, `minus`, `symbol` (np. `np`, `bz`), `empty`;
- `label`: opis symbolu z legendy Librusa (np. `np` → `nieprzygotowany`), dla zwykłych ocen `null`;
- `id`: numer oceny w Librusie (z linku do szczegółów), przydatny do wykrywania nowych wpisów;
- `weight`, `counts` („Licz do średniej”), `comment`: gdy szkoła je udostępnia, inaczej `null`.

Sensory „Wszystkie oceny” i „Oceny – przedmiot” mają dodatkowo:
- `symbol_counts` (np. `{"+": 2, "np": 1}`) i `symbol_labels`, liczone bez względu na wielkość liter;
- `numeric_count` (liczba zwykłych ocen);
- płaskie liczniki `plus_count`, `minus_count`, `np_count`, `bz_count`, `bk_count`, `nb_count`, `nk_count`, `uł_count`, `nł_count`, `zl_count`, `nz_count`, `zw_count`, `uc_count`, `nu_count`, `0_count`.

Nieznane symbole (własne w danej szkole) nie są gubione. Trafiają do `symbol_counts` z `label: null`.

## Powiadomienia o nowościach (od 0.5.0)
Integracja odpala zdarzenia Home Assistanta **tylko dla naprawdę nowych** elementów:
- `z2z_librus_new_grade`: dane zdarzenia to pola oceny (`subject_name`, `display_value`, `label`, `date`, `category_name`, `weight`, `comment`, …),
- `z2z_librus_new_message`: `title`, `author`, `date`, `content` (treść dla 3 najnowszych), `href`,
- `z2z_librus_new_test`: nowa kartkówka/klasówka w terminarzu (`title`, `date`, `subject`, `number`, `data`).

- `z2z_librus_new_note` (od 0.6.0): nowa uwaga, z polami `text`, `date`, `category`, `teacher`, `type` (`pozytywna` / `negatywna` / `neutralna`), `positive`, `negative`.

Każde zdarzenie ma też `student` i `entry_id`, co pozwala rozróżnić dzieci. Lista już zgłoszonych elementów jest zapisywana w `.storage`, więc **restart HA nie wysyła starych powiadomień**. Pierwsze uruchomienie po instalacji lub aktualizacji tylko zapamiętuje stan i niczego nie zgłasza. Jeśli w jednym odświeżeniu pojawi się ponad 25 „nowych” elementów, integracja uznaje to za zmianę danych po stronie Librusa, a nie nowości, i nie spamuje.

Gotowe automatyzacje są w [`examples/automations`](examples/automations). Nie wyzwalaj powiadomień zmianą stanu sensorów, bo każdy restart HA to „zmiana” z `unavailable`.

Od 0.5.0 oceny w atrybucie `grades` są ułożone chronologicznie dla wszystkich przedmiotów (najnowsza na końcu). Najnowszą ocenę zawiera też atrybut `latest` sensora „Wszystkie oceny”.

## Uwagi (od 0.6.0)
Sensor **Uwagi**: stan to liczba uwag. Atrybuty: `positive_count`, `negative_count`, `neutral_count`, `latest`, `latest_negative` i `notes` (30 najnowszych, od najnowszej). Uwagi są pobierane z API Librusa (gateway), a gdy ono nie odpowiada, ze strony `/uwagi`. Pole `source` (`api` / `html`) mówi, skąd przyszły dane.

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
