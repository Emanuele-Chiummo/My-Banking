from __future__ import annotations

import json
import sqlite3
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
from io import BytesIO
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g, jsonify, send_file
)
from werkzeug.routing import BuildError
from werkzeug.security import generate_password_hash, check_password_hash

# --- Swagger ---
from flasgger import Swagger, swag_from

# --- Config ---
app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = 'cambia-questa-secret-in-prod'  # usa una env var in prod
app.config['DATABASE'] = str(Path(app.instance_path) / 'app.db')

# Swagger config minimale
app.config['SWAGGER'] = {
    'title': 'My Banking API',
    'uiversion': 3,
}
swagger = Swagger(app)

# Assicura la cartella instance/ esista
Path(app.instance_path).mkdir(parents=True, exist_ok=True)


# --- DB Helpers ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db

@app.teardown_appcontext
def close_db(_exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def exec_script(sql_text: str):
    db = get_db()
    db.executescript(sql_text)
    db.commit()


# --- Helper: ID, ricalcoli, movimenti ---
def _next_id(prefix: str, table: str, col: str) -> str:
    """Genera ID testuale incrementale, es. PIG001 → PIG002, gestendo correttamente 3/4/5+ cifre."""
    db = get_db()
    row = db.execute(
        f"""
        SELECT {col} AS id
        FROM {table}
        WHERE {col} LIKE ?
        ORDER BY LENGTH({col}) DESC, {col} DESC
        LIMIT 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    if not row:
        return f"{prefix}001"
    m = re.search(r"(\d+)$", row["id"])
    n = int(m.group(1)) + 1 if m else 1
    return f"{prefix}{n:03d}"

# --- Helpers demo utenti/conti/contatti ---
def _upsert_user(user_id: str, codice: str, first: str, last: str, pwd: str = "Password123!"):
    db = get_db()
    db.execute("""
        INSERT INTO users (user_id, codice_cliente, first_name, last_name, password_hash)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          codice_cliente = excluded.codice_cliente,
          first_name     = excluded.first_name,
          last_name      = excluded.last_name,
          password_hash  = excluded.password_hash
    """, (user_id, codice, first, last, generate_password_hash(pwd)))

def _ensure_account(account_id: str, user_id: str, iban: str, name: str, currency: str = "EUR", balance: float = 0.0):
    db = get_db()
    db.execute("""
        INSERT INTO accounts (account_id, user_id, iban, name, currency, balance)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO NOTHING
    """, (account_id, user_id, iban, name, currency, float(balance)))

def _ensure_contact_for(owner_user_id: str, target_user_id: str, target_account_id: str, display_name: str):
    """Crea una voce in rubrica (se assente) dall'owner verso il target."""
    db = get_db()
    exists = db.execute("""
        SELECT 1 FROM contacts
        WHERE owner_user_id = ? AND target_user_id = ? AND target_account_id = ?
    """, (owner_user_id, target_user_id, target_account_id)).fetchone()
    if exists:
        return
    contact_id = _next_id("CON", "contacts", "contact_id")
    db.execute("""
        INSERT INTO contacts (contact_id, owner_user_id, display_name, target_user_id, target_account_id)
        VALUES (?, ?, ?, ?, ?)
    """, (contact_id, owner_user_id, display_name, target_user_id, target_account_id))


# --- Notifiche & impostazioni utente ------------------------------------

def _ensure_notification(*, user_id: str, type_: str, title: str,
                         body: str | None = None, dedupe_key: str | None = None,
                         payload: dict | None = None) -> str:
    db = get_db()
    payload_json = json.dumps(payload or {})

    if dedupe_key:
        existing = db.execute(
            """
            SELECT notification_id, status FROM notifications
            WHERE user_id = ? AND dedupe_key = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, dedupe_key),
        ).fetchone()
        if existing:
            db.execute(
                """
                UPDATE notifications
                SET body = COALESCE(?, body),
                    payload = ?,
                    created_at = CASE WHEN status = 'UNREAD' THEN datetime('now') ELSE created_at END
                WHERE notification_id = ?
                """,
                (body, payload_json, existing["notification_id"]),
            )
            db.commit()
            return existing["notification_id"]

    notification_id = _next_id("NOT", "notifications", "notification_id")
    db.execute(
        """
        INSERT INTO notifications (notification_id, user_id, type, title, body, status, dedupe_key, payload)
        VALUES (?, ?, ?, ?, ?, 'UNREAD', ?, ?)
        """,
        (notification_id, user_id, type_, title, body, dedupe_key, payload_json),
    )
    db.commit()
    return notification_id


def _clear_notification_by_dedupe(*, user_id: str, dedupe_key: str) -> None:
    if not dedupe_key:
        return
    db = get_db()
    db.execute(
        """
        UPDATE notifications
        SET status = 'READ', read_at = datetime('now')
        WHERE user_id = ? AND dedupe_key = ? AND status = 'UNREAD'
        """,
        (user_id, dedupe_key),
    )
    db.commit()


def _list_notifications(user_id: str, limit: int = 10, status: str | None = None) -> list[sqlite3.Row]:
    db = get_db()
    params = [user_id]
    where = ["user_id = ?"]
    if status in {"UNREAD", "READ"}:
        where.append("status = ?")
        params.append(status)
    sql = f"""
        SELECT notification_id, type, title, body, status, payload, created_at, read_at
        FROM notifications
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        LIMIT ?
    """
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    result = []
    for row in rows:
        payload = row["payload"]
        try:
            payload_data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            payload_data = {"raw": payload}
        result.append({**dict(row), "payload": payload_data})
    return result


def _count_unread_notifications(user_id: str) -> int:
    db = get_db()
    row = db.execute(
        "SELECT COUNT(1) AS cnt FROM notifications WHERE user_id = ? AND status = 'UNREAD'",
        (user_id,),
    ).fetchone()
    return int(row["cnt"] or 0)


def _mark_notification(user_id: str, notification_id: str, status: str) -> bool:
    if status not in {"READ", "UNREAD"}:
        return False
    db = get_db()
    res = db.execute(
        """
        UPDATE notifications
        SET status = ?, read_at = CASE WHEN ? = 'READ' THEN datetime('now') ELSE NULL END
        WHERE notification_id = ? AND user_id = ?
        """,
        (status, status, notification_id, user_id),
    )
    db.commit()
    return res.rowcount > 0


def _ensure_user_settings(user_id: str) -> sqlite3.Row:
    db = get_db()
    settings = db.execute(
        """
        SELECT user_id, default_currency, decimal_places, notify_threshold
        FROM user_settings
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if settings:
        return settings
    db.execute(
        """
        INSERT INTO user_settings (user_id, default_currency, decimal_places, notify_threshold)
        VALUES (?, 'EUR', 2, 1.0)
        """,
        (user_id,),
    )
    db.commit()
    return db.execute(
        "SELECT user_id, default_currency, decimal_places, notify_threshold FROM user_settings WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def _get_user_settings(user_id: str) -> dict:
    settings = _ensure_user_settings(user_id)
    return dict(settings) if settings else {"user_id": user_id, "default_currency": "EUR", "decimal_places": 2, "notify_threshold": 1.0}


def _update_user_settings(user_id: str, *, default_currency: str, decimal_places: int, notify_threshold: float) -> dict:
    currency = (default_currency or "EUR").upper()
    if currency not in {"EUR", "USD", "GBP"}:
        currency = "EUR"
    try:
        decimals = max(0, min(int(decimal_places), 4))
    except (TypeError, ValueError):
        decimals = 2
    try:
        threshold = float(notify_threshold)
    except (TypeError, ValueError):
        threshold = 1.0
    if threshold <= 0:
        threshold = 1.0

    db = get_db()
    db.execute(
        """
        INSERT INTO user_settings (user_id, default_currency, decimal_places, notify_threshold)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          default_currency = excluded.default_currency,
          decimal_places   = excluded.decimal_places,
          notify_threshold = excluded.notify_threshold,
          updated_at       = datetime('now')
        """,
        (user_id, currency, decimals, threshold),
    )
    db.commit()
    return _get_user_settings(user_id)


NAV_PRIMARY_LINKS = [
    {
        "endpoint": "dashboard",
        "label": "Dashboard",
        "icon": "bi-speedometer2",
        "match": "dashboard",
    },
    {
        "endpoint": "reports",
        "label": "Report",
        "icon": "bi-graph-up",
        "match": "reports",
    },
    {
        "endpoint": "p2p",
        "label": "P2P",
        "icon": "bi-people",
        "match": "p2p",
    },
]


@app.context_processor
def inject_global_context():
    if not session.get("user_id"):
        return {
            "nav_links": [],
        }
    user_id = session["user_id"]
    settings = _get_user_settings(user_id)
    items = _list_notifications(user_id, limit=5)
    unread = sum(1 for note in items if note.get("status") == "UNREAD")
    links = []
    for item in NAV_PRIMARY_LINKS:
        try:
            link_url = url_for(item["endpoint"])
        except BuildError:
            link_url = None
        links.append({
            "label": item["label"],
            "icon": item.get("icon"),
            "endpoint": item["endpoint"],
            "match": item.get("match", item["endpoint"]),
            "url": link_url,
        })
    return {
        "user_settings": settings,
        "nav_notifications": {
            "items": items,
            "unread": unread,
        },
        "nav_links": links,
    }

@app.cli.command("seed-demo-more-users")
def seed_demo_more_users():
    """
    Crea altri utenti/conti di test e li aggiunge alla rubrica di USE001 per il P2P.
    Utenti creati/aggiornati:
      - USE002 Mario Rossi (ACC002)
      - USE003 Lucia Bianchi (ACC003)
      - USE004 Giuseppe Verdi (ACC004)
      - USE005 Chiara Neri (ACC005)
    """
    db = get_db()

    # Assicura l'utente principale (USE001) esista, servirà come "owner" rubrica
    owner = db.execute("SELECT user_id, first_name, last_name FROM users WHERE user_id='USE001'").fetchone()
    if not owner:
        _upsert_user("USE001", "123456", "Emanuele", "Chiummo")
        owner = db.execute("SELECT user_id, first_name, last_name FROM users WHERE user_id='USE001'").fetchone()
    owner_name = f"{owner['first_name']} {owner['last_name']}".strip()

    users = [
        # USE002 potrebbe già esistere (seed-demo-p2p), la upsert lo gestisce
        dict(user_id="USE002", codice="654321", first="Mario",    last="Rossi",
             acc="ACC002", iban="IT60X0542811101000000654321", acc_name="Conto Mario",    balance=500.00),
        dict(user_id="USE003", codice="111222", first="Lucia",    last="Bianchi",
             acc="ACC003", iban="IT60X0542811101000000003003", acc_name="Conto Lucia",    balance=820.00),
        dict(user_id="USE004", codice="333444", first="Giuseppe", last="Verdi",
             acc="ACC004", iban="IT60X0542811101000000004004", acc_name="Conto Giuseppe", balance=125.50),
        dict(user_id="USE005", codice="555666", first="Chiara",   last="Neri",
             acc="ACC005", iban="IT60X0542811101000000005005", acc_name="Conto Chiara",   balance=2100.00),
    ]

    # Crea/aggiorna utenti e conti
    for u in users:
        _upsert_user(u["user_id"], u["codice"], u["first"], u["last"])
        _ensure_account(u["acc"], u["user_id"], u["iban"], u["acc_name"], "EUR", u["balance"])

    # Aggiungi i nuovi utenti alla rubrica di USE001
    for u in users:
        display = f"{u['first']} {u['last']}"
        _ensure_contact_for(owner_user_id="USE001",
                            target_user_id=u["user_id"],
                            target_account_id=u["acc"],
                            display_name=display)


    acc_001 = db.execute("""
        SELECT account_id, name FROM accounts
        WHERE user_id='USE001'
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()
    if acc_001:
        for u in users:
            _ensure_contact_for(owner_user_id=u["user_id"],
                                target_user_id="USE001",
                                target_account_id=acc_001["account_id"],
                                display_name=owner_name)

    db.commit()
    print("✅ seed-demo-more-users: creati/aggiornati USE002..USE005, conti e rubrica aggiornata.")



def _get_piggy_balance(piggy_id: str) -> float:
    """Ritorna il saldo reale del salvadanaio calcolato dalle movimentazioni."""
    db = get_db()
    row = db.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN direction='TO_PIGGY' THEN amount ELSE 0 END),0) -
          COALESCE(SUM(CASE WHEN direction='FROM_PIGGY' THEN amount ELSE 0 END),0) AS tot
        FROM piggy_transfers
        WHERE piggy_id = ?
    """, (piggy_id,)).fetchone()
    return float(row["tot"] or 0.0)

def _recalc_piggy(piggy_id: str):
    """Aggiorna current_amount al valore calcolato da piggy_transfers."""
    db = get_db()
    piggy_sum = _get_piggy_balance(piggy_id)
    db.execute("UPDATE piggy_banks SET current_amount = ? WHERE piggy_id = ?", (piggy_sum, piggy_id))
    db.commit()

def _apply_account_delta(account_id: str, delta: float):
    db = get_db()
    db.execute(
        "UPDATE accounts SET balance = COALESCE(balance, 0) + ? WHERE account_id = ?",
        (float(delta), account_id)
    )

def _ensure_user_owns_piggy(user_id: str, piggy_id: str) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM piggy_banks WHERE piggy_id = ? AND user_id = ? AND status != 'DELETED'",
                     (piggy_id, user_id)).fetchone()
    return bool(row)

def _insert_piggy_transfer(*, piggy_id: str, account_id: str, amount: float,
                           direction: str, note: str | None, tx_on_account: bool, when: str | None = None):
    """Inserisce un trasferimento salvadanaio con guard-rail che impedisce saldo negativo."""
    assert direction in ("TO_PIGGY", "FROM_PIGGY")
    db = get_db()
    transfer_id = _next_id("TRP", "piggy_transfers", "transfer_id")
    when = when or date.today().isoformat()

    # --- GUARD RAIL: no saldo negativo del salvadanaio ---
    current = _get_piggy_balance(piggy_id)
    projected = current + (amount if direction == "TO_PIGGY" else -amount)
    if projected < 0:
        raise ValueError("Saldo salvadanaio insufficiente per questa operazione.")

    db.execute("""
        INSERT INTO piggy_transfers (transfer_id, piggy_id, account_id, date, amount, direction, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (transfer_id, piggy_id, account_id, when, float(amount), direction, note))
    _recalc_piggy(piggy_id)

    if tx_on_account:
        tx_id = _next_id("TRX", "transactions", "transaction_id")
        if direction == "TO_PIGGY":
            tx_amount = -abs(float(amount))  # uscita dal conto
            tx_type = "DEBIT"
            desc = "Trasferimento verso salvadanaio"
        else:
            tx_amount = abs(float(amount))   # entrata sul conto
            tx_type = "CREDIT"
            desc = "Prelievo da salvadanaio"

        cur = db.execute("""
            INSERT INTO transactions (transaction_id, account_id, piggy_id, date, description, category, type, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (tx_id, account_id, piggy_id, when, desc, "Risparmio", tx_type, tx_amount))

        if cur.rowcount:
            _apply_account_delta(account_id, tx_amount)

    db.commit()
    return transfer_id

def _ensure_contact(owner_user_id: str, contact_id: str):
    db = get_db()
    return db.execute("""
        SELECT contact_id, owner_user_id, display_name, target_user_id, target_account_id, iban
        FROM contacts
        WHERE owner_user_id = ? AND contact_id = ?
    """, (owner_user_id, contact_id)).fetchone()


def _list_split_groups(user_id: str) -> list[dict]:
    db = get_db()
    groups = db.execute(
        """
        SELECT group_id, name, created_at
        FROM split_groups
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    ).fetchall()
    if not groups:
        return []
    ids = [g["group_id"] for g in groups]
    placeholders = ",".join(["?"] * len(ids))
    members_rows = db.execute(
        f"""
        SELECT m.member_id, m.group_id, m.contact_id, m.display_name, c.target_user_id, c.target_account_id
        FROM split_group_members m
        JOIN contacts c ON c.contact_id = m.contact_id
        WHERE c.owner_user_id = ? AND m.group_id IN ({placeholders})
        ORDER BY m.created_at ASC
        """,
        (user_id, *ids),
    ).fetchall()
    members_by_group: dict[str, list[dict]] = {gid: [] for gid in ids}
    for row in members_rows:
        members_by_group[row["group_id"]].append({
            "member_id": row["member_id"],
            "contact_id": row["contact_id"],
            "display_name": row["display_name"],
            "target_user_id": row["target_user_id"],
            "target_account_id": row["target_account_id"],
        })
    return [
        {
            "group_id": g["group_id"],
            "name": g["name"],
            "created_at": g["created_at"],
            "members": members_by_group.get(g["group_id"], []),
        }
        for g in groups
    ]


def _get_split_group(user_id: str, group_id: str):
    db = get_db()
    return db.execute(
        """
        SELECT group_id, name
        FROM split_groups
        WHERE user_id = ? AND group_id = ?
        """,
        (user_id, group_id),
    ).fetchone()


def _get_split_members(user_id: str, group_id: str) -> list[sqlite3.Row]:
    db = get_db()
    return db.execute(
        """
        SELECT m.member_id, m.group_id, m.contact_id, m.display_name,
               c.target_user_id, c.target_account_id
        FROM split_group_members m
        JOIN contacts c ON c.contact_id = m.contact_id
        WHERE m.group_id = ? AND c.owner_user_id = ?
        ORDER BY m.created_at ASC
        """,
        (group_id, user_id),
    ).fetchall()


def _split_even(amount: Decimal, count: int) -> list[Decimal]:
    """Dividi amount in count quote (centesimi) restituendo una lista di Decimal."""
    if count <= 0:
        return []
    cents_total = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    base = cents_total // count
    remainder = cents_total - base * count
    shares = [base] * count
    for idx in range(remainder):
        shares[idx] += 1
    return [Decimal(s) / Decimal(100) for s in shares]

def _p2p_instant(*, from_account_id: str, to_account_id: str, amount: float,
                 message: str | None, from_user_id: str, to_user_id: str,
                 to_name: str | None = None, from_name: str | None = None):
    """Trasferimento interno istantaneo: crea 2 transazioni e aggiorna saldi."""
    if amount <= 0:
        raise ValueError("Importo non valido")
    db = get_db()

    # verifica saldo sufficiente
    bal = db.execute("SELECT balance FROM accounts WHERE account_id = ?", (from_account_id,)).fetchone()
    if not bal:
        raise ValueError("Conto mittente non trovato")
    if float(bal["balance"] or 0) < amount - 1e-9:
        raise ValueError("Saldo insufficiente")

    p2p_id = _next_id("P2P", "p2p_transfers", "p2p_id")
    today  = date.today().isoformat()

    desc_out = f"P2P a {to_name or ('utente ' + to_user_id)}"
    desc_in  = f"P2P da {from_name or ('utente ' + from_user_id)}"
    if message:
        desc_out += f" — {message}"
        desc_in  += f" — {message}"

    try:
        # Avvia transazione esplicita
        db.execute("BEGIN")

        # 1) Mittente: DEBIT (subito dopo genero e INSERISCO)
        tx_out = _next_id("TRX", "transactions", "transaction_id")
        db.execute("""
            INSERT INTO transactions (transaction_id, account_id, piggy_id, date, description, category, type, amount)
            VALUES (?, ?, NULL, ?, ?, 'P2P', 'DEBIT', ?)
        """, (tx_out, from_account_id, today, desc_out, -abs(float(amount))))
        _apply_account_delta(from_account_id, -abs(float(amount)))

        # 2) Destinatario: CREDIT (genera ID SOLO ORA, dopo il primo insert)
        tx_in = _next_id("TRX", "transactions", "transaction_id")
        db.execute("""
            INSERT INTO transactions (transaction_id, account_id, piggy_id, date, description, category, type, amount)
            VALUES (?, ?, NULL, ?, ?, 'P2P', 'CREDIT', ?)
        """, (tx_in, to_account_id, today, desc_in, abs(float(amount))))
        _apply_account_delta(to_account_id, abs(float(amount)))

        # 3) Record P2P
        db.execute("""
            INSERT INTO p2p_transfers (p2p_id, from_user_id, to_user_id, from_account_id, to_account_id, amount, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (p2p_id, from_user_id, to_user_id, from_account_id, to_account_id, float(amount), message))

        db.commit()
        return p2p_id

    except Exception:
        db.rollback()
        raise




# --- CLI: init-db & seed-demo & seed-demo-data ---
@app.cli.command("init-db")
def init_db_cmd():
    schema_path = Path("schema.sql")
    if not schema_path.exists():
        raise SystemExit("schema.sql non trovato nella root del progetto.")
    db_file = Path(app.config['DATABASE'])
    if db_file.exists():
        db_file.unlink()
    exec_script(schema_path.read_text(encoding="utf-8"))
    print("✅ Database inizializzato.")

@app.cli.command("seed-demo")
def seed_demo_cmd():
    db = get_db()
    user_id = "USE001"
    codice_cliente = "123456"
    first_name = "Emanuele"
    last_name = "Chiummo"
    password_hash = generate_password_hash("Password123!")
    db.execute("""
        INSERT INTO users (user_id, codice_cliente, first_name, last_name, password_hash)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          codice_cliente = excluded.codice_cliente,
          first_name     = excluded.first_name,
          last_name      = excluded.last_name,
          password_hash  = excluded.password_hash
    """, (user_id, codice_cliente, first_name, last_name, password_hash))
    db.commit()
    print("✅ Utente di test creato/aggiornato.")

@app.cli.command("seed-demo-data")
def seed_demo_data_cmd():
    db = get_db()
    user_id = "USE001"
    u = db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not u:
        raise SystemExit("Prima esegui: flask --app app seed-demo")

    db.execute("""
        INSERT INTO accounts (account_id, user_id, iban, name, currency, balance)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO NOTHING
    """, ("ACC001", user_id, "IT60X0542811101000000123456", "Conto Principale", "EUR", 1250.00))

    db.execute("""
        INSERT INTO piggy_banks (piggy_id, user_id, name, target_amount, current_amount, status)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(piggy_id) DO NOTHING
    """, ("PIG001", user_id, "Vacanze", 1000.00, 150.00, "ACTIVE"))

    trx = [
        ("TRX001", "ACC001", None, "2025-09-10", "Stipendio", "Entrate", "CREDIT", 1500.00),
        ("TRX002", "ACC001", None, "2025-09-11", "Spesa Supermercato", "Spesa", "DEBIT", -85.20),
        ("TRX003", "ACC001", None, "2025-09-12", "Abbonamento Netflix", "Abbonamenti", "DEBIT", -12.99),
        ("TRX004", "ACC001", "PIG001", "2025-09-13", "Trasferimento Salvadanio", "Risparmio", "DEBIT", -100.00),
        ("TRX005", "ACC001", None, "2025-09-14", "Cena fuori", "Ristoranti", "DEBIT", -52.40)
    ]
    for t in trx:
        db.execute("""
            INSERT INTO transactions (transaction_id, account_id, piggy_id, date, description, category, type, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO NOTHING
        """, t)

    transfers = [
        ("TRP001", "PIG001", "ACC001", "2025-09-13", 100.00, "TO_PIGGY", "Accantonamento mensile"),
        ("TRP002", "PIG001", "ACC001", "2025-09-15", 50.00, "FROM_PIGGY", "Imprevisto")
    ]
    for tr in transfers:
        db.execute("""
            INSERT INTO piggy_transfers (transfer_id, piggy_id, account_id, date, amount, direction, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transfer_id) DO NOTHING
        """, tr)

    db.execute("UPDATE accounts SET balance = ? WHERE account_id = 'ACC001'", (1250.00,))
    db.execute("UPDATE piggy_banks SET current_amount = ? WHERE piggy_id = 'PIG001'", (_get_piggy_balance("PIG001"),))
    db.commit()
    print("✅ Dati demo inseriti.")

# --- DEMO 12 MESI REALISTICI ---
import random
from datetime import datetime, timedelta, date as date_cls
import calendar

def _month_iter(n_back: int = 12):
    """Ritorna gli ultimi n_back mesi come (year, month), dal mese corrente indietro."""
    today = date_cls.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n_back):
        out.append((y, m))
        m -= 1
        if m == 0:
            y -= 1
            m = 12
    return list(reversed(out))

def _rand_day(year: int, month: int, preferred: int | None = None):
    """Giorno del mese realistico: se preferred è None, un giorno casuale non festivo."""
    last_day = calendar.monthrange(year, month)[1]
    if preferred and 1 <= preferred <= last_day:
        return preferred
    day = random.randint(2, min(last_day, 28))   # evita 29-31
    dt = date_cls(year, month, day)
    if dt.weekday() == 6:                         # evita domenica
        day = max(2, day - 1)
    return day

def _safe_day(y: int, m: int, d: int) -> int:
    """Rende il giorno valido nel mese e MAI futuro se è il mese corrente."""
    last = calendar.monthrange(y, m)[1]
    d = max(1, min(d, last))
    today = date_cls.today()
    if y == today.year and m == today.month:
        d = min(d, today.day)
    return d

def _add_tx(*, account_id: str, y: int, m: int, d: int, desc: str,
            category: str, ttype: str, amount: float, piggy_id: str | None = None):
    """Inserisce una transazione. DEBIT negativo, CREDIT positivo."""
    assert ttype in ("DEBIT", "CREDIT")
    db = get_db()
    tx_id = _next_id("TRX", "transactions", "transaction_id")
    d = _safe_day(y, m, d)  # <<< evita date future
    iso_date = date_cls(y, m, d).isoformat()
    db.execute("""
        INSERT INTO transactions (transaction_id, account_id, piggy_id, date, description, category, type, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (tx_id, account_id, piggy_id, iso_date, desc, category, ttype, float(amount)))
    return float(amount)

def _ensure_demo_entities():
    """Assicura utente, account e salvadanaio demo."""
    db = get_db()
    # Utente
    u = db.execute("SELECT user_id FROM users WHERE user_id='USE001'").fetchone()
    if not u:
        from werkzeug.security import generate_password_hash
        db.execute("""INSERT INTO users (user_id, codice_cliente, first_name, last_name, password_hash)
                      VALUES ('USE001','123456','Emanuele','Chiummo',?)""",
                   (generate_password_hash("Password123!"),))
    # Account
    a = db.execute("SELECT account_id FROM accounts WHERE account_id='ACC001'").fetchone()
    if not a:
        db.execute("""INSERT INTO accounts (account_id, user_id, iban, name, currency, balance)
                      VALUES ('ACC001','USE001','IT60X0542811101000000123456','Conto Principale','EUR',0)""")
    # Piggy
    p = db.execute("SELECT piggy_id FROM piggy_banks WHERE piggy_id='PIG001'").fetchone()
    if not p:
        db.execute("""INSERT INTO piggy_banks (piggy_id, user_id, name, target_amount, current_amount, status)
                      VALUES ('PIG001','USE001','Vacanze',1000,0,'ACTIVE')""")
    db.commit()
    return "USE001", "ACC001", "PIG001"

@app.cli.command("seed-demo-12m")
def seed_demo_12m():
    """
    Genera dati demo realistici per gli ultimi 12 mesi:
    - Stipendio mensile, affitto, bollette, abbonamenti, spesa, ristoranti, carburante, shopping, viaggi stagionali.
    - Accantonamento mensile nel salvadanaio (escluso dai report grazie a piggy_id).
    """
    random.seed(42)  # riproducibilità
    db = get_db()
    user_id, account_id, piggy_id = _ensure_demo_entities()

    # 1) Pulisci periodo target (ultimi 13 mesi per sicurezza)
    cutoff = (date_cls.today().replace(day=1) - timedelta(days=370)).isoformat()
    db.execute("DELETE FROM transactions WHERE account_id=? AND date >= ?", (account_id, cutoff))
    db.execute("DELETE FROM piggy_transfers WHERE account_id=? AND date >= ?", (account_id, cutoff))
    db.commit()

    # 2) Parametri realistici
    base_opening = random.randint(600, 1400)  # saldo iniziale ipotetico
    salary_min, salary_max = 1600, 2400
    rent = random.choice([550, 650, 700, 800, 900])
    utilities_min, utilities_max = 40, 120

    subs = [
        ("Netflix", "Abbonamenti", 12.99),
        ("Spotify", "Abbonamenti", 9.99),
        ("iCloud", "Abbonamenti", random.choice([0.99, 2.99])),
    ]

    supermarkets = ["Coop", "Conad", "Esselunga", "Carrefour", "Lidl"]
    diners = ["Trattoria da Mario", "Pizzeria Bella Napoli", "Sushi Go", "Osteria del Centro"]
    fuel_stations = ["ENI", "Q8", "IP", "Tamoil"]
    shops = ["Zara", "Decathlon", "MediaWorld", "Amazon", "IKEA"]
    utilities_list = ["Bolletta Luce", "Bolletta Gas", "Bolletta Acqua"]

    # Stagionalità (agosto/viaggi + dicembre/regali)
    def seasonal_multiplier(month: int) -> float:
        if month in (7, 8):   # luglio, agosto
            return 1.25
        if month == 12:       # dicembre
            return 1.20
        return 1.0

    # 3) Generazione
    net_flow = 0.0
    for (y, m) in _month_iter(12):
        mult = seasonal_multiplier(m)

        # Stipendio (27 del mese o l'ultimo giorno lavorativo precedente)
        salary_amt = random.randint(salary_min, salary_max)
        d = min(27, calendar.monthrange(y, m)[1])
        dt = date_cls(y, m, d)
        if dt.weekday() == 6:  # se domenica, anticipa di 1
            d -= 1
        net_flow += _add_tx(account_id=account_id, y=y, m=m, d=d,
                            desc="Stipendio", category="Entrate",
                            ttype="CREDIT", amount=salary_amt)

        # Affitto (1 del mese)
        net_flow += _add_tx(account_id=account_id, y=y, m=m, d=1,
                            desc="Affitto", category="Casa",
                            ttype="DEBIT", amount=-float(rent))

        # Abbonamenti (tra il 5 e il 12)
        for name, cat, fee in subs:
            day = min(random.randint(5, 12), calendar.monthrange(y, m)[1])
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day,
                                desc=f"{name}", category=cat,
                                ttype="DEBIT", amount=-round(fee, 2))

        # Utenze (metà mese)
        bolletta = random.choice(utilities_list)
        util_cost = round(random.uniform(utilities_min, utilities_max) * mult, 2)
        day_util = min(random.randint(13, 19), calendar.monthrange(y, m)[1])
        net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day_util,
                            desc=bolletta, category="Utenze",
                            ttype="DEBIT", amount=-util_cost)

        # Spesa (3–6 volte/mese)
        for _ in range(random.randint(3, 6)):
            market = random.choice(supermarkets)
            cost = round(random.uniform(28, 110) * mult, 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day,
                                desc=f"Spesa {market}", category="Spesa",
                                ttype="DEBIT", amount=-cost)

        # Ristoranti (2–5/mese)
        for _ in range(random.randint(2, 5)):
            place = random.choice(diners)
            cost = round(random.uniform(15, 55) * mult, 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day,
                                desc=place, category="Ristoranti",
                                ttype="DEBIT", amount=-cost)

        # Carburante/Trasporti (0–2/mese)
        for _ in range(random.randint(0, 2)):
            station = random.choice(fuel_stations)
            cost = round(random.uniform(45, 120), 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day,
                                desc=f"Carburante {station}", category="Trasporti",
                                ttype="DEBIT", amount=-cost)

        # Shopping (1–3/mese)
        for _ in range(random.randint(1, 3)):
            shop = random.choice(shops)
            cost = round(random.uniform(20, 150) * mult, 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day,
                                desc=shop, category="Shopping",
                                ttype="DEBIT", amount=-cost)

        # Sanità (0–1/mese)
        if random.random() < 0.35:
            day = _rand_day(y, m)
            cost = round(random.uniform(20, 80), 2)
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day,
                                desc="Ticket sanitario", category="Sanità",
                                ttype="DEBIT", amount=-cost)

        # Viaggi (estate o dicembre): hotel + treno/aereo
        if m in (7, 8) or (m == 12 and random.random() < 0.4):
            day1 = _safe_day(y, m, _rand_day(y, m))
            day2 = _safe_day(y, m, min(day1 + 2, calendar.monthrange(y, m)[1]))
            if day2 < day1:
                day2 = day1
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day1,
                                desc="Hotel", category="Viaggi",
                                ttype="DEBIT", amount=-round(random.uniform(120, 280), 2))
            net_flow += _add_tx(account_id=account_id, y=y, m=m, d=day2,
                                desc="Treno/Aereo", category="Viaggi",
                                ttype="DEBIT", amount=-round(random.uniform(70, 180), 2))

        # Accantonamento nel salvadanaio (100€/mese) – clamp giorno (max oggi)
        d_pig = _safe_day(y, m, min(25, calendar.monthrange(y, m)[1]))
        try:
            _insert_piggy_transfer(
                piggy_id=piggy_id, account_id=account_id, amount=100.0,
                direction="TO_PIGGY", note="Accantonamento mensile",
                tx_on_account=True, when=date_cls(y, m, d_pig).isoformat()
            )
        except Exception:
            pass

        # Imprevisto estivo (agosto): FROM_PIGGY 100–200 – clamp giorno
        if m == 8 and random.random() < 0.5:
            d_imp = _safe_day(y, m, min(28, calendar.monthrange(y, m)[1]))
            try:
                _insert_piggy_transfer(
                    piggy_id=piggy_id, account_id=account_id,
                    amount=random.choice([100.0, 150.0, 200.0]),
                    direction="FROM_PIGGY", note="Imprevisto estivo",
                    tx_on_account=True, when=date_cls(y, m, d_imp).isoformat()
                )
            except Exception:
                pass

    # 4) Aggiorna saldo del conto e del salvadanaio
    db.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (float(base_opening + net_flow), account_id))
    _recalc_piggy(piggy_id)
    db.commit()
    print(f"✅ Dati demo ultimi 12 mesi generati su {account_id}. Saldo base ~{base_opening}€, piggy ricalcolato.")

@app.cli.command("seed-demo-p2p")
def seed_demo_p2p_cmd():
    """Crea un secondo utente + account e un contatto in rubrica per USE001."""
    db = get_db()
    # utente 2
    u2 = db.execute("SELECT 1 FROM users WHERE user_id='USE002'").fetchone()
    if not u2:
        db.execute("""INSERT INTO users (user_id, codice_cliente, first_name, last_name, password_hash)
                      VALUES ('USE002', '654321', 'Mario', 'Rossi', ?)""",
                   (generate_password_hash("Password123!"),))
    a2 = db.execute("SELECT 1 FROM accounts WHERE account_id='ACC002'").fetchone()
    if not a2:
        db.execute("""INSERT INTO accounts (account_id, user_id, iban, name, currency, balance)
                      VALUES ('ACC002','USE002','IT60X0542811101000000654321','Conto Mario','EUR',500.00)""")
    # contatto in rubrica per USE001 che punta a ACC002
    c1 = db.execute("SELECT 1 FROM contacts WHERE contact_id='CON001' AND owner_user_id='USE001'").fetchone()
    if not c1:
        db.execute("""INSERT INTO contacts (contact_id, owner_user_id, display_name, target_user_id, target_account_id)
                      VALUES ('CON001','USE001','Mario Rossi','USE002','ACC002')""")
    db.commit()
    print("✅ seed-demo-p2p: creati USE002/ACC002 e contatto CON001 per USE001.")



# --- Auth Utils ---
def login_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


# --- Routes WEB ---
@app.route('/')
def index():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        codice_cliente = request.form.get('codice_cliente', '').strip()
        password = request.form.get('password', '')

        if not codice_cliente or not password:
            flash('Inserisci codice utente e password.', 'warning')
            return render_template('login.html')

        db = get_db()
        user = db.execute(
            "SELECT user_id, codice_cliente, first_name, last_name, password_hash FROM users WHERE codice_cliente = ?",
            (codice_cliente,)
        ).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['user_id']
            session['codice_cliente'] = user['codice_cliente']
            session['display_name'] = f"{user['first_name']} {user['last_name']}".strip()
            _ensure_user_settings(user['user_id'])
            _ensure_notification(
                user_id=user['user_id'],
                type_='LOGIN',
                title='Nuovo accesso eseguito',
                body='Login completato con successo.',
                dedupe_key='login:last',
            )
            flash('Accesso effettuato!', 'success')
            next_url = request.args.get('next') or url_for('dashboard')
            return redirect(next_url)

        flash('Credenziali non valide.', 'danger')

    return render_template('login.html')

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('Sei uscitə dall’account.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    user_id = session.get('user_id')

    accounts = db.execute("""
        SELECT account_id, name, iban, currency, balance
        FROM accounts
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()

    piggies = db.execute("""
        SELECT piggy_id, name, target_amount, current_amount, status
        FROM piggy_banks
        WHERE user_id = ? AND status != 'DELETED'
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()

    transactions = db.execute("""
        SELECT t.transaction_id, t.date, t.description, t.category, t.type, t.amount, a.name AS account_name
        FROM transactions t
        JOIN accounts a ON a.account_id = t.account_id
        WHERE a.user_id = ?
        ORDER BY date DESC, t.created_at DESC
        LIMIT 10
    """, (user_id,)).fetchall()

    settings = _get_user_settings(user_id)

    dash_payload = {
        "account": {
            "name": (accounts[0]["name"] if accounts else None),
            "balance": float(accounts[0]["balance"] if accounts else 0.0),
            "currency": accounts[0]["currency"] if accounts else "EUR",
        },
        "piggies": [
            {
                "name": p["name"],
                "current": float(p["current_amount"] or 0.0),
                "target": float(p["target_amount"] or 0.0),
            }
            for p in piggies
        ],
        "transactions": [
            {
                "date": t["date"],
                "amount": float(t["amount"] or 0.0),
            }
            for t in transactions
        ],
    }

    # Notifica se un salvadanaio raggiunge il target
    for piggy in piggies:
        target = piggy['target_amount']
        current = float(piggy['current_amount'] or 0.0)
        if target and current >= float(target) - 1e-6:
            _ensure_notification(
                user_id=user_id,
                type_='PIGGY_TARGET',
                title=f"Obiettivo '{piggy['name']}' raggiunto",
                body=f"Hai accumulato {current:.2f}€ su un target di {float(target):.2f}€.",
                dedupe_key=f"piggy:target:{piggy['piggy_id']}",
                payload={'piggy_id': piggy['piggy_id'], 'current': current, 'target': float(target)},
            )
        else:
            _clear_notification_by_dedupe(user_id=user_id, dedupe_key=f"piggy:target:{piggy['piggy_id']}")

    notifications = _list_notifications(user_id, limit=10)
    unread_count = _count_unread_notifications(user_id)

    return render_template(
        'dashboard.html',
        display_name=session.get('display_name') or session.get('codice_cliente'),
        codice_cliente=session.get('codice_cliente'),
        accounts=accounts,
        piggies=piggies,
        transactions=transactions,
        notifications=notifications,
        unread_notifications=unread_count,
        settings=settings,
        dash_payload=dash_payload,
    )


@app.route('/notifications', methods=['GET', 'POST'])
@login_required
def notifications_center():
    user_id = session['user_id']
    if request.method == 'POST':
        notification_id = request.form.get('notification_id')
        action = (request.form.get('action') or 'read').upper()
        status = 'UNREAD' if action == 'UNREAD' else 'READ'
        if notification_id and _mark_notification(user_id, notification_id, status):
            flash('Notifica aggiornata.', 'success')
        else:
            flash('Impossibile aggiornare la notifica selezionata.', 'danger')
        return redirect(url_for('notifications_center'))

    status_filter = request.args.get('status')
    items = _list_notifications(user_id, limit=50, status=status_filter if status_filter in {'READ', 'UNREAD'} else None)
    unread_count = _count_unread_notifications(user_id)
    return render_template(
        'notifications.html',
        notifications=items,
        unread_notifications=unread_count,
        status_filter=status_filter,
    )


@app.get('/api/notifications')
@login_required
def api_notifications():
    user_id = session['user_id']
    status_filter = request.args.get('status')
    limit = request.args.get('limit', 10)
    try:
        limit_int = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit_int = 10
    items = _list_notifications(user_id, limit=limit_int, status=status_filter if status_filter in {'READ', 'UNREAD'} else None)
    return jsonify({'items': items, 'unread': _count_unread_notifications(user_id)})


@app.post('/api/notifications/<notification_id>')
@login_required
def api_notification_update(notification_id: str):
    user_id = session['user_id']
    data = request.get_json(silent=True) or {}
    status = (data.get('status') or 'READ').upper()
    if _mark_notification(user_id, notification_id, 'UNREAD' if status == 'UNREAD' else 'READ'):
        return jsonify({'message': 'ok', 'unread': _count_unread_notifications(user_id)})
    return jsonify({'message': 'not found'}), 404


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    user_id = session['user_id']
    if request.method == 'POST':
        default_currency = request.form.get('default_currency', 'EUR')
        decimal_places = request.form.get('decimal_places', 2)
        notify_threshold = request.form.get('notify_threshold', 1.0)
        _update_user_settings(
            user_id,
            default_currency=default_currency,
            decimal_places=decimal_places,
            notify_threshold=notify_threshold,
        )
        flash('Impostazioni aggiornate.', 'success')
        return redirect(url_for('settings'))

    settings_data = _get_user_settings(user_id)
    return render_template('settings.html', settings=settings_data)


@app.post("/piggy/create")
@login_required
def web_piggy_create():
    name = (request.form.get("name") or "").strip()
    target_amount = request.form.get("target_amount") or None
    if not name:
        flash("Inserisci un nome per il salvadanaio.", "warning")
        return redirect(url_for("dashboard"))

    db = get_db()
    piggy_id = _next_id("PIG", "piggy_banks", "piggy_id")
    db.execute("""
        INSERT INTO piggy_banks (piggy_id, user_id, name, target_amount, current_amount, status)
        VALUES (?, ?, ?, ?, 0, 'ACTIVE')
    """, (piggy_id, session["user_id"], name, float(target_amount) if target_amount else None))
    db.commit()
    flash(f"Salvadanaio '{name}' creato (ID {piggy_id}).", "success")
    return redirect(url_for("dashboard"))

@app.post("/piggy/transfer")
@login_required
def web_piggy_transfer():
    piggy_id = request.form.get("piggy_id")
    account_id = request.form.get("account_id")
    direction = request.form.get("direction")  # TO_PIGGY | FROM_PIGGY
    note = request.form.get("note") or None
    amount_str = request.form.get("amount") or "0"
    when = request.form.get("date") or date.today().isoformat()
    tx_on_account = True if request.form.get("create_account_tx") == "on" else False

    if not _ensure_user_owns_piggy(session["user_id"], piggy_id):
        flash("Salvadanaio non trovato.", "danger")
        return redirect(url_for("dashboard"))

    try:
        amount = float(amount_str)
    except ValueError:
        flash("Importo non valido.", "danger")
        return redirect(url_for("dashboard"))
    if amount <= 0:
        flash("L'importo deve essere positivo.", "warning")
        return redirect(url_for("dashboard"))
    if direction not in ("TO_PIGGY", "FROM_PIGGY"):
        flash("Direzione non valida.", "danger")
        return redirect(url_for("dashboard"))

    # Guard rail: no negativo
    current = _get_piggy_balance(piggy_id)
    if direction == "FROM_PIGGY" and amount > current + 1e-9:
        flash("Saldo salvadanaio insufficiente.", "danger")
        return redirect(url_for("dashboard"))

    try:
        _insert_piggy_transfer(piggy_id=piggy_id, account_id=account_id, amount=amount,
                               direction=direction, note=note, tx_on_account=tx_on_account, when=when)
        flash("Trasferimento registrato con successo.", "success")
    except ValueError as e:
        flash(str(e), "danger")

    return redirect(url_for("dashboard"))

# --- NEW: elimina salvadanaio (soft delete + riversamento) ---
@app.post("/piggy/delete")
@login_required
def web_piggy_delete():
    piggy_id = request.form.get("piggy_id")
    target_account_id = request.form.get("account_id")
    if not piggy_id or not target_account_id:
        flash("Dati mancanti per eliminare il salvadanaio.", "danger")
        return redirect(url_for("dashboard"))
    if not _ensure_user_owns_piggy(session["user_id"], piggy_id):
        flash("Salvadanaio non trovato.", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()
    # riversa eventuale saldo residuo
    saldo = _get_piggy_balance(piggy_id)
    if saldo > 0:
        _insert_piggy_transfer(
            piggy_id=piggy_id,
            account_id=target_account_id,
            amount=saldo,
            direction="FROM_PIGGY",
            note="Chiusura salvadanaio (rientro fondi)",
            tx_on_account=True,
            when=date.today().isoformat()
        )

    # soft delete per evitare problemi di FK
    db.execute("UPDATE piggy_banks SET status = 'DELETED' WHERE piggy_id = ? AND user_id = ?", (piggy_id, session["user_id"]))
    db.commit()
    flash("Salvadanaio eliminato.", "info")
    return redirect(url_for("dashboard"))


# --- API (Swagger) ---

@app.post("/api/login")
@swag_from({
    "summary": "Login API",
    "description": "Autentica l'utente usando codice cliente e password. Imposta la sessione server-side.",
    "tags": ["Auth"],
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "codice_cliente": {"type": "string", "example": "123456"},
                        "password": {"type": "string", "example": "Password123!"}
                    },
                    "required": ["codice_cliente", "password"]
                }
            }
        }
    },
    "responses": {"200": {"description": "Login riuscito"}, "401": {"description": "Credenziali non valide"}}
})
def api_login():
    data = request.get_json(silent=True) or {}
    codice_cliente = (data.get("codice_cliente") or "").strip()
    password = data.get("password") or ""
    if not codice_cliente or not password:
        return jsonify({"message": "codice_cliente e password sono obbligatori"}), 400

    db = get_db()
    user = db.execute(
        "SELECT user_id, codice_cliente, password_hash FROM users WHERE codice_cliente = ?",
        (codice_cliente,)
    ).fetchone()
    if user and check_password_hash(user["password_hash"], password):
        session["user_id"] = user["user_id"]
        session["codice_cliente"] = user["codice_cliente"]
        return jsonify({"message": "ok"})
    return jsonify({"message": "unauthorized"}), 401


@app.get("/api/accounts")
@swag_from({
    "summary": "Lista conti correnti",
    "tags": ["Accounts"],
    "responses": {"200": {"description": "Elenco conti"}, "401": {"description": "Non autenticato"}}
})
def api_accounts():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    db = get_db()
    rows = db.execute("""
        SELECT account_id, name, iban, currency, balance
        FROM accounts
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (session["user_id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/transactions")
@swag_from({
    "summary": "Lista transazioni con ricerca e ordinamento",
    "tags": ["Transactions"],
    "parameters": [
        {"name": "account_id", "in": "query", "schema": {"type": "string"}},
        {"name": "q", "in": "query", "schema": {"type": "string"}, "description": "Cerca in descrizione/categoria"},
        {"name": "type", "in": "query", "schema": {"type": "string", "enum": ["DEBIT", "CREDIT"]}},
        {"name": "date_from", "in": "query", "schema": {"type": "string", "example": "2024-01-01"}},
        {"name": "date_to", "in": "query", "schema": {"type": "string", "example": "2024-12-31"}},
        {"name": "sort", "in": "query", "schema": {"type": "string", "enum": ["date", "amount", "description", "category"]}},
        {"name": "order", "in": "query", "schema": {"type": "string", "enum": ["asc", "desc"]}},
        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10, "minimum": 1, "maximum": 200}},
        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0, "minimum": 0}}
    ],
    "responses": {"200": {"description": "OK"}, "401": {"description": "Non autenticato"}}
})
def api_transactions():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401

    account_id = request.args.get("account_id")
    q = (request.args.get("q") or "").strip()
    tx_type = request.args.get("type")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    sort = (request.args.get("sort") or "date").lower()
    order = (request.args.get("order") or "desc").lower()

    try:
        limit = max(1, min(int(request.args.get("limit", 10)), 200))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0

    # whitelist colonne ordinamento
    sort_map = {
        "date": "t.date",
        "amount": "t.amount",
        "description": "t.description",
        "category": "t.category",
    }
    sort_col = sort_map.get(sort, "t.date")
    order_sql = "ASC" if order == "asc" else "DESC"

    where = ["a.user_id = ?"]
    params = [session["user_id"]]

    if account_id:
        where.append("t.account_id = ?")
        params.append(account_id)
    if tx_type in ("DEBIT", "CREDIT"):
        where.append("t.type = ?")
        params.append(tx_type)
    if q:
        where.append("(t.description LIKE ? OR t.category LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if date_from:
        where.append("t.date >= ?")
        params.append(date_from)
    if date_to:
        where.append("t.date <= ?")
        params.append(date_to)

    sql = f"""
        SELECT t.transaction_id, t.date, t.description, t.category, t.type, t.amount,
               t.account_id, a.name AS account_name
        FROM transactions t
        JOIN accounts a ON a.account_id = t.account_id
        WHERE {' AND '.join(where)}
        ORDER BY {sort_col} {order_sql}, t.created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    db = get_db()
    rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])



@app.get("/api/piggy-banks")
@swag_from({
    "summary": "Lista salvadanai",
    "tags": ["PiggyBank"],
    "responses": {"200": {"description": "OK"}, "401": {"description": "Non autenticato"}}
})
def api_piggy_banks():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    db = get_db()
    rows = db.execute("""
        SELECT piggy_id, name, target_amount, current_amount, status
        FROM piggy_banks
        WHERE user_id = ? AND status != 'DELETED'
        ORDER BY created_at DESC
    """, (session["user_id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/piggy/create")
@swag_from({
    "summary": "Crea un nuovo salvadanaio",
    "tags": ["PiggyBank"],
    "requestBody": {"required": True},
    "responses": {"200": {"description": "Creato"}, "400": {"description": "Errore input"}, "401": {"description": "Non autenticato"}}
})
def api_piggy_create():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    target_amount = data.get("target_amount", None)
    if not name:
        return jsonify({"message": "name richiesto"}), 400
    try:
        ta = float(target_amount) if target_amount is not None else None
    except (TypeError, ValueError):
        return jsonify({"message": "target_amount non valido"}), 400

    db = get_db()
    piggy_id = _next_id("PIG", "piggy_banks", "piggy_id")
    db.execute("""
        INSERT INTO piggy_banks (piggy_id, user_id, name, target_amount, current_amount, status)
        VALUES (?, ?, ?, ?, 0, 'ACTIVE')
    """, (piggy_id, session["user_id"], name, ta))
    db.commit()
    return jsonify({"piggy_id": piggy_id, "name": name, "target_amount": ta, "status": "ACTIVE"})


@app.post("/api/piggy/transfer")
@swag_from({
    "summary": "Trasferimento verso/da salvadanaio",
    "description": "Impedisce saldo negativo del salvadanaio.",
    "tags": ["PiggyBank"],
    "requestBody": {"required": True},
    "responses": {"200": {"description": "Creato"}, "400": {"description": "Errore input"}, "401": {"description": "Non autenticato"}}
})
def api_piggy_transfer():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    data = request.get_json(silent=True) or {}
    required = ["transfer_id", "piggy_id", "account_id", "date", "amount", "direction"]
    if any(k not in data for k in required):
        return jsonify({"message": "campi obbligatori mancanti"}), 400
    if data["direction"] not in ("TO_PIGGY", "FROM_PIGGY"):
        return jsonify({"message": "direction non valida"}), 400

    # guard rail
    try:
        amount = float(data["amount"])
    except (TypeError, ValueError):
        return jsonify({"message": "amount non valido"}), 400
    current = _get_piggy_balance(data["piggy_id"])
    projected = current + (amount if data["direction"] == "TO_PIGGY" else -amount)
    if projected < 0:
        return jsonify({"message": "saldo salvadanaio insufficiente"}), 400

    db = get_db()
    db.execute("""
        INSERT INTO piggy_transfers (transfer_id, piggy_id, account_id, date, amount, direction, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(transfer_id) DO NOTHING
    """, (
        data["transfer_id"], data["piggy_id"], data["account_id"],
        data["date"], float(amount), data["direction"], data.get("note")
    ))

    # aggiorna current_amount e (opzionale) registra anche sul conto
    _recalc_piggy(data["piggy_id"])

    if data.get("create_account_tx"):
        tx_id = f"TRX{data['transfer_id'][3:]}" if str(data["transfer_id"]).startswith("TRP") else f"TRX_{data['transfer_id']}"
        if data["direction"] == "TO_PIGGY":
            amt = -abs(float(amount))
            desc = "Trasferimento verso salvadanaio"
            ttype = "DEBIT"
        else:
            amt = abs(float(amount))
            desc = "Prelievo da salvadanaio"
            ttype = "CREDIT"

        cur = db.execute("""
            INSERT INTO transactions (transaction_id, account_id, piggy_id, date, description, category, type, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO NOTHING
        """, (
            tx_id, data["account_id"], data["piggy_id"], data["date"],
            desc, "Risparmio", ttype, amt
        ))
        if cur.rowcount:
            _apply_account_delta(data["account_id"], amt)

    db.commit()
    return jsonify({"message": "ok"})

# --- API elimina salvadanaio ---
@app.delete("/api/piggy/<piggy_id>")
@swag_from({
    "summary": "Elimina un salvadanaio",
    "description": "Se il salvadanaio ha saldo > 0, specifica ?account_id=ACCxxx per riversare i fondi prima della chiusura.",
    "tags": ["PiggyBank"],
    "parameters": [
        {"name": "piggy_id", "in": "path", "schema": {"type": "string"}, "required": True},
        {"name": "account_id", "in": "query", "schema": {"type": "string"}, "required": False}
    ],
    "responses": {"200": {"description": "Eliminato"}, "400": {"description": "Errore"}, "401": {"description": "Non autenticato"}}
})
def api_piggy_delete(piggy_id):
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    if not _ensure_user_owns_piggy(session["user_id"], piggy_id):
        return jsonify({"message": "not found"}), 404

    account_id = request.args.get("account_id")
    saldo = _get_piggy_balance(piggy_id)
    if saldo > 0 and not account_id:
        return jsonify({"message": "account_id richiesto per riversare il saldo residuo"}), 400

    if saldo > 0:
        _insert_piggy_transfer(
            piggy_id=piggy_id,
            account_id=account_id,
            amount=saldo,
            direction="FROM_PIGGY",
            note="Chiusura salvadanaio (rientro fondi)",
            tx_on_account=True,
            when=date.today().isoformat()
        )
    db = get_db()
    db.execute("UPDATE piggy_banks SET status = 'DELETED' WHERE piggy_id = ? AND user_id = ?", (piggy_id, session["user_id"]))
    db.commit()
    return jsonify({"message": "deleted"})

@app.get("/api/contacts")
@swag_from({"summary": "Rubrica contatti P2P", "tags": ["P2P"]})
def api_contacts():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    q = (request.args.get("q") or "").strip()
    params = [session["user_id"]]
    where = ["owner_user_id = ?"]
    if q:
        where.append("display_name LIKE ?")
        params.append(f"%{q}%")
    db = get_db()
    rows = db.execute(f"""
        SELECT contact_id, display_name, target_user_id, target_account_id, iban
        FROM contacts WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
    """, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/p2p/groups")
def api_split_groups_list():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    groups = _list_split_groups(session["user_id"])
    return jsonify({"groups": groups})


@app.post("/api/p2p/groups")
def api_split_groups_create():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"message": "name richiesto"}), 400
    user_id = session["user_id"]
    group_id = _next_id("SPG", "split_groups", "group_id")
    db = get_db()
    db.execute(
        """
        INSERT INTO split_groups (group_id, user_id, name)
        VALUES (?, ?, ?)
        """,
        (group_id, user_id, name),
    )
    db.commit()
    return jsonify({
        "message": "created",
        "group": {
            "group_id": group_id,
            "name": name,
            "members": [],
        },
    }), 201


@app.delete("/api/p2p/groups/<group_id>")
def api_split_groups_delete(group_id: str):
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    db = get_db()
    res = db.execute(
        "DELETE FROM split_groups WHERE group_id = ? AND user_id = ?",
        (group_id, session["user_id"]),
    )
    if res.rowcount == 0:
        return jsonify({"message": "not found"}), 404
    db.commit()
    return jsonify({"message": "deleted"})


@app.post("/api/p2p/groups/<group_id>/members")
def api_split_groups_add_member(group_id: str):
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    user_id = session["user_id"]
    group = _get_split_group(user_id, group_id)
    if not group:
        return jsonify({"message": "not found"}), 404
    data = request.get_json(silent=True) or {}
    contact_id = data.get("contact_id")
    if not contact_id:
        return jsonify({"message": "contact_id richiesto"}), 400
    contact = _ensure_contact(user_id, contact_id)
    if not contact:
        return jsonify({"message": "contatto non valido"}), 400
    if not contact["target_user_id"] or not contact["target_account_id"]:
        return jsonify({"message": "il contatto selezionato non supporta richieste P2P interne"}), 400
    db = get_db()
    dup = db.execute(
        "SELECT 1 FROM split_group_members WHERE group_id = ? AND contact_id = ?",
        (group_id, contact_id),
    ).fetchone()
    if dup:
        return jsonify({"message": "contatto già nel gruppo"}), 409
    member_id = _next_id("SPM", "split_group_members", "member_id")
    db.execute(
        """
        INSERT INTO split_group_members (member_id, group_id, contact_id, display_name)
        VALUES (?, ?, ?, ?)
        """,
        (member_id, group_id, contact_id, contact["display_name"]),
    )
    db.commit()
    return jsonify({
        "message": "added",
        "member": {
            "member_id": member_id,
            "contact_id": contact_id,
            "display_name": contact["display_name"],
        },
    }), 201


@app.delete("/api/p2p/groups/<group_id>/members/<member_id>")
def api_split_groups_remove_member(group_id: str, member_id: str):
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    user_id = session["user_id"]
    group = _get_split_group(user_id, group_id)
    if not group:
        return jsonify({"message": "not found"}), 404
    db = get_db()
    res = db.execute(
        "DELETE FROM split_group_members WHERE member_id = ? AND group_id = ?",
        (member_id, group_id),
    )
    if res.rowcount == 0:
        return jsonify({"message": "not found"}), 404
    db.commit()
    return jsonify({"message": "deleted"})


@app.post("/api/p2p/groups/<group_id>/split")
def api_split_groups_split(group_id: str):
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    user_id = session["user_id"]
    group = _get_split_group(user_id, group_id)
    if not group:
        return jsonify({"message": "not found"}), 404
    data = request.get_json(silent=True) or {}
    raw_amount = data.get("amount")
    mode = (data.get("mode") or "").lower()
    message = (data.get("message") or "").strip() or None
    if mode not in {"send", "request"}:
        return jsonify({"message": "mode deve essere 'send' o 'request'"}), 400
    try:
        amount = Decimal(str(raw_amount))
    except (TypeError, ValueError, InvalidOperation):
        return jsonify({"message": "amount non valido"}), 400
    if amount <= 0:
        return jsonify({"message": "amount deve essere > 0"}), 400

    members = _get_split_members(user_id, group_id)
    if not members:
        return jsonify({"message": "il gruppo non ha membri"}), 400

    shares = _split_even(amount, len(members))
    results = []
    owner_name = session.get("display_name") or session.get("codice_cliente") or "Utente"

    if mode == "send":
        from_account_id = data.get("from_account_id")
        if not from_account_id:
            return jsonify({"message": "from_account_id richiesto per mode=send"}), 400
        db = get_db()
        account = db.execute(
            "SELECT balance FROM accounts WHERE account_id = ? AND user_id = ?",
            (from_account_id, user_id),
        ).fetchone()
        if not account:
            return jsonify({"message": "conto mittente non valido"}), 400
        total_out = sum(shares)
        balance = Decimal(str(account["balance"] or 0))
        if balance < total_out:
            return jsonify({"message": "saldo insufficiente per coprire tutte le quote"}), 400

        for row, share in zip(members, shares):
            share_float = float(share)
            try:
                p2p_id = _p2p_instant(
                    from_account_id=from_account_id,
                    to_account_id=row["target_account_id"],
                    amount=share_float,
                    message=message,
                    from_user_id=user_id,
                    to_user_id=row["target_user_id"],
                    to_name=row["display_name"],
                    from_name=owner_name,
                )
            except ValueError as exc:
                return jsonify({"message": str(exc)}), 400
            _ensure_user_settings(row["target_user_id"])
            _ensure_notification(
                user_id=user_id,
                type_="P2P_SENT",
                title="Trasferimento inviato",
                body=f"Hai inviato {share_float:.2f}€ a {row['display_name']} per {group['name']}.",
                dedupe_key=f"p2p:split:sent:{p2p_id}",
                payload={
                    "p2p_id": p2p_id,
                    "group_id": group_id,
                    "amount": share_float,
                    "member_id": row["member_id"],
                },
            )
            _ensure_notification(
                user_id=row["target_user_id"],
                type_="P2P_RECEIVED",
                title="Hai ricevuto un trasferimento",
                body=f"{owner_name} ti ha inviato {share_float:.2f}€ (gruppo {group['name']}).",
                dedupe_key=f"p2p:split:recv:{p2p_id}",
                payload={
                    "p2p_id": p2p_id,
                    "group_id": group_id,
                    "amount": share_float,
                },
            )
            results.append({
                "member_id": row["member_id"],
                "contact_id": row["contact_id"],
                "display_name": row["display_name"],
                "amount": round(share_float, 2),
                "status": "sent",
                "p2p_id": p2p_id,
            })
        return jsonify({"message": "sent", "results": results, "mode": mode})

    for row, share in zip(members, shares):
        share_float = float(share)
        _ensure_user_settings(row["target_user_id"])
        notif_id = _ensure_notification(
            user_id=row["target_user_id"],
            type_="P2P_REQUEST",
            title=f"Richiesta rimborso gruppo {group['name']}",
            body=f"{owner_name} chiede {share_float:.2f}€ per una spesa condivisa.",
            payload={
                "group_id": group_id,
                "origin_user": user_id,
                "amount": share_float,
                "message": message,
            },
        )
        results.append({
            "member_id": row["member_id"],
            "contact_id": row["contact_id"],
            "display_name": row["display_name"],
            "amount": round(share_float, 2),
            "status": "requested",
            "notification_id": notif_id,
        })

    _ensure_notification(
        user_id=user_id,
        type_="P2P_REQUEST",
        title=f"Richieste inviate per {group['name']}",
        body=f"Hai chiesto {float(amount):.2f}€ da {len(members)} persone.",
        payload={"group_id": group_id, "amount": float(amount)},
    )
    return jsonify({"message": "requested", "results": results, "mode": mode})

@app.post("/api/p2p/send")
@swag_from({"summary": "Invio P2P interno istantaneo", "tags": ["P2P"], "requestBody": {"required": True}})
def api_p2p_send():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401

    data = request.get_json(silent=True) or {}
    from_account_id = data.get("from_account_id")
    contact_id = data.get("contact_id")
    amount = data.get("amount")
    message = data.get("message") or None
    to_name = (data.get("contact_display_name") or "").strip() or None  # <-- qui

    if not from_account_id or not contact_id or amount is None:
        return jsonify({"message": "campi richiesti: from_account_id, contact_id, amount"}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"message": "amount non valido"}), 400

    contact = _ensure_contact(session["user_id"], contact_id)
    if not contact or not contact["target_user_id"] or not contact["target_account_id"]:
        return jsonify({"message": "contatto non valido o non interno"}), 400
    if amount <= 0:
        return jsonify({"message": "importo deve essere positivo"}), 400

    db = get_db()
    owner = db.execute("SELECT user_id FROM accounts WHERE account_id = ?", (from_account_id,)).fetchone()
    if not owner or owner["user_id"] != session["user_id"]:
        return jsonify({"message": "conto mittente non valido"}), 400
    if contact["target_user_id"] == session["user_id"]:
        return jsonify({"message": "non puoi inviare a te stess*"}), 400

    try:
        p2p_id = _p2p_instant(
            from_account_id=from_account_id,
            to_account_id=contact["target_account_id"],
            amount=amount,
            message=message,
            from_user_id=session["user_id"],
            to_user_id=contact["target_user_id"],
            to_name=to_name,
            from_name=session.get("display_name")  # opzionale
        )
        _ensure_notification(
            user_id=session['user_id'],
            type_='P2P_SENT',
            title='Trasferimento inviato',
            body=f"Hai inviato {amount:.2f}€ a {to_name or contact['display_name']}.",
            dedupe_key=f"p2p:sent:{p2p_id}",
            payload={'p2p_id': p2p_id, 'amount': amount, 'contact_id': contact_id},
        )
        if contact['target_user_id']:
            _ensure_user_settings(contact['target_user_id'])
            _ensure_notification(
                user_id=contact['target_user_id'],
                type_='P2P_RECEIVED',
                title='Hai ricevuto un trasferimento',
                body=f"{session.get('display_name') or session.get('codice_cliente')} ti ha inviato {amount:.2f}€.",
                dedupe_key=f"p2p:recv:{p2p_id}",
                payload={'p2p_id': p2p_id, 'amount': amount, 'from_user': session.get('user_id')},
            )
        return jsonify({"message": "ok", "p2p_id": p2p_id, "to_name": to_name, "amount": amount})
    except ValueError as e:
        return jsonify({"message": str(e)}), 400


# --- REPORT HELPERS ---

def _date_from_months(months: int) -> str:
    """Ritorna la data (YYYY-MM-DD) spostata indietro di 'months' mesi circa (30 giorni * n)."""
    days = max(1, int(months) * 30)
    d = (datetime.utcnow() - timedelta(days=days)).date()
    return d.isoformat()

def _report_summary(user_id: str, months: int = 3):
    """Calcola metriche e dataset per la pagina report."""
    db = get_db()
    since = _date_from_months(months)

    # Serie mensile: entrate/uscite (positive)
    monthly = db.execute("""
        SELECT strftime('%Y-%m', t.date) AS ym,
               SUM(CASE WHEN t.type='CREDIT' THEN t.amount ELSE 0 END) AS inc_raw,
               SUM(CASE WHEN t.type='DEBIT'  THEN -t.amount ELSE 0 END) AS exp_raw
        FROM transactions t
        JOIN accounts a ON a.account_id = t.account_id
        WHERE a.user_id = ?
          AND t.date >= ?
          AND t.piggy_id IS NULL      -- <<< esclude movimenti legati ai salvadanai
        GROUP BY ym
        ORDER BY ym
    """, (user_id, since)).fetchall()
    monthly_data = [{
        "month": r["ym"],
        "income": float(r["inc_raw"] or 0.0),
        "expenses": float(r["exp_raw"] or 0.0),
    } for r in monthly]

    # Spese per categoria
    by_cat = db.execute("""
        SELECT t.category,
               SUM(-t.amount) AS spent
        FROM transactions t
        JOIN accounts a ON a.account_id = t.account_id
        WHERE a.user_id = ?
          AND t.date >= ?
          AND t.type='DEBIT'
          AND t.piggy_id IS NULL      -- <<< esclude movimenti salvadanaio
        GROUP BY t.category
        HAVING spent > 0
        ORDER BY spent DESC
    """, (user_id, since)).fetchall()
    categories = [{"category": r["category"] or "Altro", "amount": float(r["spent"] or 0.0)} for r in by_cat]

    # Totali periodo
    period_income = sum(m["income"] for m in monthly_data)
    period_expenses = sum(m["expenses"] for m in monthly_data)

    # Medie mensili (se non ci sono dati, evita divisoni)
    n_months = max(1, len({m["month"] for m in monthly_data}) or months)
    avg_expenses = period_expenses / n_months
    avg_income = period_income / n_months

    # Saldi correnti: conto + salvadanai
    acc_sum = db.execute("""
        SELECT COALESCE(SUM(balance),0) AS tot
        FROM accounts
        WHERE user_id = ?
    """, (user_id,)).fetchone()["tot"] or 0.0
    piggy_sum = db.execute("""
        SELECT COALESCE(SUM(current_amount),0) AS tot
        FROM piggy_banks
        WHERE user_id = ? AND status != 'DELETED'
    """, (user_id,)).fetchone()["tot"] or 0.0
    net_liquid = float(acc_sum) + float(piggy_sum)

    # --- Financial wellness score (0-100) ---
    # 1) Savings rate (SR): (income - expenses)/income -> 0..1
    if period_income > 0:
        sr = max(0.0, min(1.0, (period_income - period_expenses) / period_income))
    else:
        sr = 0.0

    # 2) Runway: mesi coperti = liquidità / spesa media mensile -> normalizza a 6 mesi (cap a 1)
    runway_months = (net_liquid / avg_expenses) if avg_expenses > 0 else 6.0
    runway_norm = max(0.0, min(1.0, runway_months / 6.0))

    # 3) Stabilità spese: 1 - coefficiente di variazione (cap [0..1])
    import math
    exp_vals = [m["expenses"] for m in monthly_data] or [avg_expenses]
    mean = sum(exp_vals) / len(exp_vals)
    if mean > 0 and len(exp_vals) > 1:
        var = sum((x - mean) ** 2 for x in exp_vals) / (len(exp_vals) - 1)
        cv = math.sqrt(var) / mean
        stability = max(0.0, min(1.0, 1.0 - cv))  # più vicino a 1 = più stabile
    else:
        stability = 1.0

    score = round((sr * 0.45 + runway_norm * 0.35 + stability * 0.20) * 100)

    return {
        "since": since,
        "months": months,
        "monthly": monthly_data,
        "categories": categories,
        "totals": {
            "income": round(period_income, 2),
            "expenses": round(period_expenses, 2),
            "avg_income": round(avg_income, 2),
            "avg_expenses": round(avg_expenses, 2),
            "net_liquid": round(net_liquid, 2),
            "runway_months": round(runway_months if math.isfinite(runway_months) else 6.0, 1)
        },
        "score": int(score),
        "components": {
            "savings_rate": round(sr * 100),
            "runway_norm": round(runway_norm * 100),
            "stability": round(stability * 100)
        }
    }

# --- REPORT ROUTES ---
@app.route('/reports')
@login_required
def reports():
    months = request.args.get('months', '3')
    try:
        months_int = max(1, min(int(months), 12))
    except ValueError:
        months_int = 3
    data = _report_summary(session['user_id'], months=months_int)
    return render_template('report.html', report=data, months=months_int)

@app.get('/api/reports/summary')
@login_required
def api_report_summary():
    months = request.args.get('months', '3')
    try:
        months_int = max(1, min(int(months), 12))
    except ValueError:
        months_int = 3
    data = _report_summary(session['user_id'], months=months_int)
    return jsonify(data)


# --- P2P ROUTES ---
@app.route("/p2p")
@login_required
def p2p():
    db = get_db()
    user_id = session["user_id"]
    accounts = db.execute("""
        SELECT account_id, name, iban, currency, balance
        FROM accounts
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    contacts = db.execute("""
        SELECT contact_id, display_name, target_user_id, target_account_id
        FROM contacts
        WHERE owner_user_id = ?
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    # ultimi movimenti P2P (sia in entrata che in uscita, label via categoria P2P)
    p2p_tx = db.execute("""
        SELECT date, description, type, amount
        FROM transactions t
        JOIN accounts a ON a.account_id = t.account_id
        WHERE a.user_id = ? AND t.category = 'P2P'
        ORDER BY date DESC, t.created_at DESC
        LIMIT 10
    """, (user_id,)).fetchall()
    return render_template("p2p.html", accounts=accounts, contacts=contacts, p2p_tx=p2p_tx)


# --- Avvio ---
if __name__ == '__main__':
    app.run(debug=True)
