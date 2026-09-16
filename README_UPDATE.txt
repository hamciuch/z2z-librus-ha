Z2Z Librus v0.3.5 – poprawka konfiguracji obiadu

Naprawia błąd 500 przy otwieraniu "Konfiguruj".

Przyczyna:
Poprzednia wersja ręcznie przypisywała self.config_entry w OptionsFlow.
W nowszym Home Assistant config_entry jest zarządzane przez framework.

Podmień katalog:
custom_components/z2z_librus/

Po podmianie:
1. Uruchom ponownie Home Assistant.
2. Ustawienia -> Urządzenia i usługi -> Z2Z Librus -> Konfiguruj.
3. Ustaw:
   - Pokazuj obiad w planie
   - Godzina obiadu, np. 11:45
4. Zapisz.

Opcje automatycznie przeładują integrację.
