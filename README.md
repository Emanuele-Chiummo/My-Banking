# My-Banking – Applicazione Web API-Based

## 📖 Introduzione
Il presente progetto, sviluppato nell’ambito del corso di laurea L-31 in Informatica per le Aziende Digitali (Università Pegaso), consiste nella realizzazione di una applicazione **full-stack** orientata ai servizi bancari digitali.  
L’obiettivo principale è la creazione di una piattaforma che consenta di gestire conti correnti, transazioni e strumenti di risparmio (salvadanai), con un’architettura **API-based** e un’interfaccia utente web.

---

## 🚀 Funzionalità implementate
- **Autenticazione e gestione utenti**
  - Accesso tramite codice cliente e password.
  - Gestione della sessione utente autenticata.
  - Password conservate nel database in forma cifrata (hash PBKDF2-SHA256).
  - Presenza di un utente demo precostituito per scopi di test.

- **Gestione dei conti correnti**
  - Creazione di un conto principale associato all’utente demo.
  - Visualizzazione di saldo, IBAN e valuta.

- **Salvadanaio digitale (Piggy Bank)**
  - Possibilità di rappresentare obiettivi di risparmio.
  - Definizione dell’importo target e monitoraggio dell’importo corrente.
  - Trasferimenti verso e dal salvadanaio.

- **Gestione delle transazioni**
  - Registrazione di movimenti in entrata e in uscita.
  - Classificazione per categoria (es. entrate, spese, abbonamenti, ristoranti, risparmio).
  - Visualizzazione degli ultimi movimenti nella dashboard.

- **API REST con documentazione integrata**
  - Interfaccia di documentazione disponibile su `/apidocs` (Swagger UI).
  - Endpoint implementati:
    - `POST /api/login` – autenticazione utente
    - `GET /api/accounts` – recupero dei conti correnti
    - `GET /api/transactions` – recupero delle transazioni (con possibilità di filtro per account)
    - `GET /api/piggy-banks` – recupero dei salvadanai
    - `POST /api/piggy/transfer` – registrazione di trasferimenti da/verso salvadanaio

- **Interfaccia utente**
  - Pagine di login e dashboard realizzate con HTML, CSS (Bootstrap 5) e JavaScript.
  - Visualizzazione sintetica delle informazioni principali dell’utente.
  - Messaggi di notifica (alert) gestiti dinamicamente e chiudibili automaticamente dopo alcuni secondi.

---

## 🛠️ Architettura e Tecnologie
- **Backend**: Python 3.13 con framework Flask.
- **Frontend**: HTML, CSS (Bootstrap 5.3), JavaScript.
- **Database**: SQLite, con schema SQL centralizzato.
- **API Documentation**: Flasgger (Swagger UI).
- **Sicurezza**: Hashing delle password tramite libreria Werkzeug.

---

## ⚙️ Installazione e utilizzo

### 1. Clonare il repository
```bash
git clone <repo_url>
cd My-Banking
```

### 2. Creare l’ambiente virtuale
```bash
python -m venv .venv
.\.venv\Scripts\activate
```
### 3. Installare le dipendenze
```bash
pip install -r requirements.txt
```
### 4. Inizializzare il database e i dati demo
```bash
flask --app app init-db
flask --app app seed-demo
flask --app app seed-demo-data
```
### 5. Avviare l’applicazione
```bash
flask --app app run --debug
```
### 🌐 Accesso ai servizi
- Interfaccia utente: http://127.0.0.1:5000
- Documentazione API: http://127.0.0.1:5000/apidocs

### 🔑 Credenziali demo
Codice Cliente: 123456
Password: Password123!

## 📌 Attività future
 - Implementazione funzionalità trasferimento conto corrente - salvadanaio e viceversa
 - Storico transazioni con filtri avanzati
 - Report e KPI 


## 📝 Note conclusive
Il progetto ha finalità puramente accademiche e dimostrative.
Non è destinato all’utilizzo in produzione reale.
