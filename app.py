import sqlite3
import re
from datetime import date
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g, jsonify
)
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
    db = get_db()
    row = db.execute(
        f"SELECT {col} AS id FROM {table} WHERE {col} LIKE ? ORDER BY {col} DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if not row:
        return f"{prefix}001"
    m = re.search(r"(\d+)$", row["id"])
    n = int(m.group(1)) + 1 if m else 1
    return f"{prefix}{n:03d}"

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
    # Evita giorni 29-31 se mese corto
    day = random.randint(2, min(last_day, 28))
    # Evita domenica (0 lun, 6 dom -> usiamo weekday: 0 lun, 6 dom)
    dt = date_cls(year, month, day)
    if dt.weekday() == 6:
        day = max(2, day - 1)
    return day

def _add_tx(*, account_id: str, y: int, m: int, d: int, desc: str,
            category: str, ttype: str, amount: float, piggy_id: str | None = None):
    """Inserisce una transazione. DEBIT negativo, CREDIT positivo."""
    assert ttype in ("DEBIT", "CREDIT")
    db = get_db()
    tx_id = _next_id("TRX", "transactions", "transaction_id")
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
    db.execute("""
        DELETE FROM transactions
        WHERE account_id=? AND date >= ?
    """, (account_id, cutoff))
    db.execute("""
        DELETE FROM piggy_transfers
        WHERE account_id=? AND date >= ?
    """, (account_id, cutoff))
    db.commit()

    # 2) Parametri realistici
    base_opening = random.randint(600, 1400)  # saldo iniziale ipotetico
    salary_min, salary_max = 1600, 2400
    rent = random.choice([550, 650, 700, 800, 900])
    phone_min, phone_max = 18, 32
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
        # se cade di domenica, anticipa di 1
        dt = date_cls(y, m, d)
        if dt.weekday() == 6:
            d -= 1
        net_flow += _add_tx(
            account_id=account_id, y=y, m=m, d=d,
            desc="Stipendio", category="Entrate", ttype="CREDIT", amount=salary_amt
        )

        # Affitto (1 del mese)
        net_flow += _add_tx(
            account_id=account_id, y=y, m=m, d=1,
            desc="Affitto", category="Casa", ttype="DEBIT", amount=-float(rent)
        )

        # Abbonamenti (tra il 5 e il 12)
        for name, cat, fee in subs:
            day = min(random.randint(5, 12), calendar.monthrange(y, m)[1])
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day,
                desc=f"{name}", category=cat, ttype="DEBIT", amount=-round(fee, 2)
            )

        # Bollette (metà mese)
        bolletta = random.choice(utilities_list)
        util_cost = round(random.uniform(utilities_min, utilities_max) * mult, 2)
        day_util = min(random.randint(13, 19), calendar.monthrange(y, m)[1])
        net_flow += _add_tx(
            account_id=account_id, y=y, m=m, d=day_util,
            desc=bolletta, category="Utenze", ttype="DEBIT", amount=-util_cost
        )

        # Spesa (3–6 volte/mese)
        for _ in range(random.randint(3, 6)):
            market = random.choice(supermarkets)
            cost = round(random.uniform(28, 110) * mult, 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day,
                desc=f"Spesa {market}", category="Spesa", ttype="DEBIT", amount=-cost
            )

        # Ristoranti (2–5/mese)
        for _ in range(random.randint(2, 5)):
            place = random.choice(diners)
            cost = round(random.uniform(15, 55) * mult, 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day,
                desc=place, category="Ristoranti", ttype="DEBIT", amount=-cost
            )

        # Carburante/Trasporti (0–2/mese)
        for _ in range(random.randint(0, 2)):
            station = random.choice(fuel_stations)
            cost = round(random.uniform(45, 120), 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day,
                desc=f"Carburante {station}", category="Trasporti", ttype="DEBIT", amount=-cost
            )

        # Shopping (1–3/mese)
        for _ in range(random.randint(1, 3)):
            shop = random.choice(shops)
            cost = round(random.uniform(20, 150) * mult, 2)
            day = _rand_day(y, m)
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day,
                desc=shop, category="Shopping", ttype="DEBIT", amount=-cost
            )

        # Sanità (0–1/mese)
        if random.random() < 0.35:
            day = _rand_day(y, m)
            cost = round(random.uniform(20, 80), 2)
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day,
                desc="Ticket sanitario", category="Sanità", ttype="DEBIT", amount=-cost
            )

        # Viaggi (estate o ponti): luglio/agosto e a volte dicembre
        if m in (7, 8) or (m == 12 and random.random() < 0.4):
            # hotel + treno/aereo
            day1 = _rand_day(y, m)
            day2 = min(day1 + 2, calendar.monthrange(y, m)[1])
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day1,
                desc="Hotel", category="Viaggi", ttype="DEBIT", amount=-round(random.uniform(120, 280), 2)
            )
            net_flow += _add_tx(
                account_id=account_id, y=y, m=m, d=day2,
                desc="Treno/Aereo", category="Viaggi", ttype="DEBIT", amount=-round(random.uniform(70, 180), 2)
            )

        # Accantonamento nel salvadanaio (100€/mese).
        # Usa la funzione ufficiale così crea ANCHE la transazione di conto collegata con piggy_id (esclusa dai report).
        try:
            _insert_piggy_transfer(
                piggy_id=piggy_id,
                account_id=account_id,
                amount=100.0,
                direction="TO_PIGGY",
                note="Accantonamento mensile",
                tx_on_account=True,
                when=date_cls(y, m, min(25, calendar.monthrange(y, m)[1])).isoformat()
            )
        except Exception:
            # in caso di guard-rail o altro, ignora
            pass

        # Eventuale rientro in Agosto (imprevisto): FROM_PIGGY 100–200
        if m == 8 and random.random() < 0.5:
            try:
                _insert_piggy_transfer(
                    piggy_id=piggy_id,
                    account_id=account_id,
                    amount=random.choice([100.0, 150.0, 200.0]),
                    direction="FROM_PIGGY",
                    note="Imprevisto estivo",
                    tx_on_account=True,
                    when=date_cls(y, m, min(28, calendar.monthrange(y, m)[1])).isoformat()
                )
            except Exception:
                pass

    # 4) Aggiorna saldo del conto e del salvadanaio
    #   - saldo finale = base_opening + flusso netto 12 mesi + (flusso piggy già applicato da _insert_piggy_transfer)
    #   - ricalcolo piggy
    db.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (float(base_opening + net_flow), account_id))
    _recalc_piggy(piggy_id)
    db.commit()
    print(f"✅ Dati demo ultimi 12 mesi generati su {account_id}. Saldo base ~{base_opening}€, piggy ricalcolato.")



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

    return render_template(
        'dashboard.html',
        display_name=session.get('display_name') or session.get('codice_cliente'),
        codice_cliente=session.get('codice_cliente'),
        accounts=accounts,
        piggies=piggies,
        transactions=transactions
    )

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
    "summary": "Lista transazioni",
    "tags": ["Transactions"],
    "parameters": [
        {"name": "account_id", "in": "query", "schema": {"type": "string"}, "required": False},
        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200}}
    ],
    "responses": {"200": {"description": "OK"}, "401": {"description": "Non autenticato"}}
})
def api_transactions():
    if not session.get("user_id"):
        return jsonify({"message": "unauthenticated"}), 401
    account_id = request.args.get("account_id")
    try:
        limit = int(request.args.get("limit", 20))
        limit = max(1, min(limit, 200))
    except ValueError:
        limit = 20
    db = get_db()
    if account_id:
        rows = db.execute("""
            SELECT t.transaction_id, t.date, t.description, t.category, t.type, t.amount, t.account_id
            FROM transactions t
            JOIN accounts a ON a.account_id = t.account_id
            WHERE a.user_id = ? AND t.account_id = ?
            ORDER BY t.date DESC, t.created_at DESC
            LIMIT ?
        """, (session["user_id"], account_id, limit)).fetchall()
    else:
        rows = db.execute("""
            SELECT t.transaction_id, t.date, t.description, t.category, t.type, t.amount, t.account_id
            FROM transactions t
            JOIN accounts a ON a.account_id = t.account_id
            WHERE a.user_id = ?
            ORDER BY t.date DESC, t.created_at DESC
            LIMIT ?
        """, (session["user_id"], limit)).fetchall()
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

# --- NEW: API elimina salvadanaio ---
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

# --- REPORT HELPERS ---
from datetime import datetime, timedelta

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

    

# --- Avvio ---
if __name__ == '__main__':
    app.run(debug=True)
