Z2Z Librus v0.3.9 – kartkówka znika po zakończeniu lekcji

Problem:
Sensor „Najbliższa kartkówka lub klasówka” wybierał wydarzenie tylko po dacie,
więc kartkówka z rana pozostawała jako najbliższa aż do północy.

Poprawka:
- dla dzisiejszej kartkówki/klasówki integracja dopasowuje numer lekcji
  do planu lekcji,
- po godzinie zakończenia tej lekcji wydarzenie wypada z danych sensora,
- sensor automatycznie przechodzi na kolejną przyszłą kartkówkę/klasówkę,
- jeśli godziny nie da się ustalić, wydarzenie pozostaje do końca dnia,
  aby nie ukryć go omyłkowo.

Wszystkie funkcje v0.3.8 pozostają bez zmian.
