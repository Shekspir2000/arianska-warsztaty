#!/usr/bin/env python3
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import datetime as dt
import hashlib
import html
import hmac
import mimetypes
import os
import re
import secrets
import smtplib
import sqlite3
import sys
from email.message import EmailMessage


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "registrations.sqlite3"
EVENT_NAME = "Malowanie przy winie"
EVENT_DATE = "12 czerwca"
EVENT_TIME = "18:30"
EVENT_PLACE = "Ariańska 18/1, 31-505 Kraków"
PRICE_PLN = 249
LIMIT_PLACES = 8
WORKSHOPS = [
    {
        "id": "2026-08-21-wino",
        "date": "21 sierpnia",
        "raw_date": "21.08",
        "time": "18:30",
        "title": "Malowanie przy winie",
    },
    {
        "id": "2026-09-11-kawa",
        "date": "11 września",
        "raw_date": "11.09",
        "time": "10:30",
        "title": "Malowanie z kawą",
    },
    {
        "id": "2026-09-25-wino",
        "date": "25 września",
        "raw_date": "25.09",
        "time": "18:30",
        "title": "Malowanie przy winie",
    },
    {
        "id": "2026-10-09-kawa",
        "date": "9 października",
        "raw_date": "09.10",
        "time": "10:30",
        "title": "Malowanie z kawą",
    },
    {
        "id": "2026-10-23-wino",
        "date": "23 października",
        "raw_date": "23.10",
        "time": "18:30",
        "title": "Malowanie przy winie",
    },
    {
        "id": "2026-11-20-wino",
        "date": "20 listopada",
        "raw_date": "20.11",
        "time": "18:30",
        "title": "Malowanie przy winie",
    },
    {
        "id": "2026-12-11-wino",
        "date": "11 grudnia",
        "raw_date": "11.12",
        "time": "18:30",
        "title": "Malowanie przy winie",
    },
]
STATUSES = {
    "pending": "Oczekuje na płatność",
    "paid": "Opłacone",
    "cancelled": "Anulowane",
}
SESSIONS = set()


def load_env_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


def setting(name, default=""):
    return os.environ.get(name, default)


def blik_phone():
    return setting("BLIK_PHONE", "TUTAJ_WPISZ_NUMER")


def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                message TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                paid_at TEXT
            )
            """
        )
        columns = [row[1] for row in conn.execute("PRAGMA table_info(registrations)")]
        if "workshop_id" not in columns:
            conn.execute(
                "ALTER TABLE registrations ADD COLUMN workshop_id TEXT NOT NULL DEFAULT '2026-08-21-wino'"
            )
        conn.commit()


def db_rows(query, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchall()


def db_one(query, params=()):
    rows = db_rows(query, params)
    return rows[0] if rows else None


def workshop_by_id(workshop_id, fallback=None):
    for workshop in WORKSHOPS:
        if workshop["id"] == workshop_id:
            return workshop
    return fallback if fallback is not None else WORKSHOPS[0]


def workshop_label(workshop):
    return f'{workshop["title"]} - {workshop["date"]}, {workshop["time"]}'


def workshop_start(workshop):
    return dt.datetime.fromisoformat(f'{workshop["id"][:10]} {workshop["time"]}')


def nearest_workshop():
    now = dt.datetime.now()
    upcoming = [workshop for workshop in WORKSHOPS if workshop_start(workshop) >= now]
    return upcoming[0] if upcoming else WORKSHOPS[-1]


def paid_count(workshop_id=None):
    return registration_count("paid", workshop_id)


def registration_count(status, workshop_id=None):
    if workshop_id:
        row = db_one(
            "SELECT COUNT(*) AS count FROM registrations WHERE status = ? AND workshop_id = ?",
            (status, workshop_id),
        )
    else:
        row = db_one("SELECT COUNT(*) AS count FROM registrations WHERE status = ?", (status,))
    return int(row["count"])


def remaining_places(workshop_id=None):
    return max(0, LIMIT_PLACES - paid_count(workshop_id))


def send_email(to_address, subject, body):
    sender = setting("MAIL_FROM", "rezerwacje@arianska-selection.local")
    smtp_host = setting("SMTP_HOST")
    smtp_port = int(setting("SMTP_PORT", "587"))
    smtp_user = setting("SMTP_USER")
    smtp_password = setting("SMTP_PASSWORD")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    if smtp_host:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if smtp_user and smtp_password:
                smtp.login(smtp_user, smtp_password)
            smtp.send_message(message)
        return

    log_path = BASE_DIR / "email_outbox.log"
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n" + "=" * 72 + "\n")
        log.write(f"{now_iso()} | TO: {to_address} | SUBJECT: {subject}\n\n{body}\n")


def signup_email(name, workshop):
    return (
        f"Dziękujemy za zapis na warsztaty {workshop_label(workshop)}. "
        f"Aby potwierdzić udział, opłać {PRICE_PLN} zł BLIK-iem na numer telefonu: {blik_phone()}. "
        f"W tytule wpisz swoje imię i nazwisko. "
        f"Po zaksięgowaniu płatności otrzymasz potwierdzenie rezerwacji."
    )


def paid_email(workshop):
    return (
        f"Potwierdzamy otrzymanie płatności. Twoje miejsce na warsztatach "
        f"{workshop_label(workshop)} zostało zarezerwowane. "
        f"Do zobaczenia przy Ariańskiej 18/1 w Krakowie."
    )


def render_workshops_schedule(selected_workshop_id):
    cards = []
    for workshop in WORKSHOPS:
        remaining = remaining_places(workshop["id"])
        is_selected = workshop["id"] == selected_workshop_id
        full = remaining <= 0
        selected_class = " is-selected" if is_selected else ""
        full_class = " is-full" if full else ""
        button_text = "Brak miejsc" if full else "Zarezerwuj"
        href = f'/?workshop={workshop["id"]}#reservation'
        disabled = ' aria-disabled="true"' if full else ""
        aria_current = ' aria-current="true"' if is_selected else ""
        cards.append(
            f"""
            <article class="workshop-card{selected_class}{full_class}">
              <div class="workshop-date">
                <span>{html.escape(workshop["raw_date"])}</span>
                <strong>{html.escape(workshop["time"])}</strong>
              </div>
              <div class="workshop-info">
                <h3>{html.escape(workshop["title"])}</h3>
                <p>{html.escape(workshop["date"])} · {html.escape(EVENT_PLACE)}</p>
                <small>Pozostało miejsc: {remaining} z {LIMIT_PLACES}</small>
              </div>
              <a class="button workshop-button" href="{href}"{disabled}{aria_current}>{button_text}</a>
            </article>
            """
        )
    return "".join(cards)


def render_index(selected_workshop_id=None):
    selected_workshop = workshop_by_id(selected_workshop_id, nearest_workshop())
    source = (BASE_DIR / "index.html").read_text(encoding="utf-8")
    remaining = remaining_places(selected_workshop["id"])
    form_state_class = " is-full" if remaining <= 0 else ""
    form_disabled = "disabled" if remaining <= 0 else ""
    source = re.sub(
        r"<!-- workshops_schedule:start -->.*?<!-- workshops_schedule:end -->",
        f"<!-- workshops_schedule:start -->{render_workshops_schedule(selected_workshop['id'])}<!-- workshops_schedule:end -->",
        source,
        flags=re.DOTALL,
    )
    source = re.sub(
        r'<span class="event-pill" data-nearest-workshop>.*?</span>',
        f'<span class="event-pill" data-nearest-workshop>Najbliższy: {html.escape(nearest_workshop()["date"])}, {html.escape(nearest_workshop()["time"])}</span>',
        source,
        flags=re.DOTALL,
    )
    source = re.sub(
        r'<div class="container reservation-layout[^"]*" data-reservation-layout>',
        f'<div class="container reservation-layout{form_state_class}" data-reservation-layout>',
        source,
    )
    source = re.sub(
        r'<strong data-selected-workshop>.*?</strong>',
        f'<strong data-selected-workshop>{html.escape(workshop_label(selected_workshop))}</strong>',
        source,
        flags=re.DOTALL,
    )
    source = re.sub(
        r'<div class="places-counter" data-places-counter aria-live="polite">.*?</div>',
        f'<div class="places-counter" data-places-counter aria-live="polite">Pozostało miejsc: <strong>{remaining} z {LIMIT_PLACES}</strong></div>',
        source,
        flags=re.DOTALL,
    )
    source = re.sub(
        r'<input type="hidden" name="workshop_id" data-workshop-id value="[^"]*">',
        f'<input type="hidden" name="workshop_id" data-workshop-id value="{html.escape(selected_workshop["id"])}">',
        source,
    )
    source = re.sub(
        r'<strong data-blik-phone>.*?</strong>',
        f'<strong data-blik-phone>{html.escape(blik_phone())}</strong>',
        source,
        flags=re.DOTALL,
    )
    if form_disabled:
        source = source.replace("data-form-control>", "data-form-control disabled>")
        source = source.replace("data-form-submit>", "data-form-submit disabled>")
    replacements = {
        "{{remaining_places}}": str(remaining),
        "{{limit_places}}": str(LIMIT_PLACES),
        "{{blik_phone}}": html.escape(blik_phone()),
        "{{selected_workshop_id}}": html.escape(selected_workshop["id"]),
        "{{selected_workshop_label}}": html.escape(workshop_label(selected_workshop)),
        "{{selected_workshop_date}}": html.escape(selected_workshop["date"]),
        "{{selected_workshop_time}}": html.escape(selected_workshop["time"]),
        "{{selected_workshop_title}}": html.escape(selected_workshop["title"]),
        "{{form_state_class}}": form_state_class.strip(),
        "{{form_disabled}}": form_disabled,
    }
    for key, value in replacements.items():
        source = source.replace(key, value)
    return source


def render_rental():
    return (BASE_DIR / "wynajem.html").read_text(encoding="utf-8")


def render_page(title, content):
    return f"""<!doctype html>
<html lang="pl">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(title)} | Ariańska Selection</title>
    <link rel="stylesheet" href="/styles.css">
  </head>
  <body class="plain-page">
    {content}
  </body>
</html>"""


def render_thanks(name, workshop):
    safe_name = html.escape(name)
    content = f"""
    <main class="message-screen">
      <section class="message-panel">
        <p class="section-kicker">Rezerwacja przyjęta</p>
        <h1>Dziękujemy, {safe_name}</h1>
        <p>
          Dziękujemy za zapis na <strong>{html.escape(workshop_label(workshop))}</strong>.
          Aby potwierdzić rezerwację, opłać udział
          BLIK-iem na numer telefonu: <strong>{html.escape(blik_phone())}</strong>.
          Kwota: <strong>{PRICE_PLN} zł</strong>. W tytule przelewu wpisz swoje imię
          i nazwisko. Po otrzymaniu płatności wyślemy potwierdzenie rezerwacji na adres e-mail.
        </p>
        <a class="button button-primary" href="/">Wróć na stronę</a>
      </section>
    </main>
    """
    return render_page("Dziękujemy za zapis", content)


def render_login(error=""):
    error_html = f'<p class="form-error">{html.escape(error)}</p>' if error else ""
    content = f"""
    <main class="admin-shell login-shell">
      <form class="admin-login" method="post" action="/admin/login">
        <p class="section-kicker">Panel administracyjny</p>
        <h1>Logowanie</h1>
        {error_html}
        <label>
          Login
          <input name="login" autocomplete="username" required>
        </label>
        <label>
          Hasło
          <input type="password" name="password" autocomplete="current-password" required>
        </label>
        <button class="button button-primary" type="submit">Zaloguj</button>
      </form>
    </main>
    """
    return render_page("Logowanie", content)


def render_admin(message=""):
    rows = db_rows("SELECT * FROM registrations ORDER BY created_at DESC, id DESC")
    current_nearest = nearest_workshop()
    paid = paid_count()
    pending = registration_count("pending")
    nearest_remaining = remaining_places(current_nearest["id"])
    notice = f'<p class="admin-notice">{html.escape(message)}</p>' if message else ""
    workshop_items = []
    for workshop in WORKSHOPS:
        workshop_paid = paid_count(workshop["id"])
        workshop_pending = registration_count("pending", workshop["id"])
        workshop_cancelled = registration_count("cancelled", workshop["id"])
        workshop_remaining = remaining_places(workshop["id"])
        is_nearest = workshop["id"] == current_nearest["id"]
        badge = '<span class="admin-workshop-badge">Najbliższy</span>' if is_nearest else ""
        full_class = " is-full" if workshop_remaining <= 0 else ""
        workshop_items.append(
            f"""
            <article class="admin-workshop-card{full_class}">
              <div>
                <div class="admin-workshop-title">
                  <h2>{html.escape(workshop["title"])}</h2>
                  {badge}
                </div>
                <p>{html.escape(workshop["date"])} · {html.escape(workshop["time"])}</p>
              </div>
              <dl>
                <div><dt>Oczekuje</dt><dd>{workshop_pending}</dd></div>
                <div><dt>Opłacone</dt><dd>{workshop_paid}</dd></div>
                <div><dt>Wolne</dt><dd>{workshop_remaining}</dd></div>
                <div><dt>Anulowane</dt><dd>{workshop_cancelled}</dd></div>
              </dl>
              <a class="admin-action ghost" href="/?workshop={html.escape(workshop["id"])}#reservation">Otwórz zapis</a>
            </article>
            """
        )
    items = []
    for row in rows:
        status_key = row["status"]
        workshop = workshop_by_id(row["workshop_id"])
        paid_disabled = "disabled" if status_key == "paid" or remaining_places(workshop["id"]) <= 0 else ""
        cancel_disabled = "disabled" if status_key == "cancelled" else ""
        message_text = html.escape(row["message"] or "")
        paid_at = row["paid_at"] or "-"
        items.append(
            f"""
            <article class="submission status-{html.escape(status_key)}">
              <div class="submission-main">
                <p class="submission-date">{html.escape(row["created_at"])}</p>
                <h2>{html.escape(row["name"])}</h2>
                <p><strong>{html.escape(workshop_label(workshop))}</strong></p>
                <p>{html.escape(row["email"])} · {html.escape(row["phone"])}</p>
                <p class="submission-message">{message_text or "Brak wiadomości"}</p>
              </div>
              <div class="submission-side">
                <span class="status-pill">{STATUSES.get(status_key, status_key)}</span>
                <small>Opłacono: {html.escape(paid_at)}</small>
                <form method="post" action="/admin/submissions/{row['id']}/paid">
                  <button class="admin-action" type="submit" {paid_disabled}>Oznacz jako opłacone</button>
                </form>
                <form method="post" action="/admin/submissions/{row['id']}/cancel">
                  <button class="admin-action ghost" type="submit" {cancel_disabled}>Anuluj zgłoszenie</button>
                </form>
              </div>
            </article>
            """
        )
    empty = '<p class="admin-empty">Nie ma jeszcze żadnych zgłoszeń.</p>' if not items else ""
    content = f"""
    <main class="admin-shell">
      <header class="admin-header">
        <div>
          <p class="section-kicker">Ariańska Selection</p>
          <h1>Zgłoszenia na warsztaty</h1>
        </div>
        <form method="post" action="/admin/logout">
          <button class="admin-action ghost" type="submit">Wyloguj</button>
        </form>
      </header>
      <section class="admin-stats">
        <div><span>Opłacone zgłoszenia</span><strong>{paid}</strong></div>
        <div><span>Oczekuje na płatność</span><strong>{pending}</strong></div>
        <div><span>Wolne w najbliższym terminie</span><strong>{nearest_remaining}</strong></div>
        <div><span>Cena</span><strong>{PRICE_PLN} zł</strong></div>
      </section>
      {notice}
      <section class="admin-calendar">
        <div class="admin-section-heading">
          <p class="section-kicker">Terminarz warsztatów</p>
          <h2>Kalendarz i miejsca</h2>
        </div>
        <div class="admin-workshops-list">
          {''.join(workshop_items)}
        </div>
      </section>
      <section class="submissions-list">
        <div class="admin-section-heading">
          <p class="section-kicker">Zgłoszenia</p>
          <h2>Lista rezerwacji</h2>
        </div>
        {empty}
        {''.join(items)}
      </section>
    </main>
    """
    return render_page("Panel admina", content)


def make_session():
    token = secrets.token_urlsafe(32)
    SESSIONS.add(token)
    return token


def is_admin(handler):
    raw_cookie = handler.headers.get("Cookie", "")
    jar = cookies.SimpleCookie(raw_cookie)
    token = jar.get("aria_admin")
    return bool(token and token.value in SESSIONS)


def secure_compare(a, b):
    return hmac.compare_digest(str(a), str(b))


def admin_credentials_ok(login, password):
    expected_login = setting("ADMIN_LOGIN", "admin")
    expected_password = setting("ADMIN_PASSWORD", "zmien-to-haslo")
    login_hash = hashlib.sha256(login.encode()).hexdigest()
    expected_login_hash = hashlib.sha256(expected_login.encode()).hexdigest()
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    expected_password_hash = hashlib.sha256(expected_password.encode()).hexdigest()
    return secure_compare(login_hash, expected_login_hash) and secure_compare(
        password_hash, expected_password_hash
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[0].strip() for key, values in parsed.items()}

    def send_html(self, html_source, status=200, headers=None):
        body = html_source.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def serve_file(self, path):
        full_path = (BASE_DIR / path.lstrip("/")).resolve()
        if not str(full_path).startswith(str(BASE_DIR)) or not full_path.exists() or full_path.is_dir():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
        body = full_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        if path == "/":
            query = parse_qs(parsed_url.query)
            selected_workshop_id = query.get("workshop", [nearest_workshop()["id"]])[0]
            self.send_html(render_index(selected_workshop_id))
            return
        if path == "/wynajem":
            self.send_html(render_rental())
            return
        if path == "/admin":
            if not is_admin(self):
                self.redirect("/admin/login")
                return
            self.send_html(render_admin())
            return
        if path == "/admin/login":
            self.send_html(render_login())
            return
        if path in {"/styles.css"} or path.startswith("/assets/"):
            self.serve_file(path)
            return
        self.send_error(404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/signup":
            self.handle_signup()
            return
        if path == "/admin/login":
            self.handle_login()
            return
        if path == "/admin/logout":
            self.handle_logout()
            return
        if path.startswith("/admin/submissions/"):
            self.handle_admin_action(path)
            return
        self.send_error(404)

    def handle_signup(self):
        form = self.read_form()
        workshop = workshop_by_id(form.get("workshop_id", nearest_workshop()["id"]), nearest_workshop())
        if remaining_places(workshop["id"]) <= 0:
            self.send_html(render_page("Brak miejsc", sold_out_message()), status=409)
            return
        name = form.get("name", "")
        email = form.get("email", "")
        phone = form.get("phone", "")
        consent = form.get("consent")
        message = form.get("message", "")
        if not name or not email or not phone or consent != "yes":
            self.send_html(render_page("Błąd formularza", form_error_message()), status=400)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO registrations (name, email, phone, message, workshop_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (name, email, phone, message, workshop["id"], now_iso()),
            )
            conn.commit()
        try:
            send_email(
                email,
                f"Instrukcja płatności BLIK - {workshop['title']}",
                signup_email(name, workshop),
            )
        except Exception as exc:
            print(f"Nie udało się wysłać maila do klienta: {exc}", file=sys.stderr)
        self.send_html(render_thanks(name, workshop))

    def handle_login(self):
        form = self.read_form()
        if admin_credentials_ok(form.get("login", ""), form.get("password", "")):
            token = make_session()
            self.redirect_with_cookie("/admin", token)
            return
        self.send_html(render_login("Nieprawidłowy login lub hasło."), status=401)

    def redirect_with_cookie(self, path, token):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Set-Cookie", f"aria_admin={token}; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def handle_logout(self):
        raw_cookie = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie(raw_cookie)
        token = jar.get("aria_admin")
        if token:
            SESSIONS.discard(token.value)
        self.send_response(303)
        self.send_header("Location", "/admin/login")
        self.send_header("Set-Cookie", "aria_admin=; Max-Age=0; Path=/")
        self.end_headers()

    def handle_admin_action(self, path):
        if not is_admin(self):
            self.redirect("/admin/login")
            return
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            self.send_error(404)
            return
        _, _, raw_id, action = parts
        try:
            registration_id = int(raw_id)
        except ValueError:
            self.send_error(404)
            return
        row = db_one("SELECT * FROM registrations WHERE id = ?", (registration_id,))
        if not row:
            self.send_error(404)
            return
        if action == "paid":
            workshop = workshop_by_id(row["workshop_id"])
            if row["status"] != "paid" and remaining_places(workshop["id"]) <= 0:
                self.send_html(render_admin("Limit 8 opłaconych miejsc dla tego terminu został już osiągnięty."))
                return
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE registrations SET status = 'paid', paid_at = ? WHERE id = ?",
                    (now_iso(), registration_id),
                )
                conn.commit()
            try:
                send_email(row["email"], f"Potwierdzenie rezerwacji - {workshop['title']}", paid_email(workshop))
            except Exception as exc:
                print(f"Nie udało się wysłać potwierdzenia płatności: {exc}", file=sys.stderr)
            self.redirect("/admin")
            return
        if action == "cancel":
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE registrations SET status = 'cancelled', paid_at = NULL WHERE id = ?",
                    (registration_id,),
                )
                conn.commit()
            self.redirect("/admin")
            return
        self.send_error(404)


def sold_out_message():
    return """
    <main class="message-screen">
      <section class="message-panel">
        <p class="section-kicker">Lista zamknięta</p>
        <h1>Brak wolnych miejsc</h1>
        <p>
          Wszystkie miejsca na warsztaty zostały już opłacone. Zostaw nam wiadomość,
          jeśli chcesz otrzymać informację o kolejnej edycji.
        </p>
        <a class="button button-primary" href="/">Wróć na stronę</a>
      </section>
    </main>
    """


def form_error_message():
    return """
    <main class="message-screen">
      <section class="message-panel">
        <p class="section-kicker">Uzupełnij formularz</p>
        <h1>Nie mogliśmy zapisać zgłoszenia</h1>
        <p>Sprawdź imię i nazwisko, e-mail, telefon oraz potwierdzenie zapoznania się z zasadami rezerwacji i informacją RODO.</p>
        <a class="button button-primary" href="/#reservation">Wróć do formularza</a>
      </section>
    </main>
    """


if __name__ == "__main__":
    init_db()
    port = int(setting("PORT", "8080"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Ariańska Selection działa: http://localhost:{port}")
    print("Panel admina: http://localhost:%s/admin" % port)
    server.serve_forever()
