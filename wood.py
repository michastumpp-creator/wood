import streamlit as st
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
from PIL import Image
from datetime import datetime
import fitz  # PyMuPDF
import io
import re
import os
import shutil
from google import genai
from google.genai import types

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager Pro", page_icon="🌲", layout="wide")
DB_FILE = "forst_daten.json"
UPLOAD_DIR = "belege"
BACKUP_DIR = "backups"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# --- CSS: MOBILE OPTIMIERUNG ---
st.markdown("""
    <style>
        /* NUR FÜR HANDYS (Bildschirm kleiner 600px) */
        @media only screen and (max-width: 600px) {
            
            /* Zoom: Alles verkleinern (85%), Container verbreitern */
            div[data-testid="stDataEditor"] {
                transform: scale(0.85);
                transform-origin: top left;
                width: 118% !important;
                margin-bottom: -30px;
                font-size: 10px !important;
            }

            /* Schriftarten winzig */
            div[data-testid="stDataEditor"] td, 
            div[data-testid="stDataEditor"] th,
            div[data-testid="stDataEditor"] input {
                font-size: 10px !important;
                padding: 0px 0px !important;
                line-height: 1.5 !important;
            }

            /* Header extrem schmal zwingen */
            div[data-testid="stDataEditor"] th {
                min-width: 10px !important;
                width: auto !important;
            }
            
            /* Seitenränder weg */
            .block-container {
                padding-left: 0.1rem !important;
                padding-right: 0.1rem !important;
            }
        }
    </style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload_count' not in st.session_state:
    st.session_state.last_upload_count = 0
if 'messages' not in st.session_state:
    st.session_state.messages = []

# --- HELFER FUNKTIONEN ---

def load_prompt():
    # Standard Prompt für die KI
    return """Analysiere diese Holzliste.
    1. META-DATEN: Suche nach Gesamtmenge (dokument_summe), Anzahl Stämme (dokument_anzahl_staemme), Revier, Ort, Los-Nummer und Zertifikat (FSC/PEFC).
    2. STÄMME (Tabelle): Extrahiere jeden Stamm einzeln.
       - wnr: Stamm-Nummer
       - art: Holzart (Buche, Eiche, Esche etc.)
       - l: Länge
       - d: Durchmesser
       - kl: Stärkeklasse (z.B. 3b, 4, 5)
       - g: Güteklasse (A, B, C, TF, IL etc.)
       - fm: Festmeter
       - klammer: true (wenn 'K' oder Klammerwert)
    3. POLTER (Tabelle):
       - nr: Polter-Nummer
       - fm: Menge in Fm
       - lat: GPS Breitengrad (Latitude)
       - lon: GPS Längengrad (Longitude)
    Gib NUR JSON zurück."""

def to_float(val):
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    if isinstance(val, str):
        try: return float(val.replace(',', '.'))
        except: return 0.0
    return 0.0

def parse_gps_for_map(val):
    val_str = str(val).strip()
    try:
        f = float(val_str.replace(',', '.'))
        # Grober Check für Europa
        if 40 < f < 60 or 5 < f < 20: return f
    except: pass
    # Versuch DMS Parsing
    matches = re.findall(r'(\d+)[^\d]+(\d+)[^\d]+(\d+[,.]\d+)', val_str)
    if matches:
        try:
            d = float(matches[0][0])
            m = float(matches[0][1])
            s = float(matches[0][2].replace(',', '.'))
            return d + (m / 60.0) + (s / 3600.0)
        except: pass
    return 0.0

def get_google_maps_route_url(group_df):
    coords = []
    for _, r in group_df.iterrows():
        lat = parse_gps_for_map(r.get('Lat', ''))
        lon = parse_gps_for_map(r.get('Lon', ''))
        if lat > 0 and lon > 0:
            coords.append(f"{lat},{lon}")
    if not coords: return None
    dest = coords[-1]
    base_url = "https://www.google.com/maps/dir/?api=1"
    if len(coords) > 1:
        waypoints = "|".join(coords[:-1])
        return f"{base_url}&destination={dest}&waypoints={waypoints}"
    else:
        return f"{base_url}&destination={dest}"

def create_highlighted_pdf_images(uploaded_file, text_summe, text_anzahl, polter_liste):
    uploaded_file.seek(0)
    try: doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    except: return []
    images = []
    search_terms = []
    if text_summe > 0:
        val_str = str(text_summe)
        search_terms.append({"val": val_str, "color": (1, 1, 0)}) 
        search_terms.append({"val": val_str.replace('.', ','), "color": (1, 1, 0)}) 
    if text_anzahl > 0:
        search_terms.append({"val": str(int(text_anzahl)), "color": (0, 1, 1)}) 
    for p in polter_liste:
        raw_lat, raw_lon = p.get('lat', ''), p.get('lon', '')
        if raw_lat: search_terms.append({"val": str(raw_lat), "color": (0, 1, 0)})
        if raw_lon: search_terms.append({"val": str(raw_lon), "color": (0, 1, 0)})
    for page_num, page in enumerate(doc):
        if page_num > 1: break # Nur erste 2 Seiten checken zur Performance
        for item in search_terms:
            quads = page.search_for(item["val"])
            if quads:
                for quad in quads:
                    annot = page.add_highlight_annot(quad)
                    annot.set_colors(stroke=item["color"])
                    annot.update()
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- FINANZ & LOGIK HELFER ---

def calculate_row_value(row, price_df):
    """Berechnet den Euro-Wert für eine Zeile basierend auf der Preisliste."""
    if price_df.empty or row['Volumen_Fm'] <= 0:
        return 0.0
    
    art = str(row.get('Holzart', '')).lower()
    guete = str(row.get('Gue_Kl', '')).lower()
    
    # Fuzzy Matching Logik
    # Wir schauen, ob der String in der Preisliste im String des Stammes vorkommt (z.B. "Bu" in "Rotbuche")
    for _, p_row in price_df.iterrows():
        p_art = str(p_row['Holzart']).lower()
        p_guete = str(p_row['Güte']).lower()
        
        # Match Bedingungen
        art_match = (p_art in art) or (art in p_art)
        # Bei Güte muss es genauer sein, "B" darf nicht "AB" matchen, außer wir wollen das
        # Hier einfache String-Gleichheit für Güte bevorzugt, oder 'in'
        guete_match = p_guete == guete
        
        if art_match and guete_match:
            return row['Volumen_Fm'] * float(p_row['Preis'])
            
    return 0.0

def get_species_group(holzart):
    h = str(holzart).lower()
    if "bu" in h: return "Bu"
    if "es" in h: return "Es"
    if "ei" in h: return "Ei"
    if "fi" in h: return "Fi"
    return "So" # Sonstiges

# --- DATENBANK ---

def create_auto_backup():
    if os.path.exists(DB_FILE):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"auto_backup_{timestamp}.json")
            shutil.copy2(DB_FILE, backup_path)
            # Alte Backups löschen (> 50)
            backups = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.endswith(".json")])
            while len(backups) > 50:
                os.remove(backups[0]); backups.pop(0)
        except Exception as e: print(f"Backup Fehler: {e}")

def load_db():
    # Standard Preisliste, falls DB leer oder neu
    default_prices = [
        {"Holzart": "Bu", "Güte": "B", "Preis": 120.0},
        {"Holzart": "Bu", "Güte": "C", "Preis": 85.0},
        {"Holzart": "Es", "Güte": "A", "Preis": 210.0},
        {"Holzart": "Es", "Güte": "B", "Preis": 140.0},
        {"Holzart": "Es", "Güte": "C", "Preis": 90.0},
        {"Holzart": "Ei", "Güte": "B", "Preis": 350.0},
        {"Holzart": "Ei", "Güte": "C", "Preis": 180.0},
        {"Holzart": "Fi", "Güte": "B", "Preis": 95.0},
    ]
    
    if not os.path.exists(DB_FILE): 
        return {"polter": [], "staemme": [], "preise": default_prices}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f: 
            data = json.load(f)
            if "preise" not in data: data["preise"] = default_prices
            return data
    except Exception: 
        return {"polter": [], "staemme": [], "preise": default_prices}

def save_db(db_data):
    create_auto_backup()
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        st.error(f"Fehler: {e}")
        return False

# --- LOGIK: SPEICHERN & UPDATES ---

def save_to_json(data, source_files=None):
    db = load_db()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    saved_file_paths = []
    if source_files:
        for idx, file_obj in enumerate(source_files):
            try:
                ext = file_obj.name.split('.')[-1]
                filename = f"{file_ts}_{idx}.{ext}"
                filepath = os.path.join(UPLOAD_DIR, filename)
                file_obj.seek(0)
                with open(filepath, "wb") as f: f.write(file_obj.getbuffer())
                saved_file_paths.append(filepath)
            except Exception as e: st.error(f"Datei-Fehler: {e}")

    meta = data.get('meta', {})
    los, revier = str(meta.get('los', 'Unbekannt')), str(meta.get('revier', 'Unbekannt'))
    ort, zert = str(meta.get('revier_ort', '')), str(meta.get('zertifikat', ''))
    datum_aufnahme = str(meta.get('datum', datetime.now().strftime("%Y-%m-%d")))
    soll_menge = str(meta.get('dokument_summe', 0)).replace('.', ',')
    def fmt(val): return str(val).replace(',', '.') if val is not None else ""

    for p in data.get('polter', []):
        lat_text, lon_text = str(p.get('lat', '')), str(p.get('lon', ''))
        try:
            l_lat, l_lon = parse_gps_for_map(lat_text), parse_gps_for_map(lon_text)
            link = f"http://maps.google.com/?q={l_lat},{l_lon}"
        except: link = ""
        db['polter'].append({
            "Datum_Upload": timestamp, "Datum_Aufnahme": datum_aufnahme, "Los_Nr": los, "Revier": revier,
            "Polter_Nr": p.get('nr'), "Menge_Fm": fmt(p.get('fm')), "Lat": lat_text, "Lon": lon_text,
            "Maps_Link": link, "Ort": ort, "Zertifikat": zert, "Soll_Menge_Dokument": soll_menge,
            "Status": "Bestand", "Geliefert": False, "Notiz": "",
            "Belege": saved_file_paths
        })

    for s in data.get('staemme', []):
        wnr = s.get('wnr', '')
        if s.get('klammer'): wnr = f"{wnr} (K)"
        
        gue_kl = s.get('g', s.get('klasse', ''))
        dm_kl = s.get('kl', '')

        db['staemme'].append({
            "Datum_Upload": timestamp, "Los_Nr": los, "Revier": revier, "WNr": wnr,
            "Holzart": s.get('art', ''), "Laenge": fmt(s.get('l')), "Durchmesser": fmt(s.get('d')),
            "Gue_Kl": gue_kl,
            "Dm_Kl": dm_kl, 
            "Volumen_Fm": fmt(s.get('fm')),
            "Status": "Bestand", "Geliefert": False, "Info": ""
        })
    return save_db(db)

def delete_entry_by_timestamp(timestamp):
    db = load_db()
    db['polter'] = [p for p in db['polter'] if p.get('Datum_Upload') != timestamp]
    db['staemme'] = [s for s in db['staemme'] if s.get('Datum_Upload') != timestamp]
    return save_db(db)

def move_to_transport(timestamp):
    db = load_db()
    for p in db['polter']:
        if p.get('Datum_Upload') == timestamp: p['Status'] = 'Transport'
    for s in db['staemme']:
        if s.get('Datum_Upload') == timestamp: s['Status'] = 'Transport'
    return save_db(db)

def apply_batch_updates(timestamp, new_note, polter_df, staemme_df):
    db = load_db()
    for p in db['polter']:
        if p.get('Datum_Upload') == timestamp: p['Notiz'] = new_note
    if not polter_df.empty:
        for index, row in polter_df.iterrows():
            for p in db['polter']:
                if p.get('Datum_Upload') == timestamp and str(p.get('Polter_Nr')) == str(row['Polter_Nr']):
                    if 'Geliefert' in row: p['Geliefert'] = row['Geliefert']
                    break
    if not staemme_df.empty:
        for index, row in staemme_df.iterrows():
            for s in db['staemme']:
                if s.get('Datum_Upload') == timestamp and str(s.get('WNr')) == str(row['WNr']):
                    if 'Geliefert' in row: s['Geliefert'] = row['Geliefert']
                    if 'Info' in row: s['Info'] = str(row['Info'])
                    break
    return save_db(db)

def load_data_frames():
    db = load_db()
    df_p = pd.DataFrame(db['polter'])
    if not df_p.empty:
        if 'Menge_Fm' in df_p.columns: df_p['Menge_Fm'] = df_p['Menge_Fm'].apply(to_float)
        for col, val in [('Status', 'Bestand'), ('Geliefert', False), ('Notiz', ''), ('Zertifikat', '')]:
            if col not in df_p.columns: df_p[col] = val
        if 'Belege' not in df_p.columns: df_p['Belege'] = [[] for _ in range(len(df_p))]

    df_s = pd.DataFrame(db['staemme'])
    if not df_s.empty:
        if 'Volumen_Fm' in df_s.columns: df_s['Volumen_Fm'] = df_s['Volumen_Fm'].apply(to_float)
        for col, val in [('Status', 'Bestand'), ('Geliefert', False), ('Info', ''), ('Holzart', ''), ('Gue_Kl', ''), ('Dm_Kl', '')]:
            if col not in df_s.columns: df_s[col] = val
            
    df_prices = pd.DataFrame(db.get('preise', []))
    return df_p, df_s, df_prices

def convert_df_to_excel(df_p, df_s):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if not df_p.empty:
            df_p_ex = df_p.copy()
            df_p_ex['Belege'] = df_p_ex['Belege'].astype(str)
            df_p_ex.to_excel(writer, index=False, sheet_name='Polter')
        if not df_s.empty: df_s.to_excel(writer, index=False, sheet_name='Einzelstaemme')
    return output.getvalue()

def get_list_title(upload_time, group_polter, group_stems, revier, los, ort, zert, datum_auf):
    status = group_polter['Status'].iloc[0] if not group_polter.empty else "Bestand"
    done_p = len(group_polter[group_polter['Geliefert'] == True])
    total_p = len(group_polter)
    
    vol = group_polter['Menge_Fm'].sum()
    stamm_anzahl = 0
    if not group_stems.empty:
        non_k = group_stems[~group_stems['WNr'].astype(str).str.contains(r'\(K\)', na=False)]
        stamm_anzahl = len(non_k)

    status_text = ""
    is_gray = False
    
    if status == 'Transport':
        if total_p > 0 and done_p == total_p:
            status_text = "✅ ABGEFAHREN"; is_gray = True
        elif done_p > 0:
            status_text = f"⚠️ IN ARBEIT"
        else: 
            status_text = "⏳ TRANSPORT"
        icon = "🚛"
    else:
        status_text = "BESTAND"; icon = "🌲"

    ort_str = f"({ort})" if ort else ""
    zert_str = f"[{zert}]" if zert else ""
    base = f"{icon} {status_text} | {revier} {ort_str} {zert_str} | Los {los} | 📅 {datum_auf} | 📦 {vol:.2f} Fm"
    return f"`{base}`" if is_gray else base

def show_files_section(file_paths, key_prefix):
    if not file_paths or not isinstance(file_paths, list): return
    st.markdown("**📄 Original-Belege:**")
    cols = st.columns(len(file_paths))
    for i, path in enumerate(file_paths):
        if os.path.exists(path):
            with cols[i]:
                file_name = os.path.basename(path)
                with open(path, "rb") as f:
                    st.download_button(f"⬇️ {file_name}", f, file_name=file_name, key=f"{key_prefix}_{i}")
                if path.lower().endswith(('.png', '.jpg', '.jpeg')):
                    st.image(path, width=150)

def calculate_stats(df_p_all, df_s_all, df_prices):
    total_fm = df_p_all['Menge_Fm'].sum()
    fsc_mask = df_p_all['Zertifikat'].astype(str).str.contains("FSC", case=False, na=False)
    fsc_fm = df_p_all[fsc_mask]['Menge_Fm'].sum()
    
    done_fm = 0.0
    if 'Datum_Upload' in df_p_all.columns:
        uploads = df_p_all['Datum_Upload'].unique()
        for upload in uploads:
            p_upl = df_p_all[df_p_all['Datum_Upload'] == upload]
            s_upl = df_s_all[df_s_all['Datum_Upload'] == upload] if not df_s_all.empty else pd.DataFrame()
            vol_p_done = p_upl[p_upl['Geliefert'] == True]['Menge_Fm'].sum()
            vol_s_done = 0.0
            if not s_upl.empty:
                vol_s_done = s_upl[s_upl['Geliefert'] == True]['Volumen_Fm'].sum()
            done_fm += max(vol_p_done, vol_s_done)
            
    remaining_fm = total_fm - done_fm
    
    stats = {"total": {"Bu": 0, "Es": 0, "So": 0}}
    
    if not df_s_all.empty:
        df_s_all['Gruppe'] = df_s_all['Holzart'].apply(get_species_group)
        grp_tot = df_s_all.groupby('Gruppe')['Volumen_Fm'].sum()
        for k in stats["total"]: stats["total"][k] = grp_tot.get(k, 0)

    # --- WERT BERECHNUNG ---
    total_value = 0.0
    left_value = 0.0
    
    if not df_s_all.empty and not df_prices.empty:
        # Wir fügen temporär den Preis an die Tabelle an, um Summen zu bilden
        df_calc = df_s_all.copy()
        df_calc['Row_Value'] = df_calc.apply(lambda r: calculate_row_value(r, df_prices), axis=1)
        
        total_value = df_calc['Row_Value'].sum()
        
        # Check was geliefert ist
        # Vereinfachung: Wir schauen auf das 'Geliefert' Flag der Stämme
        # Wenn wir Polter-basiert abrechnen, ist das ungenauer, aber hier OK
        left_value = df_calc[df_calc['Geliefert'] == False]['Row_Value'].sum()

    return total_fm, done_fm, remaining_fm, fsc_fm, stats, total_value, left_value

# --- SEITENLEISTE (Verwaltung & Preise) ---
with st.sidebar:
    st.header("⚙️ Forst-Büro")
    
    # DATENBANK LADEN
    db_data = load_db()
    
    # --- PREISLISTE ---
    with st.expander("💶 Preisliste bearbeiten", expanded=False):
        st.caption("Preise pro Fm je Holzart & Güte")
        df_preise_db = pd.DataFrame(db_data.get('preise', []))
        
        edited_prices = st.data_editor(
            df_preise_db,
            num_rows="dynamic",
            column_config={
                "Holzart": st.column_config.TextColumn("Art (z.B. Bu, Es)", required=True),
                "Güte": st.column_config.TextColumn("Güte (z.B. B, C)", required=True),
                "Preis": st.column_config.NumberColumn("€ / Fm", format="%.2f €", min_value=0, step=1.0)
            },
            key="price_editor"
        )
        
        if st.button("Preise speichern", use_container_width=True):
            db_data['preise'] = edited_prices.to_dict('records')
            save_db(db_data)
            st.success("Gespeichert!")
            st.rerun()
            
    st.divider()
    
    if os.path.exists(DB_FILE):
        st.success(f"Datenbank aktiv\n\n📄 {len(db_data.get('polter', []))} Polter")
        with open(DB_FILE, "r") as f:
            st.download_button("⬇️ Backup (JSON)", f, file_name=f"backup_{datetime.now().strftime('%Y-%m-%d')}.json")
        
        df_p_ex, df_s_ex, _ = load_data_frames()
        if not df_p_ex.empty:
            st.download_button("📊 Export (Excel)", convert_df_to_excel(df_p_ex, df_s_ex), file_name=f"export.xlsx")
            
        st.divider()
        with st.expander("Gefahrenzone"):
            if st.button("🗑️ Komplett Reset"): os.remove(DB_FILE); st.rerun()
    else: st.info("⚪ Datenbank leer")

# --- APP START ---
st.title("🌲 Forst-Manager Pro")
tab1, tab2, tab3, tab4 = st.tabs(["📸 Scan & KI", "🗃️ Bestand & Werte", "🚛 Transport", "🗺️ War Room"])

# --- TAB 1: SCANNER ---
with tab1:
    # API KEY CHECK
    api_key = st.secrets.get("GOOGLE_API_KEY") 
    if not api_key:
        st.error("Kein API Key gefunden! Bitte in `.streamlit/secrets.toml` eintragen.")
        st.stop()
        
    client = genai.Client(api_key=api_key)
    uploaded_files = st.file_uploader("Holzlisten hochladen (PDF/Foto)", type=["pdf", "jpg", "png"], accept_multiple_files=True)

    if uploaded_files:
        if st.session_state.last_upload_count != len(uploaded_files):
            st.session_state.analyzed_data = None
            st.session_state.last_upload_count = len(uploaded_files)

        if st.session_state.analyzed_data is None:
            if st.button(f"🚀 {len(uploaded_files)} Dateien Analysieren"):
                prompt = load_prompt()
                agg = {"meta": {}, "polter": [], "staemme": [], "sum": 0.0, "cnt": 0.0}
                pbar = st.progress(0)
                
                for i, uf in enumerate(uploaded_files):
                    with st.spinner(f"Lese {uf.name}..."):
                        uf.seek(0)
                        cont = uf.read() if uf.type == "application/pdf" else Image.open(uf)
                        if uf.type == "application/pdf": cont = types.Part.from_bytes(data=cont, mime_type="application/pdf")
                        try:
                            # Modell Aufruf
                            res = client.models.generate_content(
                                model="gemini-2.0-flash", # Schnelleres, stabiles Modell
                                contents=[prompt, cont], 
                                config=types.GenerateContentConfig(response_mime_type="application/json")
                            )
                            s = json.loads(res.text.replace("```json", "").replace("```", "").strip())
                            
                            # Aggregation
                            if not agg["meta"]: agg["meta"] = s.get("meta", {})
                            else: 
                                for k in ["zertifikat", "revier_ort", "los"]:
                                    if s.get("meta", {}).get(k): agg["meta"][k] = s["meta"][k]
                                    
                            agg["sum"] += to_float(s.get("meta", {}).get("dokument_summe", 0))
                            agg["cnt"] += to_float(s.get("meta", {}).get("dokument_anzahl_staemme", 0))
                            agg["polter"].extend(s.get("polter", []))
                            agg["staemme"].extend(s.get("staemme", []))
                        except Exception as e: st.error(f"Fehler bei {uf.name}: {e}")
                    pbar.progress((i+1)/len(uploaded_files))
                
                agg["meta"]["dokument_summe"] = agg["sum"]
                st.session_state.analyzed_data = agg
                st.rerun()

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        st.warning("⚠️ Vorschau - Daten noch nicht gespeichert!")
        
        stems = data.get('staemme', [])
        doc_sum = to_float(data.get('meta', {}).get('dokument_summe', 0))
        stamm_sum = sum([to_float(s.get('fm', 0)) for s in stems]) 
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Festmeter (Dokument)", f"{doc_sum:.2f}")
        c2.metric("Festmeter (Summe Stämme)", f"{stamm_sum:.2f}", delta=round(stamm_sum-doc_sum, 2))
        c3.metric(f"Gefundene Polter", len(data.get('polter', [])))

        with st.expander("PDF-Check (Visuell)"):
            if uploaded_files:
                tabs = st.tabs([f.name for f in uploaded_files])
                for i, t in enumerate(tabs):
                    with t:
                        if uploaded_files[i].type=="application/pdf":
                            imgs = create_highlighted_pdf_images(uploaded_files[i], 0, 0, data.get('polter', []))
                            if imgs: 
                                cols = st.columns(len(imgs))
                                for j, im in enumerate(imgs): with cols[j]: st.image(im, caption=f"S.{j+1}", use_container_width=True)
                        else: st.image(uploaded_files[i], width=300)
        
        with st.expander("Detail-Daten ansehen"):
            st.dataframe(pd.DataFrame(stems))
        
        if st.button("💾 In Datenbank speichern", type="primary"):
            if save_to_json(data, uploaded_files):
                st.success("Erfolgreich gespeichert!")
                st.session_state.analyzed_data = None
                st.rerun()

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄 Aktualisieren", key="r_b"): st.cache_data.clear()
    df_p, df_s, df_prices = load_data_frames()
    
    if df_p.empty: st.info("Keine Daten im Bestand.")
    else:
        st.subheader("📊 Finanzen & Lager")
        
        # Statistik & Wert Berechnung
        tot, done, left, fsc, s, val_tot, val_left = calculate_stats(df_p, df_s, df_prices)
        val_done = val_tot - val_left
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Gesamtwert (Erwartet)", f"{val_tot:,.2f} €", f"{tot:.1f} Fm")
        c2.metric("Umsatz (Geliefert)", f"{val_done:,.2f} €", f"{done:.1f} Fm")
        c3.metric("Lagerwert (Im Wald)", f"{val_left:,.2f} €", f"{left:.1f} Fm")
        c4.metric("Holzarten (Gesamt)", f"Bu: {s['total']['Bu']:.0f} Fm", f"Es: {s['total']['Es']:.0f} Fm")
        
        st.divider()
        
        # Bestand Karte
        pts = [{"lat": parse_gps_for_map(r['Lat']), "lon": parse_gps_for_map(r['Lon']), "info": f"Los {r['Los_Nr']}"} for _, r in df_p.iterrows() if parse_gps_for_map(r['Lat'])>0]
        if pts:
            m = folium.Map([pd.DataFrame(pts).lat.mean(), pd.DataFrame(pts).lon.mean()], zoom_start=11)
            for p in pts: folium.Marker([p['lat'], p['lon']], popup=p['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width="100%", height=250, key="gm1")

        st.subheader("📂 Akten & Listen")
        if 'Datum_Upload' in df_p.columns:
            df_p = df_p.sort_values("Datum_Upload", ascending=False)
            groups = df_p.groupby(['Datum_Upload', 'Revier', 'Los_Nr'])
            
            for (ut, rev, los), grp in groups:
                is_trans = grp['Status'].iloc[0] == 'Transport'
                note_val = grp['Notiz'].iloc[0] if 'Notiz' in grp.columns else ""
                match = df_s[df_s['Datum_Upload'] == ut].copy() if not df_s.empty else pd.DataFrame()
                belege = grp['Belege'].iloc[0] if 'Belege' in grp.columns else []
                
                # Titel generieren
                ort = grp['Ort'].iloc[0] if 'Ort' in grp.columns else ""
                zert = grp['Zertifikat'].iloc[0] if 'Zertifikat' in grp.columns else ""
                datum_auf = grp['Datum_Aufnahme'].iloc[0] if 'Datum_Aufnahme' in grp.columns else ""
                final_title = get_list_title(ut, grp, match, rev, los, ort, zert, datum_auf)

                with st.expander(final_title):
                    show_files_section(belege, f"belege_b_{ut}")
                    
                    c_act, c_cnt = st.columns([1, 3])
                    with c_act:
                        if not is_trans and st.button("🚀 An Fuhrmann senden", key=f"mt_{ut}"):
                            move_to_transport(ut); st.rerun()
                        maps_url = get_google_maps_route_url(grp)
                        if maps_url: st.link_button("🗺️ Route planen", maps_url)
                    with c_cnt:
                        new_note = st.text_input("Notiz:", value=note_val, key=f"note_b_{ut}")

                    # Tabelle Polter & Stämme
                    if not match.empty:
                        st.markdown("**Einzelstämme & Kalkulation**")
                        
                        # Wertberechnung pro Zeile für Anzeige
                        match['Kalk_Preis'] = match.apply(lambda r: calculate_row_value(r, df_prices), axis=1)
                        
                        match['Display'] = (
                            match['Holzart'] + " " + match['Gue_Kl'] + " (" + 
                            match['Laenge'].astype(str) + "m / " + match['Durchmesser'].astype(str) + "cm)"
                        )
                        
                        edited_stems = st.data_editor(
                            match[['Display', 'Volumen_Fm', 'Kalk_Preis', 'Info']], 
                            key=f"ed_st_b_{ut}", 
                            hide_index=True,
                            column_config={
                                "Display": st.column_config.TextColumn("Stamm", disabled=True), 
                                "Volumen_Fm": st.column_config.NumberColumn("Fm", disabled=True),
                                "Kalk_Preis": st.column_config.NumberColumn("Wert", format="%.2f €", disabled=True),
                                "Info": st.column_config.TextColumn("Info / Notiz")
                            }
                        )
                        match['Info'] = edited_stems['Info']
                    
                    if st.button("💾 Speichern", key=f"sv_b_{ut}"):
                        apply_batch_updates(ut, new_note, pd.DataFrame(), match)
                        st.success("Gespeichert!"); st.rerun()
                    
                    if st.button("🗑️ Liste Löschen", key=f"dl_{ut}"):
                        delete_entry_by_timestamp(ut); st.rerun()

# --- TAB 3: TRANSPORT ---
with tab3:
    if st.button("🔄", key="r_t"): st.cache_data.clear()
    df_p, df_s, _ = load_data_frames() # Preise hier nicht zwingend nötig
    df_pt = df_p[df_p['Status'] == 'Transport'] if not df_p.empty else pd.DataFrame()
    
    if df_pt.empty: st.info("Alle Aufträge erledigt.")
    else:
        st.subheader("🚛 Aktive Fahraufträge")
        df_pt = df_pt.sort_values("Datum_Upload", ascending=False)
        groups = df_pt.groupby(['Datum_Upload', 'Revier', 'Los_Nr'])
        
        for (ut, rev, los), grp in groups:
            done = len(grp[grp['Geliefert'] == True]); total = len(grp)
            match = df_s[df_s['Datum_Upload'] == ut].copy() if not df_s.empty else pd.DataFrame()
            belege = grp['Belege'].iloc[0] if 'Belege' in grp.columns else []
            note_val = grp['Notiz'].iloc[0] if 'Notiz' in grp.columns else ""
            
            final_title = get_list_title(ut, grp, match, rev, los, "", "", "") # Kurzfassung
            
            with st.expander(final_title, expanded=True):
                st.progress(done/total if total>0 else 0)
                
                col_a, col_b = st.columns([1,1])
                with col_a: 
                    maps_url = get_google_maps_route_url(grp)
                    if maps_url: st.link_button("🗺️ Navigation starten", maps_url)
                with col_b:
                    show_files_section(belege, f"belege_t_{ut}")

                n_note = st.text_area("Notiz vom Fahrer:", value=note_val, key=f"note_t_{ut}")

                # Polter Abhaken
                st.markdown("### Lade-Liste")
                edited_p = st.data_editor(
                    grp[['Polter_Nr', 'Menge_Fm', 'Geliefert']],
                    column_config={
                        "Menge_Fm": st.column_config.NumberColumn("Fm", width="small", disabled=True),
                        "Geliefert": st.column_config.CheckboxColumn("Geladen?", default=False)
                    },
                    hide_index=True, key=f"ed_p_t_{ut}"
                )
                
                # Karte für den Fahrer
                v_pts = []
                for _, r in grp.iterrows():
                    la, lo = parse_gps_for_map(r.get('Lat','')), parse_gps_for_map(r.get('Lon',''))
                    if la > 0: v_pts.append({"lat": la, "lon": lo, "c": "gray" if r['Geliefert'] else "red"})
                if v_pts:
                    m = folium.Map([pd.DataFrame(v_pts).lat.mean(), pd.DataFrame(v_pts).lon.mean()], zoom_start=13)
                    for p in v_pts: folium.Marker([p['lat'], p['lon']], icon=folium.Icon(color=p['c'], icon="truck", prefix='fa')).add_to(m)
                    st_folium(m, width="100%", height=250, key=f"mp_t_{ut}")

                if st.button("Auftrag Speichern", key=f"sv_t_{ut}", type="primary"):
                    apply_batch_updates(ut, n_note, edited_p, pd.DataFrame()) # Stämme hier irrelevant für Fahrer-Check
                    st.success("Gespeichert!"); st.rerun()
                
                if done == total and total > 0:
                    st.success("✅ Auftrag komplett!")
                    if st.button("Archivieren", key=f"arc_{ut}"):
                        delete_entry_by_timestamp(ut); st.rerun()

# --- TAB 4: WELTKARTE ---
with tab4:
    if st.button("🔄", key="r_w"): st.cache_data.clear()
    df_p, _, _ = load_data_frames()
    
    if df_p.empty: st.info("Keine Daten.")
    else:
        st.subheader("🗺️ War Room")
        show_only_pending = st.toggle("Nur offene Aufträge (Rot)", value=False)
        
        df_map = df_p[df_p['Geliefert'] == False] if show_only_pending else df_p
        
        map_points = []
        for _, row in df_map.iterrows():
            lat = parse_gps_for_map(row.get('Lat', ''))
            lon = parse_gps_for_map(row.get('Lon', ''))
            if lat > 0 and lon > 0:
                color = "green" if row.get('Geliefert', False) else "red"
                info = f"<b>{row.get('Revier')}</b><br>Los {row.get('Los_Nr')}<br>{row.get('Menge_Fm')} Fm"
                map_points.append({"lat": lat, "lon": lon, "info": info, "color": color})
        
        if map_points:
            avg_lat = pd.DataFrame(map_points)['lat'].mean()
            avg_lon = pd.DataFrame(map_points)['lon'].mean()
            m = folium.Map([avg_lat, avg_lon], zoom_start=10)
            for p in map_points:
                folium.Marker([p['lat'], p['lon']], popup=p['info'], icon=folium.Icon(color=p['color'], icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width="100%", height=500, key="global_map")
        else:
            st.warning("Keine GPS-Daten verfügbar.")
