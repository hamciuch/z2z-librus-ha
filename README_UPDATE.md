# Z2Z Librus v0.9.0

- **Bezpiecznik konta:** dane z API są używane tylko wtedy, gdy `/Me` potwierdza
  login tego wpisu (po imieniu i nazwisku, gdy loginu brak). Inne konto →
  odnowienie tokenu, a gdy nie pomaga → dane z API odrzucone, błąd w logu.
- **Oceny punktowe:** `4` zamiast `4.00`, nowe pole `points_text` (np. `4/5 pkt`).
- **Jedna funkcja budująca ocenę** dla strony ocen, API i ocen punktowych - te
  same pola niezależnie od źródła; nowe pole `source` także dla strony (`page`).
- **Ostatnie dane z API zapisywane w `.storage`** - oceny punktowe i uwagi nie
  znikają, gdy po restarcie HA API akurat nie działa.
- **Historia HA:** długie listy atrybutów nie trafiają do bazy (limit 16 KB).
- **Sprzątanie:** bez pól diagnostycznych `raw`, `wf_samples`, `point_samples`;
  `api_diagnostics` ma `account_check`, `point_grades` i czytelne liczniki.
- **Logi:** awaria strony ocen nie jest już opisywana jako awaria API.
- **librus-apix >= 1.5.3, < 2** (1.5.3 czyta więcej ocen opisowych).
- **Testy (pytest) i CI:** testy, hassfest i walidacja HACS przy każdym pushu.

Po aktualizacji zrestartuj Home Assistant. Lista znanych ocen jest cicho
zapamiętywana od nowa (zmiana formatu punktów), więc nie przyjdą powiadomienia
o starych ocenach.
