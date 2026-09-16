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

## Debug
```yaml
logger:
  logs:
    custom_components.z2z_librus: debug
```

Nie publikuj loginu, hasła, cookies ani pełnych odpowiedzi zawierających dane dziecka.
