# Ariańska Selection - zapisy na warsztaty

Prosty system zapisów na warsztaty "Malowanie przy winie" bez klasycznej bramki płatniczej.

## Uruchomienie

1. Skopiuj `.env.example` do `.env`.
2. Ustaw `BLIK_PHONE`, `ADMIN_LOGIN` i `ADMIN_PASSWORD`.
3. Uruchom:

```bash
python3 app.py
```

Strona działa pod adresem `http://localhost:8080`.
Panel administracyjny działa pod adresem `http://localhost:8080/admin`.

## Płatności i maile

Po zapisie klient dostaje instrukcję płatności BLIK. Jeżeli skonfigurujesz `SMTP_HOST`, system wyśle wiadomość e-mail. Jeżeli SMTP nie jest ustawione, treści maili są zapisywane w pliku `email_outbox.log`, co ułatwia testy lokalne.

## Baza danych

Zgłoszenia są przechowywane w SQLite:

`data/registrations.sqlite3`

Tabela `registrations` ma pola:

`id`, `name`, `email`, `phone`, `message`, `status`, `created_at`, `paid_at`.
