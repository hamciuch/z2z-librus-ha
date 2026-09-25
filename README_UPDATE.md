# Z2Z Librus v0.4.1

Oceny: pełna obsługa symboli Librusa (`np`, `bz`, `nk`, `uł`, `nł`, `zl`, `nz`, `zw`, `uc`, `nu`, `+`, `-`, `0` …).

- Każda ocena ma nowe pola: `kind` (`grade`/`plus`/`minus`/`symbol`), `label`
  (opis z legendy, np. `np` → `nieprzygotowany`), `id`, `weight`, `counts`, `comment`.
- `counts` jest `null`, gdy Librus nie pokazuje „Licz do średniej” (wcześniej
  biblioteka zwracała wtedy fałszywe `false`).
- Sensory ocen (zbiorczy i per przedmiot) mają `symbol_counts`, `symbol_labels`,
  `numeric_count` i liczniki `<symbol>_count` dla wszystkich znanych symboli.
  Liczenie jest niewrażliwe na wielkość liter (`NP` = `np`).
- Dotychczasowe atrybuty (`values`, `plus_count`, `minus_count`, `np_count`,
  `bz_count`) działają bez zmian.

Uwaga: nowa ocena pojawia się w HA dopiero po najbliższym odświeżeniu
(domyślnie co 30 min) albo po wciśnięciu przycisku odświeżania.

Po aktualizacji zrestartuj Home Assistant.
