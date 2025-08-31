# ===============================================
#  Gestione Assistenza - app.py (pagine separate)
#  - Logo:    "logo.png" nella stessa cartella
#  - DB:      "assistenza.db" (creato/aggiornato auto)
#  - Menu:    sidebar con radio (pagine separate)
#  - Excel:   import colonne dinamiche → extra_json
#  - Ticket:  FIFO, allegati, ricerca matricola
#  - Emoji:   nei pulsanti e titoli
#  Autore:    (tuo nome)
# ===============================================

import os
import json
import sqlite3
from datetime import datetime, date
import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO
from streamlit_pdf_viewer import pdf_viewer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image


def check_login():
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False
        st.session_state["username"] = None

    # Se non loggato → mostra form di login
    if not st.session_state["logged_in"]:
        st.subheader("🔐 Login richiesto")

        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        if st.button("Accedi"):
            if "users" not in st.secrets:
                st.error("❌ Nessuna configurazione utenti trovata in `secrets.toml`.")
                st.stop()

            users = st.secrets["users"]

            if username in users and password == users[username]:
                st.session_state["logged_in"] = True
                st.session_state["username"] = username
                st.success(f"✅ Accesso eseguito come **{username}**")
                st.rerun()
            else:
                st.error("❌ Username o password errati.")
        st.stop()
    # Se loggato → mostra un piccolo pannello utente con logout
    else:
        col1, col2 = st.columns([4,1])
        with col1:
            st.info(f"👤 Utente: **{st.session_state['username']}**")
        with col2:
            if st.button("🚪 Logout"):
                st.session_state["logged_in"] = False
                st.session_state["username"] = None
                st.rerun()
    

check_login()

# -------------------------------
# Config pagina + Logo
# -------------------------------
st.set_page_config(page_title="Gestione Assistenza", layout="wide", initial_sidebar_state="expanded")

DB_FILE="assistenza.db"

    
def get_table_columns(conn, table):
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}  # set di nomi colonna

def ensure_columns(conn, table, columns_types: dict):
    existing = get_table_columns(conn, table)
    for col, coltype in columns_types.items():
        if col not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {coltype}')
    conn.commit()

def setup_db():
    conn = sqlite3.connect(DB_FILE)

    # Crea le tabelle se non esistono (solo PK)
    conn.execute('CREATE TABLE IF NOT EXISTS clienti (id INTEGER PRIMARY KEY AUTOINCREMENT)')
    conn.execute('CREATE TABLE IF NOT EXISTS tecnici (id INTEGER PRIMARY KEY AUTOINCREMENT)')
    conn.execute('CREATE TABLE IF NOT EXISTS ticket  (id INTEGER PRIMARY KEY AUTOINCREMENT)')

    # Porta le tabelle allo schema che l’app si aspetta (aggiunge solo se mancano)
    ensure_columns(conn, "clienti", {
        "matricola": "TEXT",
        "codice": "TEXT",
        "azienda": "TEXT",
        "indirizzo": "TEXT",
        "citta": "TEXT",
        "provincia": "TEXT",
    })

    ensure_columns(conn, "tecnici", {
        "nome": "TEXT",
        "cognome": "TEXT",
        "citta": "TEXT",
        "provincia": "TEXT",
        "regione": "TEXT",
        "esperienza": "TEXT",
    })

    ensure_columns(conn, "ticket", {
        "cliente_id": "INTEGER",
        "tecnico_id": "INTEGER",
        "matricola_manual": "TEXT",     # <- mancava nel tuo DB
        "tecnico_manual": "TEXT",       # <- mancava nel tuo DB
        "descrizione": "TEXT",
        "fattura": "REAL",
        "note": "TEXT",
        "data_intervento": "TEXT",
        "intervento_svolto": "TEXT",
        "allegato": "TEXT",
        "stato": "TEXT",
    })

    conn.commit()
    conn.close()
# CHIAMA SUBITO QUESTA FUNZIONE A INIZIO APP
setup_db()
def import_excel_dynamic(conn, table: str, file):
    # leggi excel
    df = pd.read_excel(file)

    # Normalizza i nomi colonna in modo coerente (stessi nomi che userà il DB)
    def normalize(name: str) -> str:
        return (
            str(name)
            .strip()
            .replace("\n", " ")
            .replace("\r", " ")
        )
    df.columns = [normalize(c) for c in df.columns]

    # Aggiungi le colonne mancanti nella tabella come TEXT
    existing = get_table_columns(conn, table)
    for col in df.columns:
        if col not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT')
    conn.commit()

    # Converte eventuali datetime in stringa ISO (SQLite non ha un tipo datetime nativo)
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")

    # Sostituisci NaN con stringa vuota
    df = df.fillna("")

    # Inserisci (ora le colonne esistono tutte)
    df.to_sql(table, conn, if_exists="append", index=False)
    
# -------------------------------
# Utility DB
# -------------------------------

def connetti_db():
    """Connessione SQLite con foreign_keys attive e Row factory."""
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def crea_o_migra_db():
    """Crea le tabelle se non esistono e aggiunge le colonne mancanti (migrazioni leggere)."""
    con = connetti_db()
    cur = con.cursor()

    # CLIENTI: colonne base + extra_json
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clienti (
        id INTEGER PRIMARY KEY,
        matricola TEXT NOT NULL UNIQUE,
        azienda TEXT NOT NULL,
        indirizzo TEXT,
        contatto TEXT,
        extra_json TEXT DEFAULT '{}'
    )
    """)

    # TECNICI: colonne base + extra_json
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tecnici (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        codice TEXT UNIQUE,
        regione TEXT,
        contatto TEXT,
        extra_json TEXT DEFAULT '{}'
    )
    """)

    # TICKET
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ticket (
        id INTEGER PRIMARY KEY,
        id_cliente INTEGER NOT NULL,
        id_tecnico INTEGER,
        id_prodotto INTEGER,                -- opzionale in futuro
        problema TEXT NOT NULL,
        stato TEXT NOT NULL DEFAULT 'Aperto',
        referente TEXT,
        data_creazione TEXT NOT NULL,
        criticita TEXT DEFAULT 'Media',
        data_intervento TEXT,               -- può rimanere NULL, si autoimposta su "in lavorazione" o "chiuso"
        intervento_svolto TEXT,
        allegato_nome TEXT,
        allegato_percorso TEXT,
        importo REAL,
        extra_json TEXT DEFAULT '{}',
        FOREIGN KEY (id_cliente) REFERENCES clienti (id) ON DELETE CASCADE,
        FOREIGN KEY (id_tecnico) REFERENCES tecnici (id) ON DELETE SET NULL
    )
    """)

    # Migrazioni leggere: aggiungi colonne se mancano
    def ensure_column(table, col_def):
        # col_def: "nome_colonna TIPO DEFAULT '...'"
        col_name = col_def.split()[0]
        info = cur.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {r['name'] for r in info}
        if col_name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

    # Clienti
    ensure_column("clienti", "extra_json TEXT DEFAULT '{}'")
    # Tecnici
    ensure_column("tecnici", "extra_json TEXT DEFAULT '{}'")

    # Ticket (in caso di DB vecchio)
    ensure_column("ticket", "data_intervento TEXT")
    ensure_column("ticket", "intervento_svolto TEXT")
    ensure_column("ticket", "allegato_nome TEXT")
    ensure_column("ticket", "allegato_percorso TEXT")
    ensure_column("ticket", "extra_json TEXT DEFAULT '{}'")

    con.commit()
    con.close()

def get_all(table, order_by="id DESC"):
    con = connetti_db()
    rows = con.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
    con.close()
    return rows

def to_safe_date_str(d):
    """Converte in 'YYYY-MM-DD' se possibile, altrimenti None."""
    if d is None or d == "":
        return None
    if isinstance(d, (datetime, date)):
        return d.strftime("%Y-%m-%d")
    # Stringa: prova a normalizzare
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        return str(d)

def df_from_rows(rows):
    """Converte list[sqlite3.Row] in pandas.DataFrame."""
    if not rows:
        return pd.DataFrame()
    # sqlite3.Row si comporta come mappa
    return pd.DataFrame([dict(r) for r in rows])

def ensure_json(obj):
    """Ritorna un dict a partire da JSON string o dict, altrimenti {}."""
    if obj is None or obj == "":
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        return json.loads(obj)
    except Exception:
        return {}

# --------------------------------
# Import/Export helpers
# --------------------------------

def normalize_headers(cols):
    """Normalizza intestazioni: strip, upper/lower coese, sostituzioni spazi→_ ."""
    norm = []
    seen = {}
    for c in cols:
        base = str(c).strip()
        base = base.replace("\n", " ").replace("\r", " ")
        base = base.replace("  ", " ")
        base = base.lower()
        base = base.replace(" ", "_")
        base = base.replace("-", "_")
        base = base.replace("/", "_")
        # evita duplicati
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
        norm.append(base)
    return norm

def split_known_and_extra(df, known_cols):
    """Divide DF in (known_dict, extra_dict) per riga, restituendo lista di dict per INSERT."""
    df = df.copy()
    df.columns = normalize_headers(df.columns)
    records = []
    for _, row in df.iterrows():
        row_dict = {k: row.get(k, None) for k in df.columns}
        known = {k: row_dict.get(k) for k in known_cols if k in row_dict}
        extra_items = {k: row_dict[k] for k in row_dict.keys() if k not in known_cols}
        # serializza eventuali Timestamp in stringa
        for k, v in list(known.items()):
            if isinstance(v, (pd.Timestamp, datetime, date)):
                known[k] = to_safe_date_str(v)
        for k, v in list(extra_items.items()):
            if isinstance(v, (pd.Timestamp, datetime, date)):
                extra_items[k] = to_safe_date_str(v)
        records.append((known, extra_items))
    return records
             ## NOTICE TICKET APERTI# 
# Assicura che DB_PATH esista (adatta il valore se il tuo nome è diverso)
#try:
#    DB_PATH
#except NameError:
DB_PATH = "assistenza.db"
    
def get_ticket_aperti(limit=200):
    """
    Recupera i ticket con stato 'Aperto' o 'In lavorazione'.
    Ritorna un DataFrame (puoi usare limit per non caricare troppi record).
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                t.id,
                t.matricola,
                COALESCE(t.azienda, c.azienda, '') AS azienda,
                COALESCE(t.tecnico_nome, '') AS tecnico_nome,
                t.descrizione,
                t.stato,
                t.data_creazione
            FROM ticket t
            LEFT JOIN clienti c ON c.id = t.cliente_id
            WHERE t.stato IN ('Aperto','In lavorazione')
            ORDER BY datetime(t.data_creazione) DESC
            LIMIT ?
            """,
            conn,
            params=(limit,)
        )
    finally:
        conn.close()

    # sicurezza: assicurati che le colonne esistano anche se vuote
    for col in ["id","matricola","azienda","tecnico_nome","descrizione","stato","data_creazione"]:
        if col not in df.columns:
            df[col] = ""
    return df

def mostra_popup_ticket_aperti(df_open):
    """
    Mostra il popup nativo se disponibile, altrimenti fallback con expander.
    Chiamare questa funzione quando si vuole forzare la visualizzazione.
    """
    if df_open.empty:
        return

    # usa st.dialog se disponibile (Streamlit moderno)
    if hasattr(st, "dialog"):
        @st.dialog("⚠️ TICKET APERTI (aperto/lavorazione)")
        def _modal():
            st.caption("ticket pendenti in lavorazione.")
            st.dataframe(df_open, use_container_width=True, hide_index=True)
            if st.button("Chiudi"):
                # segna come mostrato per questa sessione
                st.session_state["open_tickets_seen"] = True
                st.rerun()
        _modal()
    else:
        # fallback semplice (expander)
        with st.expander("⚠️ Ticket APERTI (aperto/lavorazione)", expanded=True):
            st.caption(" ticket in lavorazione.")
            st.dataframe(df_open, use_container_width=True, hide_index=True)
            if st.button("Chiudi avviso (expander)"):
                st.session_state["open_tickets_seen"] = True
                st.rerun()

# ---------- Inizializzazione stato e visualizzazione (metti questo in cima allo script) ----------
# inizializza flag sessione (una volta per sessione)
if "open_tickets_seen" not in st.session_state:
    st.session_state["open_tickets_seen"] = False

# --------------------------------
# UI: Sidebar
# --------------------------------
LOGO_PATH = "logo.png"

with st.sidebar:
    # Logo
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=180)

    # Navigazione
    st.markdown("### 🧭 Navigazione")
    menu = st.radio("Scegli la sezione:", [
        "🏠 Gestione PV",
        "👷 Gestione Tecnici",
        "🎫 Ticket di Assistenza",
        "🖨️ Stampa Ticket",
        "📊 Analisi Ticket",
        "🛠️ Tools",
        "⬆️🏠 Up_load Clienti",
        "⬆️👷 Up_load Tecnici",
        "📤 Import/Export",
    ])

    # Ticket aperti
    try:
        df_open = get_ticket_aperti(limit=300)
        if not df_open.empty:
            if st.button("🔔 Ticket aperti"):
                st.session_state["open_tickets_seen"] = False  # forza riapertura
    except Exception as e:
        st.error(f"Errore nel recupero ticket aperti: {e}")
        df_open = pd.DataFrame()  # fallback vuoto


# ==========================
# MOSTRA POPUP UNA SOLA VOLTA
# ==========================
if not df_open.empty:
    if not st.session_state.get("open_tickets_seen", False):
        mostra_popup_ticket_aperti(df_open)
        st.session_state["open_tickets_seen"] = True


# --------------------------------
# Avvio: crea/migra DB
# --------------------------------
crea_o_migra_db()
# 📄 Pagina CLIENTI 

DB_FILE = "assistenza.db"  # 🔹 modifica con il tuo path al DB

# -------------------------
# Funzione di importazione dinamica
# -------------------------
def import_excel_dynamic(conn, table_name, file):
    """
    Importa un file Excel dentro la tabella SQLite.
    Se trova un vincolo UNIQUE/PRIMARY KEY, aggiorna invece di dare errore.
    """

    # Carica Excel in DataFrame
    df = pd.read_excel(file)

    # Rimuove eventuali spazi nei nomi colonne
    df.columns = [c.strip() for c in df.columns]

    cur = conn.cursor()

    # Otteniamo le colonne della tabella
    cur.execute(f"PRAGMA table_info({table_name})")
    table_info = cur.fetchall()
    table_cols = [row[1] for row in table_info]

    # Filtriamo solo le colonne comuni
    df = df[[c for c in df.columns if c in table_cols]]

    if df.empty:
        raise ValueError("⚠️ Nessuna colonna del file corrisponde alla tabella.")

    # Troviamo chiavi uniche o primary key
    cur.execute(f"PRAGMA index_list({table_name})")
    indexes = cur.fetchall()

    unique_cols = []
    for idx in indexes:
        if idx[2]:  # se è UNIQUE
            cur.execute(f"PRAGMA index_info({idx[1]})")
            unique_cols = [r[2] for r in cur.fetchall()]
            break

    # Se non esiste UNIQUE, usiamo la primary key
    if not unique_cols:
        pk_cols = [row[1] for row in table_info if row[5] == 1]  # row[5] = PK flag
        unique_cols = pk_cols

    if not unique_cols:
        raise ValueError(f"⚠️ La tabella {table_name} non ha UNIQUE o PRIMARY KEY.")

    # Costruiamo query dinamica
    cols = ", ".join(df.columns)
    placeholders = ", ".join(["?"] * len(df.columns))
    update_clause = ", ".join([f"{col}=excluded.{col}" for col in df.columns if col not in unique_cols])
    conflict_cols = ", ".join(unique_cols)

    sql = f"""
    INSERT INTO {table_name} ({cols})
    VALUES ({placeholders})
    ON CONFLICT({conflict_cols}) DO UPDATE SET
    {update_clause};
    """

    # Inseriamo/aggiorniamo riga per riga
    for _, row in df.iterrows():
        cur.execute(sql, tuple(row))

    conn.commit()


# -------------------------
# Pagina IMPORT CLIENTI
# -------------------------
def pagina_upload_clienti():
    st.header("⬆️🏠 Up_load Clienti")

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")

    # 📥 Importazione da Excel
    st.subheader("📥 Importa Clienti da Excel")
    file_clienti = st.file_uploader("Carica file Excel Clienti", type=["xlsx"])
    if file_clienti:
        try:
            import_excel_dynamic(conn, "clienti", file_clienti)
            st.success("✅ Clienti importati correttamente!")
        except Exception as e:
            st.error(f"Errore import clienti: {e}")
        finally:
            conn.close()

    # 📋 Lista clienti
    try:
        conn = sqlite3.connect(DB_FILE)
        clienti_df = pd.read_sql_query(
            "SELECT id, matricola, codice, azienda, indirizzo, citta, provincia FROM clienti ORDER BY azienda ASC, matricola ASC",
            conn
        )
        st.dataframe(clienti_df, use_container_width=True)
        conn.close()
    except Exception as e:
        st.warning(f"Nessun cliente trovato o errore: {e}")


# -------------------------
# Pagina IMPORT TECNICI
# -------------------------
def pagina_upload_tecnici():
    st.header("⬆️👷 Up_load Tecnici")

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")

    # 📥 Importazione da Excel
    st.subheader("📥 Importa Tecnici da Excel")
    file_tecnici = st.file_uploader("Carica file Excel Tecnici", type=["xlsx"])
    if file_tecnici:
        try:
            import_excel_dynamic(conn, "tecnici", file_tecnici)
            st.success("✅ Tecnici importati correttamente!")
        except Exception as e:
            st.error(f"Errore import tecnici: {e}")
        finally:
            conn.close()

    # 📋 Lista tecnici
    try:
        conn = sqlite3.connect(DB_FILE)
        tecnici_df = pd.read_sql_query(
            "SELECT id, nome, citta, provincia, regione FROM tecnici ORDER BY nome ASC",
            conn
        )
        st.dataframe(tecnici_df, use_container_width=True)
        conn.close()
    except Exception as e:
        st.warning(f"Nessun tecnico trovato o errore: {e}")
    finally:
        conn.close()


# -------------------------
# Utility di formattazione
# -------------------------
def format_cliente(row):
    parts = [row.get("matricola"), row.get("azienda"), row.get("indirizzo"), row.get("citta")]
    provincia = row.get("provincia")
    if provincia:
        parts[-1] = f"{parts[-1]} ({provincia})" if parts[-1] else provincia
    return " - ".join([p for p in parts if p and str(p).strip() != ""])


def format_tecnico(row):
    parts = [row.get("nome"), row.get("citta")]
    provincia = row.get("provincia")
    if provincia:
        parts[-1] = f"{parts[-1]} ({provincia})" if parts[-1] else provincia
    parts.append(row.get("regione"))
    return " - ".join([p for p in parts if p and str(p).strip() != ""])

# PAGINA: TICKET
# ===========================================


# === Directory allegati ===
ALLEGATI_DIR = "allegati"
if not os.path.exists(ALLEGATI_DIR):
    os.makedirs(ALLEGATI_DIR)


# === Funzioni helper ===
def format_cliente(row):
    parts = [row.get("matricola"), row.get("azienda"), row.get("indirizzo"), row.get("citta")]
    provincia = row.get("provincia")
    if provincia:
        parts[-1] = f"{parts[-1]} ({provincia})" if parts[-1] else provincia
    return " - ".join([p for p in parts if p and str(p).strip() != ""])


def format_tecnico(row):
    parts = [row.get("esperienza"),row.get("nome"),  row.get("telefono"), row.get("cellulare"), row.get("referente"), row.get("citta") ]
    provincia = row.get("provincia")
    
    if provincia:
        parts[-1] = f"{parts[-1]} ({provincia})" if parts[-1] else provincia
    parts.append(row.get("regione"))
    return " - ".join([p for p in parts if p and str(p).strip() != ""])

def ensure_ticket_regione_column(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(ticket)")
    cols = [r[1] for r in cur.fetchall()]
    if "regione" not in cols:
        cur.execute("ALTER TABLE ticket ADD COLUMN regione TEXT")
        conn.commit()
    conn.close()


def salva_ticket(
    matricola, modello, cliente_id, codice, azienda, indirizzo_cliente,
    citta_cliente, provincia_cliente, contatto,
    tecnico_id, tecnico_nome, citta_tecnico, provincia_tecnico,
    descrizione, intervento_svolto, note, fattura, data_intervento,
    stato, guasto, allegato_path,
    *, tecnico_regione=None   # <-- opzionale, solo keyword
):
    """
    Inserisce un ticket nello schema esistente + snapshot 'regione' del tecnico (se presente).
    I nomi colonna sono allineati alla tua tabella 'ticket'.
    """

    # Normalizza data_intervento in stringa YYYY-MM-DD o None
    if isinstance(data_intervento, (datetime, date)):
        data_intervento_str = data_intervento.strftime("%Y-%m-%d")
    elif isinstance(data_intervento, str):
        data_intervento_str = data_intervento  # già stringa
    else:
        data_intervento_str = None

    # Normalizza fattura
    try:
        fattura_val = float(fattura) if fattura is not None else 0.0
    except Exception:
        fattura_val = 0.0

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO ticket (
            matricola, modello, cliente_id, codice, azienda, indirizzo_cliente, citta_cliente, provincia_cliente,
            contatto, tecnico_id, tecnico_nome, citta_tecnico, provincia_tecnico, regione,
            descrizione, intervento_svolto, note, fattura, data_intervento, stato, guasto, allegato, data_creazione
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        matricola, modello, cliente_id, codice, azienda, indirizzo_cliente, citta_cliente, provincia_cliente,
        contatto, tecnico_id, tecnico_nome, citta_tecnico, provincia_tecnico, tecnico_regione,
        descrizione, intervento_svolto, note, fattura_val, data_intervento_str, stato, guasto, allegato_path, now_str
    ))

    conn.commit()
    conn.close()


def mostra_popup_ticket_aperti(df):
    """
    Mostra un popup modale nativo (st.dialog) con i ticket aperti/in lavorazione.
    """
     # --- se c'è st.dialog, usa il modale vero ---
    if hasattr(st, "dialog"):
        @st.dialog("⚠️ Ticket aperti / in lavorazione")
        def _modal():
            st.caption("Controlla i ticket pendenti prima di crearne uno nuovo.")
            st.dataframe(df, use_container_width=True, hide_index=True)
            if st.button("Chiudi"):
                st.session_state["open_tickets_seen"] = True
                st.rerun()
        _modal()
    else:
        # --- fallback (expander) ---
        st.warning(f"⚠️ Ci sono {len(df)} ticket aperti/in lavorazione.")
        with st.expander("📋 Vedi elenco ticket pendenti", expanded=True):
            st.dataframe(df, use_container_width=True, hide_index=True)
            if st.button("Ho letto"):
                st.session_state["open_tickets_seen"] = True
                st.rerun()

def pagina_ticket():
    st.header("🎫 GESTIONE TICKET")
    
    # carica ticket aperti / in lavorazione
    con = sqlite3.connect(DB_FILE)
    df_open = pd.read_sql_query("""
        SELECT t.id,
               c.matricola,
               c.azienda,
               t.indirizzo_cliente,
               t.citta_cliente,
               t.provincia_cliente,
               t.tecnico_nome,
               t.descrizione,
               t.stato,
               t.data_creazione
        FROM ticket t
        LEFT JOIN clienti c ON c.id = t.cliente_id
        WHERE t.stato IN ('Aperto','In lavorazione')
        ORDER BY datetime(t.data_creazione) ASC, t.id ASC
    """, con)
    con.close()

     
    # mostra il popup solo se:
    # - ci sono ticket aperti
    # - non è già stato chiuso in questa sessione
    if not df_open.empty and not st.session_state.get("open_tickets_seen", False):
        mostra_popup_ticket_aperti(df_open)

    #### opzionale: bottone per riaprire il popup quando vuoi
    #with st.sidebar:
        #if not df_open.empty and st.button("🔔 Ticket aperti"):
            #st.session_state["open_tickets_seen"] = False
            # se c'è st.dialog, aprilo subito
            #if hasattr(st, "dialog"):
                #mostra_popup_ticket_aperti(df_open)##
   
    
    
    st.subheader("➕ Nuovo Ticket ")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # --- Caricamento clienti e tecnici ---
    clienti_df = pd.read_sql_query("""
        SELECT id, matricola, modello, codice, azienda, indirizzo, citta, provincia, contatto
        FROM clienti
        ORDER BY matricola ASC
    """, conn)

    tecnici_df = pd.read_sql_query("""
        SELECT id, nome, citta, provincia, regione, esperienza, telefono, cellulare, referente
        FROM tecnici
        ORDER BY nome ASC
    """, conn)

    # --- Ricerca cliente ---
    search_cliente = st.text_input("🔍 Cerca cliente per matricola, azienda, città...")
    if search_cliente:
        clienti_df = clienti_df[
            clienti_df.apply(lambda row: search_cliente.lower() in format_cliente(row).lower(), axis=1)
        ]
    cliente_map = {format_cliente(row): row for _, row in clienti_df.iterrows()}
    cliente_sel = st.selectbox("📌 Seleziona Cliente", list(cliente_map.keys())) if not clienti_df.empty else None

    # --- Ricerca tecnico ---
    search_tecnico = st.text_input("🔍 Cerca tecnico per nome, città, regione...")
    if search_tecnico:
        tecnici_df = tecnici_df[
            tecnici_df.apply(lambda row: search_tecnico.lower() in format_tecnico(row).lower(), axis=1)
        ]
    tecnico_map = {format_tecnico(row): row for _, row in tecnici_df.iterrows()}
    tecnico_sel = st.selectbox("🛠️ Seleziona Tecnico", list(tecnico_map.keys())) if not tecnici_df.empty else None
    
    # --- Bottone Calcolo distanza ---
    if cliente_sel and tecnico_sel:
        cliente = cliente_map[cliente_sel]
        tecnico = tecnico_map[tecnico_sel]

        indirizzo_cliente = f"{cliente['indirizzo']} {cliente['citta']} {cliente['provincia']}"
        indirizzo_tecnico = f"{tecnico['citta']} {tecnico['provincia']}"

        url_maps = f"https://www.google.com/maps/dir/{indirizzo_cliente}/{indirizzo_tecnico}"

        st.markdown(
            f'<a href="{url_maps}" target="_blank">'
            '<button style="padding:10px; font-size:16px; background-color:#4CAF50; color:white; border:none; border-radius:8px;">'
            '🌍 Calcola distanza Cliente → Tecnico'
            '</button></a>',
            unsafe_allow_html=True
        )
    # --- Form nuovo ticket ---
    with st.form("nuovo_ticket_form", clear_on_submit=True):

        if cliente_sel:
            st.caption(f"🏠 Cliente selezionato: {cliente_sel}")
        if tecnico_sel:
            st.caption(f"🛠️ Tecnico selezionato: {tecnico_sel}")

        cliente_obj = cliente_map[cliente_sel] if cliente_sel else None
        matricola_default = cliente_obj["matricola"] if cliente_obj is not None else ""
        modello_default = cliente_obj["modello"] if cliente_obj is not None else ""
        contatto_default = cliente_obj["contatto"] if cliente_obj is not None else ""

        matricola = st.text_input("Matricola", value=matricola_default)
        modello = st.text_input("Modello", value=modello_default)
        contatto = st.text_area("Contatto", value=contatto_default)
        descrizione = st.text_area("Inserire descrizione criticità")
        intervento_svolto = st.text_area("Descrizione intervento svolto")
        note = st.text_area("Note aggiuntive")
        fattura = st.number_input("Fattura (€)", min_value=0.0, format="%.2f")
        data_intervento = st.date_input("Data intervento", value=None)
        stato = st.selectbox("Stato", ["Aperto", "In lavorazione", "Chiuso"])
        allegato = st.file_uploader("Carica Allegato (opzionale)", type=["pdf", "jpg", "png"])

        critical_df = pd.read_sql_query("SELECT codice_guasto, guasto FROM critical ORDER BY codice_guasto ASC", conn)
        if not critical_df.empty:
            guasto_map = { 
                f"{row['codice_guasto']} - {row['guasto']}": f"{row['codice_guasto']} - {row['guasto']}"
                for _, row in critical_df.iterrows()
                }
            guasto_sel = st.selectbox("⚠️ Seleziona Guasto", list(guasto_map.keys()))
        else:
            guasto_sel = None
            st.warning("⚠️ Nessun guasto configurato in tabella 'critical'.")

        submitted = st.form_submit_button("💾 Salva Ticket")

        if submitted:
            if not cliente_sel or not tecnico_sel:
                st.error("❌ Devi selezionare sia un cliente che un tecnico.")
            else:
                cliente = cliente_map[cliente_sel]
                tecnico = tecnico_map[tecnico_sel]

                # Salvataggio allegato
                allegato_path = None
                if allegato:
                    allegato_path = os.path.join(ALLEGATI_DIR, allegato.name)
                    with open(allegato_path, "wb") as f:
                        f.write(allegato.getbuffer())


                salva_ticket(
                    matricola, modello, cliente["id"], cliente["codice"], cliente["azienda"],
                    cliente["indirizzo"], cliente["citta"], cliente["provincia"], contatto,
                    tecnico["id"], tecnico["nome"], tecnico["citta"], tecnico["provincia"],
                    descrizione, intervento_svolto, note, fattura,
                    data_intervento, stato, guasto_map[guasto_sel] if guasto_sel else None,
                    allegato_path,
                    tecnico_regione=tecnico.get("regione")  # <-- opzionale
                )
                st.success("✅ Ticket creato con successo!")
                st.rerun()
    # ===============================
    st.subheader("📋 Visualizza / Gestisci Ticket")

    col1, col2 = st.columns([2,1])
    with col1:
        matricola_search = st.text_input("🔍 Cerca Ticket per matricola", key="ricerca_ticket")
    with col2:
        stato_filter = st.selectbox("📌 Filtra per stato", ["Tutti", "Aperto", "In lavorazione", "Chiuso"], key="filtro_stato")

    # 🔎 Query SENZA JOIN → uso solo snapshot salvati in ticket
    df_tickets = pd.read_sql_query("""
        SELECT 
            id,
            matricola,
            modello,
            azienda,
            indirizzo_cliente,
            citta_cliente,
            provincia_cliente,
            regione,
            contatto,
            tecnico_nome,
            citta_tecnico,
            provincia_tecnico,
            descrizione,
            fattura,
            note,
            data_intervento,
            intervento_svolto,
            stato,
            guasto,
            allegato,
            data_creazione
        FROM ticket
        ORDER BY id DESC
    """, conn)

    # --- Filtro matricola ---
    if matricola_search:
        df_tickets = df_tickets[df_tickets["matricola"].str.contains(matricola_search, case=False, na=False)]

    # --- Filtro stato ---
    if stato_filter != "Tutti":
        df_tickets = df_tickets[df_tickets["stato"] == stato_filter]

    if not df_tickets.empty:
        st.dataframe(df_tickets)

        # Creiamo un dizionario "ticket_map" utile per selezionare i record
        ticket_map = {
            f"ID {row['id']} - {row['matricola']} - {row['azienda']} - {row['tecnico_nome']} - {row['stato']}": row 
            for _, row in df_tickets.iterrows()
        }

    # --- Modifica Ticket ---
    st.subheader("✏️ Modifica Ticket")

    # 🔍 Ricerca Ticket (ora solo in campi snapshot)
    search_ticket = st.text_input("🔍 Cerca ticket per matricola, azienda, indirizzo, citta, tecnico, descrizione o guasto...")

    query = """
        SELECT *
        FROM ticket
        WHERE matricola LIKE ? 
        OR azienda LIKE ?
        OR indirizzo_cliente LIKE ? 
        OR citta_cliente LIKE ? 
        OR tecnico_nome LIKE ?
        OR guasto LIKE ?
        OR descrizione LIKE ?
        ORDER BY data_creazione DESC
        LIMIT 50
    """
    cursor.execute(query, (
        f"%{search_ticket}%", f"%{search_ticket}%", f"%{search_ticket}%",
        f"%{search_ticket}%", f"%{search_ticket}%", f"%{search_ticket}%", f"%{search_ticket}%"
    ))
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]

    tickets = [dict(zip(cols, row)) for row in rows]

    if tickets:
        ticket_map = {
            f"#{t['id']} | Matricola: {t['matricola']} | {t['azienda']} | {t['indirizzo_cliente']} | {t['citta_cliente']} | {t['provincia_cliente']} | Tecnico: {t['tecnico_nome']} | Guasto: {t['guasto']}": t
            for t in tickets
        }
        ticket_sel = st.selectbox("📌 Seleziona Ticket", list(ticket_map.keys()))
    else:
        ticket_sel = None
        st.info("Nessun ticket trovato.")

    if ticket_sel:
        ticket_data = ticket_map[ticket_sel]
        ticket_id = ticket_data["id"]

        # --- Mostra allegato esistente ---
        if ticket_data["allegato"]:
            st.markdown("📎 **Allegato esistente:**")
            ext = os.path.splitext(ticket_data["allegato"])[-1].lower()
            try:
                if ext in [".jpg", ".jpeg", ".png"]:
                    st.image(ticket_data["allegato"], caption="Anteprima immagine", use_column_width=True)
                elif ext == ".pdf":
                    from streamlit_pdf_viewer import pdf_viewer
                    with open(ticket_data["allegato"], "rb") as f:
                        pdf_bytes = f.read()
                    pdf_viewer(input=pdf_bytes, width=600)
                else:
                    st.info(f"📂 Allegato salvato: {ticket_data['allegato']}")
            except Exception as e:
                st.warning(f"⚠️ Impossibile caricare l'anteprima: {e}")

        # --- Recupero lista guasti ---
        critical_df = pd.read_sql_query("SELECT codice_guasto, guasto FROM critical ORDER BY codice_guasto ASC", conn)
        guasto_map = { 
            f"{row['codice_guasto']} - {row['guasto']}": f"{row['codice_guasto']} - {row['guasto']}"
            for _, row in critical_df.iterrows()
        }

        # Guasto già salvato → pre-selezione
        guasto_default = None
        if ticket_data.get("guasto"):
            for k, v in guasto_map.items():
                if v == ticket_data["guasto"]:
                    guasto_default = k
                    break

        # --- Form di modifica ticket ---
        with st.form("modifica_ticket_form", clear_on_submit=False):
            descrizione = st.text_area("Descrizione", value=ticket_data["descrizione"])
            intervento_svolto = st.text_area("Intervento svolto", value=ticket_data["intervento_svolto"])
            note = st.text_area("Note", value=ticket_data["note"])
            fattura = st.number_input("Fattura (€)", min_value=0.0, format="%.2f", value=ticket_data["fattura"] or 0.0)
            data_intervento = st.date_input(
                "Data intervento",
                value=datetime.strptime(ticket_data["data_intervento"], "%Y-%m-%d").date() if ticket_data["data_intervento"] else None
            )
            stato = st.selectbox(
                "Stato", ["Aperto", "In lavorazione", "Chiuso"],
                index=["Aperto", "In lavorazione", "Chiuso"].index(ticket_data["stato"])
            )

            # --- Campo guasto ---
            guasto_sel = st.selectbox(
                "⚠️ Seleziona Guasto",
                list(guasto_map.keys()),
                index=list(guasto_map.keys()).index(guasto_default) if guasto_default else 0
            )

            # --- Upload nuovo allegato ---
            nuovo_allegato = st.file_uploader("🔄 Carica nuovo allegato (opzionale)", type=["pdf", "jpg", "jpeg", "png"])
            if nuovo_allegato:
                ext = nuovo_allegato.type.split("/")[-1]
                if ext in ["jpg", "jpeg", "png"]:
                    st.image(nuovo_allegato, caption="Nuova immagine", use_column_width=True)
                elif ext == "pdf":
                    from streamlit_pdf_viewer import pdf_viewer
                    pdf_bytes = nuovo_allegato.getvalue()
                    pdf_viewer(input=pdf_bytes, width=600)

            # --- Bottone submit ---
            submitted = st.form_submit_button("💾 Salva Modifiche")

        if submitted:
            allegato_path = ticket_data["allegato"]
            if nuovo_allegato:
                allegato_path = os.path.join(ALLEGATI_DIR, nuovo_allegato.name)
                with open(allegato_path, "wb") as f:
                    f.write(nuovo_allegato.getbuffer())

            cursor.execute("""
                UPDATE ticket SET
                    descrizione=?, intervento_svolto=?, note=?, fattura=?, data_intervento=?, stato=?, guasto=?, allegato=?
                WHERE id=?
            """, (
                descrizione, intervento_svolto, note, fattura,
                data_intervento.strftime("%Y-%m-%d") if data_intervento else None,
                stato, guasto_map[guasto_sel], allegato_path, ticket_id
            ))
            conn.commit()
            st.success("✅ Ticket aggiornato con successo!")
            st.rerun()

# --- Cancella Ticket ---
    st.subheader("🗑️ Cancella Ticket")
    search_del = st.text_input("🔍 Inserisci matricola per cercare ticket da cancellare", key="search_cancella")
    df_del = df_tickets[df_tickets["matricola"].str.contains(search_del, case=False, na=False)] if search_del else df_tickets

    if not df_del.empty:
        ticket_del_map = {f"ID {row['id']} - {row['matricola']} - {row['azienda']} - {row['indirizzo_cliente']} - {row['citta_cliente']} - {row['provincia_cliente']} - {row['tecnico_nome']}": row for _, row in df_del.iterrows()}
        ticket_del = st.selectbox("Seleziona un ticket da eliminare", list(ticket_del_map.keys()), key="delete_ticket")
        if st.button("❌ Elimina Ticket", key="confirm_delete_ticket"):
            ticket_id = ticket_del_map[ticket_del]["id"]
            cursor.execute("DELETE FROM ticket WHERE id = ?", (ticket_id,))
            conn.commit()
            st.success(f"✅ Ticket {ticket_id} eliminato con successo!")
            st.rerun()
            
    # -------------------------
    # RESET COMPLETO
    # -------------------------
    st.subheader("🧹 Reset Database Ticket")

    if st.button("⚠️ Elimina tutti i ticket e resetta ID"):
        reset_ticket()
        st.success("✅ Tutti i ticket eliminati e ID resettato!")
        st.rerun()      
            

## ANALISI TICKET 

DB_PATH = "assistenza.db"  # aggiorna se serve

def carica_ticket():
    conn = sqlite3.connect("assistenza.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            t.id, t.data_creazione, t.descrizione, t.stato, t.guasto,
            t.fattura, t.matricola, t.azienda, t.citta_cliente, t.provincia_cliente,
            t.regione_tecnico, t.tecnico_nome
        FROM ticket t
        ORDER BY datetime(t.data_creazione) DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return pd.DataFrame(rows)

def pagina_analisi_ticket():
    st.title("📊 Analisi Ticket")

    conn = sqlite3.connect("assistenza.db")
    df = pd.read_sql_query("SELECT * FROM ticket", conn)
    conn.close()

    if df.empty:
        st.warning("⚠️ Nessun ticket disponibile.")
        return
    # --- Conversioni data e fattura ---
    df["data_intervento"] = pd.to_datetime(df["data_intervento"], errors="coerce")
    df["fattura"] = pd.to_numeric(df["fattura"], errors="coerce").fillna(0)
    
    # Conversione sicura della data
    ## df["data_intervento"] = pd.to_datetime(df["data_intervento"], errors="coerce")

    # Sostituisco eventuali NaT con oggi (per non avere min/max rotti)
    if df["data_intervento"].isnull().all():
        data_min = pd.to_datetime("today")
        data_max = pd.to_datetime("today")
    else:
        data_min = df["data_intervento"].min()
        data_max = df["data_intervento"].max()

    

    # ------------------------------
    # FILTRI
    # ------------------------------
    st.subheader("🔍 Filtri di ricerca")
    col1, col2, col3 = st.columns(3)
    col4, col5, col6 = st.columns(3)

    # Matricola (sempre manuale)
    matricola = col1.text_input("🏷 MATRICOLA")

    # Azienda
    azienda = None
    if "azienda" in df.columns:
        aziende_uniche = sorted([a for a in df["azienda"].fillna("").unique().tolist() if a != ""])
        azienda = col2.selectbox("🏠 AZIENDA", ["Tutte"] + aziende_uniche)

    # Provincia
    provincia = None
    if "provincia_cliente" in df.columns:
        province_uniche = sorted([p for p in df["provincia_cliente"].fillna("").unique().tolist() if p != ""])
        provincia = col3.selectbox("🏞 PROVINCIA", ["Tutte"] + province_uniche)

    # Regione
    regione = None
    if "regione" in df.columns:
        regioni_uniche = sorted([r for r in df["regione"].fillna("").unique().tolist() if r != ""])
        regione = col4.selectbox("🏜 REGIONE", ["Tutte"] + regioni_uniche)

    # Tecnico
    tecnico = None
    if "tecnico_nome" in df.columns:
        tecnici_unici = sorted([t for t in df["tecnico_nome"].fillna("").unique().tolist() if t != ""])
        tecnico = col5.selectbox("👷‍♂️ TECNICO", ["Tutti"] + tecnici_unici)

    # Guasto
    guasto = None
    if "guasto" in df.columns:
        guasti_unici = sorted([g for g in df["guasto"].fillna("").unique().tolist() if g != ""])
        guasto = col6.selectbox("⚠️ GUASTO", ["Tutti"] + guasti_unici)

   # Filtri periodo
    col7, col8 = st.columns(2)
    data_da = col7.date_input("Da data ⏱", data_min)
    data_a = col8.date_input("A data ⏱", data_max)
    # ------------------------------
    # APPLICA FILTRI
    # ------------------------------
    df_filtrato = df.copy()

    if matricola:
        df_filtrato = df_filtrato[df_filtrato["matricola"].astype(str).str.contains(matricola, case=False, na=False)]

    if azienda and azienda != "Tutte":
        df_filtrato = df_filtrato[df_filtrato["azienda"] == azienda]

    if provincia and provincia != "Tutte":
        df_filtrato = df_filtrato[df_filtrato["provincia_cliente"] == provincia]

    if regione and regione != "Tutte":
        df_filtrato = df_filtrato[df_filtrato["regione"] == regione]

    if tecnico and tecnico != "Tutti":
        df_filtrato = df_filtrato[df_filtrato["tecnico_nome"] == tecnico]

    if guasto and guasto != "Tutti":
        df_filtrato = df_filtrato[df_filtrato["guasto"] == guasto]

    # Filtro per date
    if "data_intervento" in df_filtrato.columns:
        df_filtrato["data_intervento"] = pd.to_datetime(df_filtrato["data_intervento"], errors="coerce")
        df_filtrato = df_filtrato[
            (df_filtrato["data_intervento"] >= pd.to_datetime(data_da)) &
            (df_filtrato["data_intervento"] <= pd.to_datetime(data_a))
        ]

    # ------------------------------
    # RIEPILOGO
    # ------------------------------
    st.subheader("📌 Riepilogo selezione")

    nr_interventi = len(df_filtrato)
    fatturato = df_filtrato["fattura"].sum() if "fattura" in df_filtrato.columns else 0

    col_r1, col_r2 = st.columns(2)
    col_r1.metric("📑 Numero Interventi", nr_interventi)
    col_r2.metric("💰 Totale Costo fatturato", f"{fatturato:,.2f}€".replace(",", "X").replace(".", ",").replace("X", "."))
    ## col_r2.metric("💰 Totale Costo fatturato ", f"{fatturato:,.2f} €")

    # ------------------------------
    # TABELLA RISULTATI
    # ------------------------------
      # --- Tabella ---
    st.subheader("📑 Risultati")
    st.dataframe(
        df_filtrato.style.format({
            "fattura": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "data_intervento": lambda x: x.strftime("%d/%m/%Y") if pd.notnull(x) else ""
        }),
        use_container_width=True
    )
    
    ### st.subheader("📋 Risultati")
    ## st.dataframe(df_filtrato, use_container_width=True)

    # ------------------------------
    # DOWNLOAD EXCEL
    # ------------------------------
    st.subheader("⬇️ Esporta")
    buffer = BytesIO()
    
    if not df_filtrato.empty:
        csv = df_filtrato.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️📥 Scarica dati CSV",
            data=csv,
            file_name="ticket_filtrati.csv",
            mime="text/csv",
        )
     ## Creiamo una copia con valori formattati in europeo
    df_export = df_filtrato.copy()
    df_export["fattura"] = df_export["fattura"].apply(lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    df_export["data_intervento"] = df_export["data_intervento"].dt.strftime("%d/%m/%Y")
    
    # Creiamo una copia con valori formattati in europeo
    
    df_export = df_filtrato.copy()
    df_export["fattura"] = df_export["fattura"].apply(lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    df_export["data_intervento"] = df_export["data_intervento"].dt.strftime("%d/%m/%Y")

    df_export.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)

    st.download_button(
        label="⬇️📥 Scarica dati Excel",
        data=buffer,
        file_name="analisi_ticket.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ==========================
# FUNZIONI DATABASE
# ==========================

def get_clienti():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT * FROM clienti", conn)
    conn.close()
    return df

def get_colonne_clienti():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(clienti)")
    columns = [row[1] for row in cur.fetchall()]
    conn.close()
    return [c for c in columns if c != "id"]

def aggiorna_cliente(row):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    # Recuperiamo i tipi delle colonne
    cur.execute("PRAGMA table_info(clienti)")
    col_info = {c[1]: c[2].upper() for c in cur.fetchall()}  # {colonna: tipo}

    valori = []
    for col in ["id", "matricola", "matint", "modello", "proprieta","codice", "azienda", "ubicazione", "indirizzo", "citta", "provincia", "contatto", "vestizione", "note", "ddt"]:
        val = row[col]
        tipo = col_info.get(col, "TEXT")

        # Conversione base dei tipi
        if pd.isna(val):
            val = None
        elif "INT" in tipo:
            val = int(val)
        elif "REAL" in tipo or "FLOA" in tipo or "DOUB" in tipo:
            val = float(val)
        else:
            val = str(val)

        valori.append(val)

    cur.execute("""
        INSERT INTO clienti (id, matricola, matint, modello, proprieta, codice, azienda, ubicazione, indirizzo, citta, provincia, contatto, vestizione, note, ddt)
        VALUES (?, ?, ?, ?, ?,?, ?, ?, ?, ?,?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            matricola=excluded.matricola,
            matint=excluded.matint,
            modello=excluded.modello,
            proprieta=excluded.proprieta,
            codice=excluded.codice,
            azienda=excluded.azienda,
            ubicazione=excluded.ubicazione,
            indirizzo=excluded.indirizzo,
            citta=excluded.citta,
            provincia=excluded.provincia,
            contatto=excluded.contatto,
            vestizione=excluded.vestizione,
            note=excluded.note,
            ddt=excluded.ddt
    """, valori)

    conn.commit()
    conn.close()

def aggiungi_cliente(nuovi_valori):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    colonne = ", ".join(nuovi_valori.keys())
    placeholders = ", ".join(["?"] * len(nuovi_valori))
    valori = list(nuovi_valori.values())
    cur.execute(f"INSERT INTO clienti ({colonne}) VALUES ({placeholders})", valori)
    conn.commit()
    conn.close()

def elimina_cliente_by_id(cliente_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM clienti WHERE id=?", (cliente_id,))
    conn.commit()
    conn.close()
    
def reset_clienti():
    """Elimina tutti i clienti e resetta l'ID autoincrement"""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM clienti")  # Elimina tutti i dati
    cur.execute("DELETE FROM sqlite_sequence WHERE name='clienti'")  # Reset contatore ID
    conn.commit()
    conn.close()
    
def reset_ticket():
    """Elimina tutti i ticket e resetta l'ID autoincrement"""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM ticket")  # Elimina tutti i dati
    cur.execute("DELETE FROM sqlite_sequence WHERE name='ticket'")  # Reset contatore ID
    conn.commit()
    conn.close()

# ==========================
# INTERFACCIA STREAMLIT
# ==========================
def pagina_clienti():
    st.header("🏠 GESTIONE PV")

# -------------------------
# ANALISI COPERTURA TECNICI
# -------------------------
    st.subheader("⚠️👷 Province clienti senza copertura tecnica")

    conn = sqlite3.connect(DB_PATH)
    df_clienti = pd.read_sql_query("SELECT * FROM clienti", conn)
    df_tecnici = pd.read_sql_query("SELECT * FROM tecnici", conn)
    df_ticket = pd.read_sql_query("SELECT * FROM ticket", conn)
    conn.close()

    if not df_clienti.empty:
        province_coperte = set(df_tecnici["provincia"].dropna().unique())
        province_clienti = set(df_clienti["provincia"].dropna().unique())
        province_scoperte = province_clienti - province_coperte

        if province_scoperte:
            rows_summary = []
            dettagli = []

            for prov in province_scoperte:
                clienti_prov = df_clienti[df_clienti["provincia"] == prov]
                num_clienti = len(clienti_prov)
                num_citta = clienti_prov["citta"].nunique()

            # pallini criticità
                if num_clienti == 1:
                    copertura = "🟡"
                elif 2 <= num_clienti <= 3:
                    copertura = "🟠"
                else:
                    copertura = "🔴"

                rows_summary.append({
                    "Provincia": prov,
                    "N. Clienti": num_clienti,
                    "N. Località": num_citta,
                    "Copertura": copertura
                })

                # dettaglio clienti provincia
                for _, cli in clienti_prov.iterrows():
                    has_ticket = df_ticket[df_ticket["matricola"] == cli.get("matricola", "")]
                    warning = "⚠️" if not has_ticket.empty else ""

                    dettagli.append({
                        "Provincia": prov,
                        "Matricola": cli.get("matricola", ""),
                        "Proprietà": cli.get("proprieta", ""),
                        "Modello": cli.get("modello", ""),
                        "Azienda": cli.get("azienda", ""),
                        "Indirizzo": cli.get("indirizzo", ""),
                        "Città": cli.get("citta", ""),
                        "Warning Ticket": warning
                    })

            # Tabella riepilogo per provincia
            df_summary = pd.DataFrame(rows_summary)
            st.dataframe(df_summary, use_container_width=True, height=240)

            # Tabella dettaglio TUTTE le province (espandibile)
            with st.expander("🔍 Mostra dettaglio clienti di tutte le province scoperte"):
                df_dettagli = pd.DataFrame(dettagli)
                st.dataframe(df_dettagli, use_container_width=True)

                # Download Excel unico
                file_path = "dettaglio_clienti_scoperti.xlsx"
                df_dettagli.to_excel(file_path, index=False)

                with open(file_path, "rb") as f:
                    st.download_button(
                        label="📥 Scarica Excel con dettagli",
                        data=f,
                        file_name=file_path,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )


    # -------------------------
    # TABELLA CLIENTI
    # -------------------------
    df = get_clienti()

    if df.empty:
        st.warning("⚠️ Nessun cliente trovato nel database")
    else:
        st.subheader("✏️ Modifica Clienti Esistenti")

        edited_df = st.data_editor(
            df,
            num_rows="fixed",
            use_container_width=True,
            hide_index=True
        )

        # 🔎 Confronto forzando i valori a stringa (per non perdere modifiche)
        changed_rows = edited_df.astype(str).compare(df.astype(str))

        if not changed_rows.empty:
            st.info("🔄 Sono state rilevate modifiche, clicca Salva per aggiornare il database.")
            if st.button("💾 Salva modifiche"):
                for i in edited_df.index:
                    if not edited_df.loc[i].equals(df.loc[i]):
                        try:
                            aggiorna_cliente(edited_df.loc[i])
                        except Exception as e:
                            st.error(f"❌ Errore nell'aggiornamento cliente ID {edited_df.loc[i]['id']}: {e}")
                st.success("✅ Database aggiornato con successo")
                st.rerun()

        # -------------------------
        # ELIMINA CLIENTE
        # -------------------------
        st.subheader("❌ Elimina Cliente")

        search_matricola = st.text_input("🔍 Cerca per matricola")
        risultati = df[df["matricola"].str.contains(search_matricola, case=False, na=False)] if search_matricola else pd.DataFrame()

        if not risultati.empty:
            st.write("📋 Risultati trovati:")
            st.dataframe(risultati, use_container_width=True)

            risultati["label"] = risultati.apply(
                lambda x: f"{x['matricola']} | {x['azienda']} | {x['indirizzo']} | {x['codice']} (ID:{x['id']})",
                axis=1
            )

            cliente_sel = st.selectbox("Seleziona cliente da eliminare", risultati["label"])

            if st.button("⚠️ Conferma eliminazione"):
                cliente_id = int(cliente_sel.split("ID:")[-1].replace(")", ""))
                try:
                    elimina_cliente_by_id(cliente_id)
                    st.success(f"✅ Cliente eliminato: {cliente_sel}")
                    st.rerun()
                except sqlite3.IntegrityError as e:
                    st.error(f"❌ Impossibile eliminare il cliente: {e}. "
                             "Verifica se esistono record collegati (es. ordini) "
                             "o aggiungi ON DELETE CASCADE al vincolo.")

    # -------------------------
    # RESET COMPLETO
    # -------------------------
    st.subheader("🧹 Reset Database Clienti")

    if st.button("⚠️ Elimina tutti i clienti e resetta ID"):
        reset_clienti()
        st.success("✅ Tutti i clienti eliminati e ID resettato!")
        st.rerun()

    # -------------------------
    # AGGIUNGI NUOVO CLIENTE
    # -------------------------
    st.subheader("➕ Aggiungi Nuovo Cliente")

    colonne_clienti = get_colonne_clienti()
    with st.form("aggiungi_cliente"):
        nuovi_valori = {}
        for col in colonne_clienti:
            nuovi_valori[col] = st.text_input(col)
        submitted = st.form_submit_button("Aggiungi")

        if submitted:
            if any(nuovi_valori.values()):
                try:
                    aggiungi_cliente(nuovi_valori)
                    st.success("✅ Nuovo cliente aggiunto con successo")
                    st.rerun()
                except sqlite3.IntegrityError as e:
                    st.error(f"❌ Errore nell'inserimento: {e}")
            else:
                st.error("⚠️ Inserisci almeno un valore")

# ─── FUNZIONI DATABASE ───
TABLE = "tecnici"

def get_tecnici():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql(f"SELECT * FROM {TABLE}", conn)
    conn.close()
    return df

def get_column_names():
    conn = sqlite3.connect(DB_FILE)
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({TABLE})")]
    conn.close()
    return [c for c in cols if c != "id"]

def update_tecnico(row):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    # costruisco update dinamico
    cols = [c for c in row.index if c != "id"]
    set_clause = ", ".join([f"{c}=?" for c in cols])
    values = [row[c] for c in cols] + [int(row["id"])]
    cur.execute(f"UPDATE {TABLE} SET {set_clause} WHERE id=?", values)
    conn.commit()
    conn.close()

def add_tecnico(vals: dict):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cols = ", ".join(vals.keys())
    ph = ", ".join(["?"] * len(vals))
    cur.execute(f"INSERT INTO {TABLE} ({cols}) VALUES ({ph})", list(vals.values()))
    conn.commit()
    conn.close()

def delete_tecnico_by_id(tid: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(f"DELETE FROM {TABLE} WHERE id=?", (tid,))
    conn.commit()
    conn.close()

def reset_tecnici():
    """Elimina tutti i tecnici e resetta l'ID autoincrement"""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM tecnici")  # Elimina tutti i dati
    cur.execute("DELETE FROM sqlite_sequence WHERE name='tecnici'")  # Reset contatore ID
    conn.commit()
    conn.close()

# ─── STREAMLIT UI ───
def pagina_tecnici():
    st.header("👷 GESTIONE TECNICI")

    df = get_tecnici()

    if df.empty:
        st.warning("⚠️ Nessun tecnico trovato nel database")
    else:
        st.subheader("✏️ Modifica Tecnici Esistenti")

        edited = st.data_editor(
            df,
            num_rows="fixed",
            use_container_width=True,
            hide_index=True
        )

        changes = edited.compare(df)
        if not changes.empty:
            st.info("🔄 Modifiche rilevate! Clicca 'Salva modifiche' per aggiornare il database.")
            if st.button("💾 Salva modifiche"):
                for i in edited.index:
                    if not edited.loc[i].equals(df.loc[i]):
                        update_tecnico(edited.loc[i])
                st.success("✅ Tecnici aggiornati")
                st.rerun()

        st.subheader("❌ Elimina Tecnico")
        search = st.text_input("🔍 Cerca per nome")
        results = df[df["nome"].str.contains(search, case=False, na=False)] if search else pd.DataFrame()

        if not results.empty:
            st.write("Risultati:")
            st.dataframe(results, use_container_width=True)

            results["label"] = results.apply(
                lambda x: " | ".join([str(x[c]) for c in get_column_names()]) + f" (ID:{x['id']})",
                axis=1
            )

            sel = st.selectbox("Seleziona tecnico da eliminare", results["label"])
            if st.button("⚠️ Conferma eliminazione"):
                tid = int(sel.split("ID:")[-1].strip(")"))
                delete_tecnico_by_id(tid)
                st.success(f"✅ Tecnico eliminato: {sel}")
                st.rerun()
            
    # RESET COMPLETO
    # -------------------------
    st.subheader("🧹 Reset Database Tecnici")

    if st.button("⚠️ Elimina tutti i Tecnici e resetta ID"):
        reset_tecnici()
        st.success("✅ Tutti i Tencici eliminati e ID resettato!")
        st.rerun()            

    st.subheader("➕ Aggiungi Nuovo Tecnico")


    cols = get_column_names()
    with st.form("add_tecnico"):
        vals = {}
        for c in cols:
            vals[c] = st.text_input(c)
        submitted = st.form_submit_button("Aggiungi")
        if submitted:
            if any(vals.values()):
                add_tecnico(vals)
                st.success("✅ Nuovo tecnico aggiunto")
                st.rerun()
            else:
                st.error("⚠️ Inserisci almeno un campo")

# ===========================================
# PAGINA: IMPORT / EXPORT
# ===========================================
def pagina_import_export():
    st.header("📤 Import / Export")

    st.subheader("⬇️ Export")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📥 Esporta Clienti (Excel)"):
            rows = get_all("clienti")
            df = df_from_rows(rows)
            # Niente: extra_json resta come colonna; chi lo importa può gestirlo
            out = "export_clienti.xlsx"
            df.to_excel(out, index=False)
            with open(out, "rb") as f:
                st.download_button("Scarica export_clienti.xlsx", f, file_name="clienti.xlsx")
            if os.path.exists(out):
                os.remove(out)
    with col2:
        if st.button("📥 Esporta Tecnici (Excel)"):
            rows = get_all("tecnici")
            df = df_from_rows(rows)
            out = "export_tecnici.xlsx"
            df.to_excel(out, index=False)
            with open(out, "rb") as f:
                st.download_button("Scarica export_tecnici.xlsx", f, file_name="tecnici.xlsx")
            if os.path.exists(out):
                os.remove(out)
    with col3:
        if st.button("📥 Esporta Ticket (Excel)"):
            con = connetti_db()
            df = pd.read_sql_query("""
                SELECT
                    t.id,
                    t.cliente_id,
                    t.tecnico_id,
                    t.matricola_manual,
                    t.tecnico_manual,
                    t.descrizione,
                    t.fattura,
                    t.note,
                    t.data_intervento,
                    t.intervento_svolto,
                    t.allegato,
                    t.stato,
                    t.data_creazione,
                    c.matricola,
                    c.azienda,
                    c.indirizzo AS indirizzo_cliente,
                    c.citta AS citta_cliente,
                    c.provincia AS provincia_cliente,
                    t.regione,
                    t.tecnico_nome,
                    t.citta_tecnico,
                    t.provincia_tecnico
                FROM ticket t
                LEFT JOIN clienti c ON c.id = t.cliente_id
                ORDER BY datetime(t.data_creazione) ASC, t.id ASC
            """, con)
            con.close()

            out = "export_ticket.xlsx"
            df.to_excel(out, index=False)
            with open(out, "rb") as f:
                st.download_button("Scarica export_ticket.xlsx", f, file_name="ticket.xlsx")
            if os.path.exists(out):
                os.remove(out)

                
    ### UPLOAD TICKET (adattivo) ###
    # --- Funzione robusta per gestire le date da Excel ---
    def to_safe_date_str(val):
        """
        Converte un valore proveniente da Excel in stringa data 'YYYY-MM-DD',
        oppure ritorna None se non valido/vuoto.
        """
        if pd.isna(val) or val in ("", None):
            return None

        # Se è già datetime
        if isinstance(val, (pd.Timestamp, datetime)):
            return val.strftime("%Y-%m-%d")

        # Se è numerico (Excel serial date)
        if isinstance(val, (int, float)):
            try:
                return pd.to_datetime(val, origin="1899-12-30", unit="D").strftime("%Y-%m-%d")
            except Exception:
                return None

        # Se è stringa
        try:
            d = pd.to_datetime(str(val), dayfirst=False, errors="coerce")
            return d.strftime("%Y-%m-%d") if d is not pd.NaT else None
        except Exception:
            return None


    ### UPLOAD TICKET (adattivo migliorato) ###
    st.subheader("⬆️ Upload Ticket (Excel)")
    up = st.file_uploader("Carica un Excel con ticket da inserire", type=["xlsx"], key="up_ticket_excel")
    if up is not None:
        try:
            df = pd.read_excel(up)
            st.write("Anteprima:", df.head())

            # 🔹 Normalizza intestazioni
            df.columns = normalize_headers(df.columns)

            # 🔹 Mappa colonne Excel → DB
            colmap = {
                "matricola": "matricola",
                "descrizione": "descrizione",
                "fattura": "fattura",
                "note": "note",
                "data_intervento": "data_intervento",
                "intervento_svolto": "intervento_svolto",
                "stato": "stato",
                "allegato": "allegato",
                "tecnico_nome": "tecnico_nome",
                "citta_tecnico": "citta_tecnico",
                "provincia_tecnico": "provincia_tecnico",
            }

            # Aggiungi eventuali colonne che vuoi "freezare" (es. azienda, indirizzo, ecc.)
            freeze_cols = ["azienda", "indirizzo_cliente", "citta_cliente", "provincia_cliente"]

            # Controllo minimo: matricola + descrizione
            if not all(c in df.columns for c in ["matricola", "descrizione"]):
                st.error("❌ L'Excel deve contenere almeno le colonne: matricola, descrizione")
                st.stop()

            if "upload_done" not in st.session_state:
                st.session_state.upload_done = False

            if not st.session_state.upload_done:
                if st.button("✅ Conferma upload ticket"):
                    con = connetti_db()
                    cur = con.cursor()
                    ins, skip, dup = 0, 0, 0

                    for _, r in df.iterrows():
                        matricola = str(r.get("matricola") or "").strip()
                        if not matricola:
                            skip += 1
                            continue

                        # recupero cliente dalla tabella clienti
                        cli = cur.execute("SELECT * FROM clienti WHERE matricola = ?", (matricola,)).fetchone()
                        if not cli:
                            skip += 1
                            continue

                        descrizione = str(r.get("descrizione") or "").strip()
                        if not descrizione:
                            skip += 1
                            continue

                        # Campi opzionali
                        fattura = r.get("fattura") or 0
                        note = str(r.get("note") or "")
                        data_interv = to_safe_date_str(r.get("data_intervento"))
                        intervento = str(r.get("intervento_svolto") or "")
                        stato = str(r.get("stato") or "Aperto")
                        allegato = str(r.get("allegato") or "")
                        tecnico_nome = str(r.get("tecnico_nome") or "")
                        citta_tecnico = str(r.get("citta_tecnico") or "")
                        provincia_tecnico = str(r.get("provincia_tecnico") or "")
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        # 🔎 Controllo duplicati
                        exists = cur.execute("""
                            SELECT 1 FROM ticket 
                            WHERE cliente_id=? AND descrizione=? AND data_intervento=?
                        """, (cli["id"], descrizione, data_interv)).fetchone()
                        if exists:
                            dup += 1
                            continue

                        # Inserimento con freeze dati cliente
                        cur.execute("""
                            INSERT INTO ticket (
                                cliente_id, descrizione, fattura, note, data_intervento, intervento_svolto,
                                stato, allegato, tecnico_nome, citta_tecnico, provincia_tecnico, data_creazione,
                                matricola_manual, azienda, indirizzo_cliente, citta_cliente, provincia_cliente
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            cli["id"], descrizione, fattura, note, data_interv, intervento,
                            stato, allegato, tecnico_nome, citta_tecnico, provincia_tecnico, now_str,
                            cli["matricola"], cli["azienda"], cli["indirizzo"], cli["citta"], cli["provincia"]
                        ))
                        ins += 1

                    con.commit()
                    con.close()

                    st.success(f"✅ Upload completato. Inseriti: {ins}, Saltati: {skip}, Duplicati trovati: {dup}.")
                    st.session_state.upload_done = True
                    st.rerun()

            else:
                st.info("✔️ Upload già eseguito. Ricarica la pagina per ripetere.")
        except Exception as e:
            st.error(f"Errore upload ticket: {e}")
       
            
DB_FILE = "assistenza.db" # 🔹 Usa lo stesso path che usi nel resto del progetto

def pagina_tools():
    st.header("🛠️ Tools - Gestione Guasti (critical)")

    # --- Connessione DB ---
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # per avere i risultati come dict-like
    
    # --- Leggo la tabella direttamente in dataframe ---
    critical_df = pd.read_sql_query("SELECT id, codice_guasto, guasto FROM critical ORDER BY codice_guasto ASC", conn)

    st.subheader("📋 Elenco guasti registrati")
    if not critical_df.empty:
        st.dataframe(critical_df, use_container_width=True)
    else:
        st.info("Nessun guasto presente nel database.")

    # --- Aggiungi nuovo guasto ---
    st.subheader("➕ Aggiungi nuovo guasto")
    with st.form("form_add_guasto", clear_on_submit=True):
        codice_guasto = st.text_input("Codice guasto (es. G01)").strip()
        descrizione_guasto = st.text_input("Descrizione guasto (es. Non si accende)").strip()
        add_submitted = st.form_submit_button("💾 Aggiungi Guasto")

    if add_submitted:
        if codice_guasto and descrizione_guasto:
            conn.execute(
                "INSERT INTO critical (codice_guasto, guasto) VALUES (?, ?)",
                (codice_guasto, descrizione_guasto)
            )
            conn.commit()
            st.success(f"✅ Guasto '{codice_guasto} - {descrizione_guasto}' aggiunto con successo!")
            conn.close()
            st.rerun()
        else:
            st.warning("⚠️ Inserisci sia codice che descrizione.")

    # --- Eliminazione guasto ---
    st.subheader("🗑️ Elimina guasto")
    if not critical_df.empty:
        guasto_map = {f"{row['codice_guasto']} - {row['guasto']}": row['id'] for _, row in critical_df.iterrows()}
        guasto_da_eliminare = st.selectbox("Seleziona guasto da eliminare", list(guasto_map.keys()))
        if st.button("❌ Elimina Guasto"):
            conn.execute("DELETE FROM critical WHERE id = ?", (guasto_map[guasto_da_eliminare],))
            conn.commit()
            st.success(f"Guasto '{guasto_da_eliminare}' eliminato con successo!")
            conn.close()
            st.rerun()
    conn.close()
    


LOGO_PATH = "logo.png"   # metti il tuo logo qui

# --- Generatore PDF ---
def genera_pdf_ticket_fattura(ticket_data, campi):
    def safe_str(val, default="-"):
        """Converte None in stringa sicura"""
        if val is None:
            return default
        return str(val)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    styleN = styles["Normal"]
    styleH = styles["Heading1"]

    elementi = []

    ### Logo + intestazione
    ###if os.path.exists(LOGO_PATH):
       ### logo = Image(LOGO_PATH, width=5.5*cm, height=3*cm)
        ###elementi.append(logo)

    ##elementi.append(Paragraph("<b>ENTERPRISE Srl</b>", styleN))
    ##elementi.append(Paragraph("Via 1° Maggio, 20873 - Cavenago di Brianza (MB)", styleN))
    ##elementi.append(Paragraph("Tel: 02-1234567 | Email: info@enterprisepromo.it", styleN))
    ##elementi.append(Spacer(5, 20))##

    # Titolo documento
    titolo = Paragraph(f"<para align=center><b>TICKET   {safe_str(ticket_data.get('id'))}</b></para>", styleH)
    elementi.append(titolo)
    titolo = Paragraph(f"<para align=center><b>DateTime {safe_str(ticket_data.get('data_creazione'))}</b></para>", styleH)
    elementi.append(titolo)
    elementi.append(Spacer( 10, 10))

    # Dati Cliente & Tecnico
    dati = [
        ["Matricola", safe_str(ticket_data.get("matricola"))],
        ["Modello", safe_str(ticket_data.get("modello"))],
        ["Cliente", safe_str(ticket_data.get("azienda"))],
        ["Indirizzo", safe_str(ticket_data.get("indirizzo_cliente"))],
        ["Città", f"{safe_str(ticket_data.get('citta_cliente'))} ({safe_str(ticket_data.get('provincia_cliente'))})"],
        ["Referente P.V.", safe_str(ticket_data.get("contatto"))],
      ##["Tecnico", safe_str(ticket_data.get("tecnico_nome"))],##
     ## ["Data Intervento", safe_str(ticket_data.get("data_intervento"))],##
        ["Stato TICKET", safe_str(ticket_data.get("stato"))]
    ]

    tabella_info = Table(dati, hAlign="LEFT", colWidths=[4*cm, 10*cm])
    tabella_info.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('BOX', (0,0), (-1,-1), 1, colors.grey),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ]))

    elementi.append(tabella_info)
    elementi.append(Spacer(1, 20))
    
    # Sezioni testuali opzionali
    if "Tecnico Frigorista" in campi:
        elementi.append(Paragraph("<b>Tecnico Frigorista</b>", styleN))
        elementi.append(Paragraph(safe_str(ticket_data.get("tecnico_nome")), styleN))
        elementi.append(Spacer(1, 12))
    
    # Sezioni testuali opzionali
    if "Criticità segnalata" in campi:
        elementi.append(Paragraph("<b>Criticità segnalata:</b>", styleN))
        elementi.append(Paragraph(safe_str(ticket_data.get("descrizione")), styleN))
        elementi.append(Spacer(1, 12))
    
    if "Data previsto intervento" in campi:
        elementi.append(Paragraph("<b>Data previsto intervento:</b>", styleN))
        elementi.append(Paragraph(safe_str(ticket_data.get("data_intervento")), styleN))
        elementi.append(Spacer(1, 12))
    
    if "Data intervento" in campi:
        elementi.append(Paragraph("<b>Data Intervento:</b>", styleN))
        elementi.append(Paragraph(safe_str(ticket_data.get("data_intervento")), styleN))
        elementi.append(Spacer(1, 12))

    if "Tipo Guasto" in campi:
        elementi.append(Paragraph("<b>Tipo Guasto:</b>", styleN))
        elementi.append(Paragraph(safe_str(ticket_data.get("guasto")), styleN))
        elementi.append(Spacer(1, 12))

    if "Intervento svolto" in campi:
        elementi.append(Paragraph("<b>Intervento svolto:</b>", styleN))
        elementi.append(Paragraph(safe_str(ticket_data.get("intervento_svolto")), styleN))
        elementi.append(Spacer(1, 12))

    if "Note" in campi:
        elementi.append(Paragraph("<b>Note:</b>", styleN))
        elementi.append(Paragraph(safe_str(ticket_data.get("note")), styleN))
        elementi.append(Spacer(1, 12))

    # Totale Fattura
    if "Fattura" in campi:
        fattura = ticket_data.get("fattura") or 0.0
        try:
            fattura = float(fattura)
        except Exception:
            fattura = 0.0
        totali = [["Totale Fattura (€)", f"{fattura:.2f}"]]
        tabella_tot = Table(totali, colWidths=[10*cm, 4*cm])
        tabella_tot.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 12),
            ('BOX', (0,0), (-1,-1), 1, colors.black),
        ]))
        elementi.append(Spacer(1, 20))
        elementi.append(tabella_tot)

    # Footer
    elementi.append(Spacer(1, 30))
    elementi.append(Paragraph("<i>Documento generato automaticamente dal sistema Ticket</i>", styleN))

    # Build PDF
    doc.build(elementi)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


# --- Pagina Streamlit ---
def pagina_stampa_ticket():
    st.header("🖨️ Stampa Ticket")

    conn = sqlite3.connect("assistenza.db")
    cursor = conn.cursor()

    # funzione utility locale
    def safe_str(val, default="-"):
        if val is None:
            return default
        return str(val)
    ## te.nome as tecnico_nome ##
    # ricerca ticket (stile modifica ticket)
    search_ticket = st.text_input("🔍 Cerca ticket per cliente, tecnico o descrizione...")
    query = """
        SELECT t.*, c.matricola, c.azienda, c.indirizzo as indirizzo_cliente, c.citta as citta_cliente,
               c.provincia as provincia_cliente, t.tecnico_nome 
        FROM ticket t
        LEFT JOIN clienti c ON t.cliente_id = c.id
        LEFT JOIN tecnici te ON t.tecnico_id = te.id
        WHERE t.matricola LIKE ? OR t.azienda LIKE ? OR t.tecnico_nome LIKE ? OR t.descrizione LIKE ? OR t.guasto LIKE ?
        ORDER BY t.data_creazione DESC
        LIMIT 50
    """
    cursor.execute(query, (f"%{search_ticket}%", f"%{search_ticket}%", f"%{search_ticket}%", f"%{search_ticket}%", f"%{search_ticket}%"))
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]

    # qui convertiamo subito i valori
    tickets = []
    for row in rows:
        rec = {}
        for col, val in zip(cols, row):
            # fattura può restare numerica per calcoli, non forziamo stringa
            if col == "fattura":
                rec[col] = val if val is not None else 0.0
            else:
                rec[col] = safe_str(val)
        tickets.append(rec) 

    if tickets:
        ticket_map = {
            f"#{t['id']} | {t['matricola']} |{t['azienda']} | {t['indirizzo_cliente']} |{t['tecnico_nome']} | {t['guasto']}": t
            for t in tickets
        }
        ticket_sel = st.selectbox("📌 Seleziona Ticket", list(ticket_map.keys()))
    else:
        st.info("Nessun ticket trovato.")
        return

    if ticket_sel:
        ticket_data = ticket_map[ticket_sel]

        # selezione campi da stampare
        campi_possibili = ["Tecnico Frigorista" ,"Criticità segnalata","Data previsto intervento", "Data Intervento", "Tipo Guasto", "Intervento svolto", "Note", "Fattura"]
        selected_fields = st.multiselect(
            "Seleziona campi da includere nel PDF:",
            campi_possibili,
            default=campi_possibili
        )

        if st.button("📄 Genera PDF"):
            pdf_bytes = genera_pdf_ticket_fattura(ticket_data, selected_fields)

            # preview
            st.subheader("Anteprima PDF")
            pdf_viewer(pdf_bytes, width=700)

            # download
            st.download_button(
                label="⬇️ Scarica PDF",
                data=pdf_bytes,
                file_name=f"ticket_{ticket_data['id']}.pdf",
                mime="application/pdf"
            )






# ROUTER PAGINE 
# ===========================================
if menu == "🎫 Ticket di Assistenza":
    pagina_ticket()
elif menu == "📊 Analisi Ticket":
    pagina_analisi_ticket()
elif menu == "⬆️👷 Up_load Tecnici":
    pagina_upload_tecnici()
elif menu == "⬆️🏠 Up_load Clienti":
    pagina_upload_clienti()
elif menu == "🖨️ Stampa Ticket":
    pagina_stampa_ticket()
elif menu == "🛠️ Tools":
    pagina_tools()
elif menu == "🏠 Gestione PV":
    pagina_clienti()
elif menu == "👷 Gestione Tecnici":
    pagina_tecnici()
else:
    pagina_import_export()
