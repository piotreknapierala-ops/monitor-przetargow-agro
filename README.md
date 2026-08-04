# Monitor przetargów Agro — uruchomienie

Gotowy monitor stron WWW pod kątem postępowań dotyczących m.in. obór, chlewni, kurników, indyczarni, stajni, hal, magazynów, silosów i infrastruktury ferm.

## Co działa

- skan stron wpisanych w `config/sources.csv`;
- automatyczne wyszukiwanie zakładek „Przetargi”, „Zamówienia”, „Ogłoszenia”, „BIP”;
- filtrowanie ogłoszeń niezwiązanych z budową;
- zapisywanie historii i wykrywanie zmian;
- panel w przeglądarce;
- automatyczny skan codziennie;
- ręczne uruchomienie przyciskiem w GitHub Actions;
- opcjonalny alert e-mail.

## Uruchomienie — dokładnie po kolei

## Wersja bez instalacji

Całość konfigurujesz w przeglądarce. Python i biblioteki są uruchamiane na serwerach GitHub Actions, a panel jest publikowany przez GitHub Pages. Na komputerze nie instalujesz żadnego programu.

### 1. Załóż konto GitHub

Wejdź na GitHub, utwórz konto i zaloguj się.

### 2. Utwórz repozytorium

1. Kliknij `+` w prawym górnym rogu.
2. Wybierz `New repository`.
3. Nazwa: `monitor-przetargow-agro`.
4. Wybierz `Public`.
5. Kliknij `Create repository`.

### 3. Wgraj zawartość ZIP-a

1. Rozpakuj ZIP na komputerze.
2. W utworzonym repozytorium kliknij `uploading an existing file` albo `Add file` → `Upload files`.
3. Przeciągnij **całą zawartość rozpakowanego folderu**, łącznie z folderami `.github`, `config`, `data`, `docs`.
4. Kliknij `Commit changes`.

Uwaga: Windows może ukrywać folder `.github`. Najpewniejszy sposób to otworzyć rozpakowany folder i przeciągnąć wszystkie widoczne elementy. Jeżeli `.github` się nie wgra, utwórz plik `.github/workflows/monitor.yml` ręcznie w GitHubie bez instalowania dodatkowych programów.

### 4. Włącz GitHub Pages

1. Repozytorium → `Settings`.
2. Lewa kolumna → `Pages`.
3. W `Build and deployment` ustaw `Source: GitHub Actions`.

### 5. Włącz możliwość zapisu

1. Repozytorium → `Settings`.
2. `Actions` → `General`.
3. Na dole w `Workflow permissions` zaznacz `Read and write permissions`.
4. Kliknij `Save`.

### 6. Uruchom pierwszy skan

1. Repozytorium → `Actions`.
2. Wybierz `Monitor przetargów Agro`.
3. Kliknij `Run workflow` → ponownie `Run workflow`.
4. Po zakończeniu wejdź w `Settings` → `Pages`. Tam pojawi się adres strony, zwykle:
   `https://TWOJ-LOGIN.github.io/monitor-przetargow-agro/`

## Dodawanie kolejnych jednostek

Edytuj `config/sources.csv`. Każdy nowy wiersz ma format:

```csv
Nazwa jednostki,https://adres-strony.pl/przetargi/,OHZ,wielkopolskie,1
```

Jeżeli znasz tylko stronę główną, możesz podać stronę główną. Monitor spróbuje znaleźć zakładkę przetargową automatycznie.

Kolumny:

- `name` — nazwa jednostki;
- `url` — strona główna albo zakładka z przetargami;
- `category` — np. OHZ, stadnina, uczelnia, instytut;
- `region` — województwo;
- `active` — `1` oznacza aktywne źródło, `0` wyłączone.

## Alert e-mail — opcjonalnie

Dla Gmaila wymagane jest hasło aplikacji, a nie zwykłe hasło do konta.

W repozytorium przejdź: `Settings` → `Secrets and variables` → `Actions` → `New repository secret` i dodaj:

- `SMTP_USER` — adres Gmail wysyłający alert;
- `SMTP_PASS` — hasło aplikacji Gmail;
- `ALERT_TO` — adres odbierający alert.

Bez tych sekretów monitor nadal działa, tylko nie wysyła e-maili.

## Ręczne uruchomienie

`Actions` → `Monitor przetargów Agro` → `Run workflow`.

## Ważne ograniczenie wersji 1

Ta wersja monitoruje bezpośrednio wskazane strony i automatycznie odkryte podstrony. Kolejny moduł powinien pobierać również wszystkie krajowe ogłoszenia z oficjalnego API BZP/e-Zamówienia i filtrować je według zamawiającego, CPV i słów kluczowych.
