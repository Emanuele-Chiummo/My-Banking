import sqlite3
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

# --- CLI: init-db & seed-demo & seed-demo-data ---
@app.cli.command("init-db")
def init_db_cmd():
    """Crea/ricrea le tabelle dal file schema.sql"""
    schema_path = Path("schema.sql")
    if not schema_path.exists():
        raise SystemExit("schema.sql non trovato nella root del progetto.")
    # ricrea un db pulito
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
    """Popola dati demo: account, transazioni, salvadanaio e trasferimenti"""
    db = get_db()

    # Utente demo (assicurati che esista)
    user_id = "USE001"
    u = db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not u:
        raise SystemExit("Prima esegui: flask --app app seed-demo")

    # Conto principale
    db.execute("""
        INSERT INTO accounts (account_id, user_id, iban, name, currency, balance)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO NOTHING
    """, ("ACC001", user_id, "IT60X0542811101000000123456", "Conto Principale", "EUR", 1250.00))

    # Salvadanaio
    db.execute("""
        INSERT INTO piggy_banks (piggy_id, user_id, name, target_amount, current_amount, status)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(piggy_id) DO NOTHING
    """, ("PIG001", user_id, "Vacanze", 1000.00, 150.00, "ACTIVE"))

    # Transazioni conto (ultimi movimenti)
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

    # Trasferimenti salvadanaio (coerenti con TRX004)
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

    # Ricalcoli semplici
    db.execute("UPDATE accounts SET balance = ? WHERE account_id = 'ACC001'", (1250.00,))
    piggy_sum = db.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN direction='TO_PIGGY' THEN amount ELSE 0 END),0) -
          COALESCE(SUM(CASE WHEN direction='FROM_PIGGY' THEN amount ELSE 0 END),0) AS tot
        FROM piggy_transfers
        WHERE piggy_id = 'PIG001'
    """).fetchone()["tot"]
    db.execute("UPDATE piggy_banks SET current_amount = ? WHERE piggy_id = 'PIG001'", (piggy_sum,))

    db.commit()
    print("✅ Dati demo inseriti.")

# --- Auth Utils (decoratore per le view web) ---
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
            session['user_id'] = user['user_id']     # TEXT (es. USE001)
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

    # Conti dell’utente
    accounts = db.execute("""
        SELECT account_id, name, iban, currency, balance
        FROM accounts
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()

    # Salvadanai
    piggies = db.execute("""
        SELECT piggy_id, name, target_amount, current_amount, status
        FROM piggy_banks
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()

    # Ultime transazioni (limite 10)
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
    "responses": {
        "200": {
            "description": "Login riuscito",
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"message": {"type": "string"}}}
                }
            }
        },
        "401": {"description": "Credenziali non valide"}
    }
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
    "responses": {
        "200": {
            "description": "Elenco conti",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "account_id": {"type": "string"},
                                "name": {"type": "string"},
                                "iban": {"type": "string"},
                                "currency": {"type": "string"},
                                "balance": {"type": "number"}
                            }
                        }
                    }
                }
            }
        },
        "401": {"description": "Non autenticato"}
    }
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
        {
            "name": "account_id",
            "in": "query",
            "schema": {"type": "string"},
            "required": False,
            "description": "Filtra per account_id"
        },
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
            "required": False,
            "description": "Numero massimo di transazioni"
        }
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
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (session["user_id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/piggy/transfer")
@swag_from({
    "summary": "Trasferimento verso/da salvadanaio",
    "description": "Crea un trasferimento TO_PIGGY o FROM_PIGGY e opzionalmente registra una transazione di conto collegata.",
    "tags": ["PiggyBank"],
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "transfer_id": {"type": "string", "example": "TRP999"},
                        "piggy_id": {"type": "string", "example": "PIG001"},
                        "account_id": {"type": "string", "example": "ACC001"},
                        "date": {"type": "string", "example": "2025-09-15"},
                        "amount": {"type": "number", "example": 25.0},
                        "direction": {"type": "string", "enum": ["TO_PIGGY", "FROM_PIGGY"]},
                        "note": {"type": "string", "example": "Accantonamento rapido"},
                        "create_account_tx": {"type": "boolean", "example": True}
                    },
                    "required": ["transfer_id", "piggy_id", "account_id", "date", "amount", "direction"]
                }
            }
        }
    },
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

    db = get_db()
    # Inserisci trasferimento
    db.execute("""
        INSERT INTO piggy_transfers (transfer_id, piggy_id, account_id, date, amount, direction, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(transfer_id) DO NOTHING
    """, (
        data["transfer_id"], data["piggy_id"], data["account_id"],
        data["date"], float(data["amount"]), data["direction"],
        data.get("note")
    ))

    # Aggiorna current_amount del salvadanaio (ricalcolo veloce)
    piggy_sum = db.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN direction='TO_PIGGY' THEN amount ELSE 0 END),0) -
          COALESCE(SUM(CASE WHEN direction='FROM_PIGGY' THEN amount ELSE 0 END),0) AS tot
        FROM piggy_transfers
        WHERE piggy_id = ?
    """, (data["piggy_id"],)).fetchone()["tot"]
    db.execute("UPDATE piggy_banks SET current_amount = ? WHERE piggy_id = ?", (piggy_sum, data["piggy_id"]))

    # (Opzionale) Crea una transazione conto collegata
    if data.get("create_account_tx"):
        # genera un ID fittizio se non passato
        tx_id = f"TRX{data['transfer_id'][3:]}" if str(data["transfer_id"]).startswith("TRP") else f"TRX_{data['transfer_id']}"
        if data["direction"] == "TO_PIGGY":
            amount = -abs(float(data["amount"]))  # uscita dal conto
            category = "Risparmio"
            desc = "Trasferimento verso salvadanaio"
        else:
            amount = abs(float(data["amount"]))   # entrata sul conto
            category = "Risparmio"
            desc = "Prelievo da salvadanaio"

        db.execute("""
            INSERT INTO transactions (transaction_id, account_id, piggy_id, date, description, category, type, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO NOTHING
        """, (
            tx_id, data["account_id"], data["piggy_id"], data["date"],
            desc, category,
            "DEBIT" if amount < 0 else "CREDIT",
            amount
        ))

        # (semplice) non ricalcolo il balance: lascio ai processi batch/riconciliazione

    db.commit()
    return jsonify({"message": "ok"})
# --- Fine API ---

# --- Avvio ---
if __name__ == '__main__':
    app.run(debug=True)
