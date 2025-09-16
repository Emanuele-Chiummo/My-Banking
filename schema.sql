PRAGMA foreign_keys = ON;

-- Utenti precensiti (ID testuale, es. USE001)
CREATE TABLE IF NOT EXISTS users (
  user_id        TEXT PRIMARY KEY,              -- es. USE001
  codice_cliente TEXT UNIQUE NOT NULL,          -- es. 123456
  first_name     TEXT NOT NULL,
  last_name      TEXT NOT NULL,
  password_hash  TEXT NOT NULL,
  created_at     TEXT DEFAULT (datetime('now'))
);


-- Conti correnti (1 utente : N conti)
CREATE TABLE IF NOT EXISTS accounts (
  account_id   TEXT PRIMARY KEY,                -- es. ACC001
  user_id      TEXT NOT NULL,
  iban         TEXT UNIQUE,
  name         TEXT,                            -- nome/alias del conto
  currency     TEXT DEFAULT 'EUR',
  balance      REAL DEFAULT 0,                  -- opzionale (puoi anche ricalcolarlo)
  created_at   TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Salvadanaio (1 utente : N salvadanai)
CREATE TABLE IF NOT EXISTS piggy_banks (
  piggy_id       TEXT PRIMARY KEY,              -- es. PIG001
  user_id        TEXT NOT NULL,
  name           TEXT NOT NULL,                 -- es. "Vacanze"
  target_amount  REAL,                          -- obiettivo
  current_amount REAL DEFAULT 0,
  status         TEXT DEFAULT 'ACTIVE',         -- ACTIVE|PAUSED|CLOSED
  created_at     TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Movimenti del salvadanaio (trasferimenti da/verso conto)
CREATE TABLE IF NOT EXISTS piggy_transfers (
  transfer_id TEXT PRIMARY KEY,                 -- es. TRP001
  piggy_id    TEXT NOT NULL,
  account_id  TEXT NOT NULL,
  date        TEXT NOT NULL,                    -- ISO 'YYYY-MM-DD'
  amount      REAL NOT NULL,                    -- positivo
  direction   TEXT NOT NULL,                    -- TO_PIGGY|FROM_PIGGY
  note        TEXT,
  FOREIGN KEY (piggy_id) REFERENCES piggy_banks(piggy_id) ON DELETE CASCADE,
  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

-- Transazioni conto (con collegamento opzionale al salvadanaio)
CREATE TABLE IF NOT EXISTS transactions (
  transaction_id TEXT PRIMARY KEY,              -- es. TRX001
  account_id     TEXT NOT NULL,
  piggy_id       TEXT,                          -- relazione opzionale
  date           TEXT NOT NULL,
  description    TEXT,
  category       TEXT,
  type           TEXT NOT NULL,                 -- DEBIT|CREDIT
  amount         REAL NOT NULL,                 -- segno coerente con 'type'
  created_at     TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE,
  FOREIGN KEY (piggy_id)  REFERENCES piggy_banks(piggy_id) ON DELETE SET NULL
);

-- Contatti P2P dell'utente (rubrica)
CREATE TABLE IF NOT EXISTS contacts (
  contact_id      TEXT PRIMARY KEY,
  owner_user_id   TEXT NOT NULL,              -- l'utente loggato
  display_name    TEXT NOT NULL,
  target_user_id  TEXT,                       -- se contatto è un utente interno
  target_account_id TEXT,                     -- account di destinazione (interno)
  iban            TEXT,                       -- per futuri invii esterni (demo)
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (owner_user_id) REFERENCES users(user_id)
);

-- Trasferimenti P2P interni (istantanei)
CREATE TABLE IF NOT EXISTS p2p_transfers (
  p2p_id           TEXT PRIMARY KEY,
  from_user_id     TEXT NOT NULL,
  to_user_id       TEXT NOT NULL,
  from_account_id  TEXT NOT NULL,
  to_account_id    TEXT NOT NULL,
  amount           REAL NOT NULL,            -- positivo (es. 25.00)
  currency         TEXT NOT NULL DEFAULT 'EUR',
  message          TEXT,
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (from_user_id) REFERENCES users(user_id),
  FOREIGN KEY (to_user_id) REFERENCES users(user_id),
  FOREIGN KEY (from_account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (to_account_id) REFERENCES accounts(account_id)
);

-- Indici utili
CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_piggy_user    ON piggy_banks(user_id);
CREATE INDEX IF NOT EXISTS idx_trx_account   ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_trx_piggy     ON transactions(piggy_id);
