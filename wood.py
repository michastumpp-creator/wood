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
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")
DB_FILE = "forst_daten.json"
UPLOAD_DIR = "belege"
BACKUP_DIR = "backups"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload_count' not in st.session_state:
    st.session_state.last_upload_count = 0
if 'messages' not in st.session_state:
    st.session_state.messages = []

# --- HELFER ---
def load_prompt():
    try:
        if os.path.exists("system_prompt.txt"):
            with open("system_prompt.txt", "r", encoding="utf-8") as f: return f.read()
    except: pass
    return None 

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
        if 47 < f < 55 or 5 < f < 15: return f
    except: pass
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
        if page_num > 1: break 
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

# --- BACKUP ---
def create_auto_backup():
    if os.path.exists(DB_FILE):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"auto_backup_{timestamp}.json")
            shutil.copy2(DB_FILE, backup_path)
            backups = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.endswith(".json")])
            while len(backups) > 50:
                os.remove(backups[0]); backups.pop(0)
        except Exception as e: print(f"Backup Fehler: {e}")

# --- DB ---
def load_db():
    if not os.path.exists(DB_FILE): return {"polter": [], "staemme": []}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {"polter": [], "staemme": []}

def save_db(db_data):
    create_auto_backup()
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        st.error(f"Fehler: {e}")
        return False

# --- LOGIK ---
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
        db['staemme'].append({
            "Datum_Upload": timestamp, "Los_Nr": los, "Revier": revier, "WNr": wnr,
            "Holzart": s.get('art', ''), "Laenge": fmt(s.get('l')), "Durchmesser": fmt(s.get('d')),
            "Gue_Kl": s.get('klasse', ''), "Volumen_Fm": fmt(s.get('fm')),
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
        for col, val in [('Status', 'Bestand'), ('Geliefert', False), ('Info', ''), ('Holzart', '')]:
            if col not in df_s.columns: df_s[col] = val
    return df_p, df_s

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
            status_text = "✅ KOMPLETT ABGEFAHREN"; is_gray = True
        elif done_p > 0:
            status_text = f"⚠️ TEILWEISE ({done_p}/{total_p})"
        else: status_text = "⏳ WARTET AUF ABFUHR"
        icon = "🚛"
    else:
        status_text = "🌲 BESTAND"; icon = "🌲"

    ort_str = f"({ort})" if ort else ""
    zert_str = f"[{zert}]" if zert else ""
    base = f"{icon} {status_text} | {revier} {ort_str} {zert_str} | Los {los} | 📅 {datum_auf} | 📦 {vol:.2f} Fm | 🪵 {stamm_anzahl} Stk"
    return f"`{base}`" if is_gray else base

def show_files_section(file_paths):
    if not file_paths or not isinstance(file_paths, list): return
    st.markdown("**📄 Original-Belege:**")
    cols = st.columns(len(file_paths))
    for i, path in enumerate(file_paths):
        if os.path.exists(path):
            with cols[i]:
                file_name = os.path.basename(path)
                with open(path, "rb") as f:
                    st.download_button(f"⬇️ {file_name}", f, file_name=file_name)
                if path.lower().endswith(('.png', '.jpg', '.jpeg')):
                    st.image(path, width=150)
        else: st.warning(f"Datei fehlt: {path}")

# --- HOLZARTEN KLASSIFIZIERUNG ---
def get_species_group(holzart):
    h = str(holzart).lower()
    if "bu" in h: return "Bu"
    if "es" in h: return "Es"
    return "So"

def calculate_stats(df_p_all, df_s_all):
    """Berechnet komplexe Statistiken inkl. Einzelstamm-Abhaken"""
    
    # 1. Gesamt Gekauft
    total_fm = df_p_all['Menge_Fm'].sum()
    
    # 2. FSC
    fsc_mask = df_p_all['Zertifikat'].astype(str).str.contains("FSC", case=False, na=False)
    fsc_fm = df_p_all[fsc_mask]['Menge_Fm'].sum()
    
    # 3. Done & Left Berechnung (Komplex wegen Einzelstämmen)
    done_fm = 0.0
    
    # Wir iterieren durch alle Uploads
    if 'Datum_Upload' in df_p_all.columns:
        uploads = df_p_all['Datum_Upload'].unique()
        for upload in uploads:
            # Daten für diesen Upload
            p_upl = df_p_all[df_p_all['Datum_Upload'] == upload]
            s_upl = df_s_all[df_s_all['Datum_Upload'] == upload] if not df_s_all.empty else pd.DataFrame()
            
            # Polter Done Summe
            vol_p_done = p_upl[p_upl['Geliefert'] == True]['Menge_Fm'].sum()
            
            # Stämme Done Summe
            vol_s_done = 0.0
            if not s_upl.empty:
                vol_s_done = s_upl[s_upl['Geliefert'] == True]['Volumen_Fm'].sum()
            
            # Logic: Wenn Stämme angehakt sind (>0), aber der Polter-Wert niedriger ist, 
            # nehmen wir die Stämme (Teillieferung).
            # Wenn Polter komplett abgehakt (vol_p_done), ist das meist der volle Wert.
            # Wir nehmen das Maximum für die sicherste Schätzung.
            done_fm += max(vol_p_done, vol_s_done)
            
    remaining_fm = total_fm - done_fm
    
    # 4. Holzarten Aufteilung (für alle 3 Kategorien)
    # Wir brauchen Hilfs-Dataframes für die Stämme
    
    stats = {
        "total": {"Bu": 0, "Es": 0, "So": 0},
        "done": {"Bu": 0, "Es": 0, "So": 0},
        "left": {"Bu": 0, "Es": 0, "So": 0},
        "fsc": {"Bu": 0, "Es": 0, "So": 0}
    }
    
    if not df_s_all.empty:
        df_s_all['Gruppe'] = df_s_all['Holzart'].apply(get_species_group)
        
        # TOTAL
        grp_tot = df_s_all.groupby('Gruppe')['Volumen_Fm'].sum()
        for k in stats["total"]: stats["total"][k] = grp_tot.get(k, 0)
        
        # DONE & LEFT
        # Hier ist es schwierig exakt zu sein, wenn ganze Polter (ohne Stamm-Haken) geliefert sind.
        # Wir machen eine Annäherung: Wir schauen uns NUR die Einzelstämme an.
        # Wenn ein Polter "Geliefert" ist, setzen wir virtuell alle seine Stämme auf "Geliefert" für die Statistik.
        
        # Kopie für Berechnung
        df_calc = df_s_all.copy()
        
        # Wir holen uns die Uploads, wo Polter fertig sind
        done_uploads_polter_based = []
        if 'Datum_Upload' in df_p_all.columns:
            for upload in df_p_all['Datum_Upload'].unique():
                p_upl = df_p_all[df_p_all['Datum_Upload'] == upload]
                if p_upl['Geliefert'].all() and len(p_upl) > 0: # Wenn alle Polter der Liste fertig
                    done_uploads_polter_based.append(upload)
        
        # Wir markieren Stämme als done, wenn sie explizit done sind ODER ihre Liste fertig ist
        df_calc['Is_Done'] = df_calc['Geliefert'] | df_calc['Datum_Upload'].isin(done_uploads_polter_based)
        
        grp_done = df_calc[df_calc['Is_Done']].groupby('Gruppe')['Volumen_Fm'].sum()
        grp_left = df_calc[~df_calc['Is_Done']].groupby('Gruppe')['Volumen_Fm'].sum()
        
        for k in stats["done"]: stats["done"][k] = grp_done.get(k, 0)
        for k in stats["left"]: stats["left"][k] = grp_left.get(k, 0)
        
        # FSC
        # Stämme filtern, die zu FSC-Poltern gehören
        fsc_uploads = df_p_all[fsc_mask]['Datum_Upload'].unique() if 'Datum_Upload' in df_p_all.columns else []
        df_fsc = df_s_all[df_s_all['Datum_Upload'].isin(fsc_uploads)]
        grp_fsc = df_fsc.groupby('Gruppe')['Volumen_Fm'].sum()
        for k in stats["fsc"]: stats["fsc"][k] = grp_fsc.get(k, 0)

    return total_fm, done_fm, remaining_fm, fsc_fm, stats

# --- SEITENLEISTE ---
with st.sidebar:
    st.header("⚙️ Verwaltung")
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f: d = json.load(f)
            st.success(f"Datenbank aktiv\n\n📄 {len(d.get('polter', []))} Polter")
            st.divider()
            with open(DB_FILE, "r") as f:
                st.download_button("⬇️ Backup (JSON)", f, file_name=f"backup_{datetime.now().strftime('%Y-%m-%d')}.json")
            df_p_ex, df_s_ex = load_data_frames()
            if not df_p_ex.empty:
                st.download_button("📊 Export (Excel)", convert_df_to_excel(df_p_ex, df_s_ex), file_name=f"export.xlsx")
            st.divider()
            with st.expander("Gefahrenzone"):
                if st.button("🗑️ Reset All"): os.remove(DB_FILE); st.rerun()
        except:
            st.error("🔴 Datei beschädigt!")
            if st.button("🗑️ Reset"): os.remove(DB_FILE); st.rerun()
    else: st.info("⚪ Datenbank leer")

# --- APP START ---
st.title("🌲 Forst-Verwaltung")
tab1, tab2, tab3 = st.tabs(["📸 Scan & Analyse", "🗃️ Bestand", "🚛 Transport"])

# --- TAB 1: SCANNER ---
with tab1:
    try: client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except: st.error("Key fehlt!"); st.stop()
    uploaded_files = st.file_uploader("Holzlisten hochladen", type=["pdf", "jpg", "png"], accept_multiple_files=True)

    if uploaded_files:
        if st.session_state.last_upload_count != len(uploaded_files):
            st.session_state.analyzed_data = None; st.session_state.messages = [] 
            st.session_state.last_upload_count = len(uploaded_files)

        if st.session_state.analyzed_data is None:
            if st.button(f"🚀 {len(uploaded_files)} Dateien Analysieren"):
                prompt = load_prompt() or """Analysiere Holzliste: META(Gesamtmenge, Stämme gezählt, Revier Ort, Zertifikat), STÄMME(Tabelle), POLTER(GPS)."""
                agg = {"meta": {}, "polter": [], "staemme": [], "sum": 0.0, "cnt": 0.0}
                pbar = st.progress(0)
                for i, uf in enumerate(uploaded_files):
                    with st.spinner(f"Lese {uf.name}..."):
                        uf.seek(0)
                        cont = uf.read() if uf.type == "application/pdf" else Image.open(uf)
                        if uf.type == "application/pdf": cont = types.Part.from_bytes(data=cont, mime_type="application/pdf")
                        try:
                            res = client.models.generate_content(model="gemini-3-flash-preview", contents=[prompt, cont], config=types.GenerateContentConfig(response_mime_type="application/json"))
                            s = json.loads(res.text.replace("```json", "").replace("```", "").strip())
                            if not agg["meta"]: agg["meta"] = s.get("meta", {})
                            else: 
                                for k in ["zertifikat", "revier_ort"]:
                                    if s.get("meta", {}).get(k): agg["meta"][k] = s["meta"][k]
                            agg["sum"] += to_float(s.get("meta", {}).get("dokument_summe", 0))
                            agg["cnt"] += to_float(s.get("meta", {}).get("dokument_anzahl_staemme", 0))
                            agg["polter"].extend(s.get("polter", [])); agg["staemme"].extend(s.get("staemme", []))
                        except Exception as e: st.error(f"Fehler: {e}")
                    pbar.progress((i+1)/len(uploaded_files))
                
                agg["meta"]["dokument_summe"] = agg["sum"]; agg["meta"]["dokument_anzahl_staemme"] = agg["cnt"]
                st.session_state.analyzed_data = agg
                st.rerun()

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        st.warning("⚠️ Daten noch nicht gespeichert!")
        
        st.divider(); st.subheader("💬 KI-Assistent")
        for m in st.session_state.messages: st.chat_message(m["role"]).write(m["content"])
        if u := st.chat_input("Frage..."):
            st.session_state.messages.append({"role": "user", "content": u}); st.chat_message("user").write(u)
            with st.spinner("..."):
                r = client.models.generate_content(model="gemini-3-flash-preview", contents=f"Daten: {json.dumps(data)}\nFrage: {u}\nAntworte kurz.")
                st.session_state.messages.append({"role": "assistant", "content": r.text}); st.rerun()
        
        st.divider()
        stems = data.get('staemme', [])
        valid_stems = [s for s in stems if not s.get('klammer')]
        doc_sum = to_float(data.get('meta', {}).get('dokument_summe', 0))
        stamm_sum = sum([to_float(s.get('fm', 0)) for s in stems]) 
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Festmeter", f"{doc_sum:.2f} / {stamm_sum:.2f}", delta=round(stamm_sum-doc_sum, 2))
        c2.metric("Stückzahl", f"{int(to_float(data.get('meta', {}).get('dokument_anzahl_staemme', 0)))} / {len(valid_stems)}", delta=len(valid_stems)-int(to_float(data.get('meta', {}).get('dokument_anzahl_staemme', 0))))
        c3.metric(f"Polter", len(data.get('polter', [])))

        with st.expander("PDF-Check"):
            if uploaded_files:
                tabs = st.tabs([f.name for f in uploaded_files])
                for i, t in enumerate(tabs):
                    with t:
                        if uploaded_files[i].type=="application/pdf":
                            imgs = create_highlighted_pdf_images(uploaded_files[i], 0, 0, data.get('polter', []))
                            if imgs: 
                                cols = st.columns(len(imgs))
                                for j, im in enumerate(imgs): 
                                    with cols[j]: st.image(im, caption=f"S.{j+1}", use_container_width=True)
                        else: st.image(uploaded_files[i], width=300)
        with st.expander("Daten"): st.dataframe(pd.DataFrame(stems))
        
        if st.button("💾 Speichern"):
            if save_to_json(data, uploaded_files):
                st.success("Gespeichert!")
                st.session_state.analyzed_data = None
                st.rerun()

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄", key="r_b"): st.cache_data.clear()
    df_p, df_s = load_data_frames()
    
    if df_p.empty: st.info("Leer.")
    else:
        st.subheader("📊 Lager-Übersicht")
        
        # BERECHNUNG
        tot, done, left, fsc, s = calculate_stats(df_p, df_s)
        
        # ANZEIGE
        c1, c2, c3, c4 = st.columns(4)
        
        c1.metric("🪵 Gekauft (Gesamt)", f"{tot:.2f} Fm")
        c1.caption(f"Bu: {s['total']['Bu']:.1f} | Es: {s['total']['Es']:.1f} | So: {s['total']['So']:.1f}")
        
        c2.metric("🚛 Abgefahren", f"{done:.2f} Fm")
        c2.caption(f"Bu: {s['done']['Bu']:.1f} | Es: {s['done']['Es']:.1f} | So: {s['done']['So']:.1f}")
        
        c3.metric("🌲 Noch im Wald", f"{left:.2f} Fm")
        c3.caption(f"Bu: {s['left']['Bu']:.1f} | Es: {s['left']['Es']:.1f} | So: {s['left']['So']:.1f}")
        
        c4.metric("✅ Davon FSC", f"{fsc:.2f} Fm")
        c4.caption(f"Bu: {s['fsc']['Bu']:.1f} | Es: {s['fsc']['Es']:.1f} | So: {s['fsc']['So']:.1f}")
        
        st.divider()
        
        pts = [{"lat": parse_gps_for_map(r['Lat']), "lon": parse_gps_for_map(r['Lon']), "info": f"Los {r['Los_Nr']}"} for _, r in df_p.iterrows() if parse_gps_for_map(r['Lat'])>0]
        if pts:
            m = folium.Map([pd.DataFrame(pts).lat.mean(), pd.DataFrame(pts).lon.mean()], zoom_start=9)
            for p in pts: folium.Marker([p['lat'], p['lon']], popup=p['info'], icon=folium.Icon(color="blue", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width="100%", height=200, key="gm1")

        st.divider(); st.subheader("📂 Akten")
        if 'Datum_Upload' in df_p.columns:
            df_p = df_p.sort_values("Datum_Upload", ascending=False)
            groups = df_p.groupby(['Datum_Upload', 'Revier', 'Los_Nr'])
            
            for (ut, rev, los), grp in groups:
                is_trans = grp['Status'].iloc[0] == 'Transport'
                note_val = grp['Notiz'].iloc[0] if 'Notiz' in grp.columns else ""
                match = df_s[df_s['Datum_Upload'] == ut].copy() if not df_s.empty else pd.DataFrame()
                belege = grp['Belege'].iloc[0] if 'Belege' in grp.columns else []
                
                ort = grp['Ort'].iloc[0] if 'Ort' in grp.columns else ""
                zert = grp['Zertifikat'].iloc[0] if 'Zertifikat' in grp.columns else ""
                datum_auf = grp['Datum_Aufnahme'].iloc[0] if 'Datum_Aufnahme' in grp.columns else ""
                
                final_title = get_list_title(ut, grp, match, rev, los, ort, zert, datum_auf)

                with st.expander(final_title):
                    show_files_section(belege)
                    st.divider()
                    
                    new_note = st.text_area("Notiz:", value=note_val, key=f"note_b_{ut}", height=68)
                    
                    c_act, c_cnt = st.columns([1, 4])
                    with c_act:
                        if not is_trans and st.button("🚀 An Fuhrmann", key=f"mt_{ut}"):
                            move_to_transport(ut); st.rerun()
                        
                        maps_url = get_google_maps_route_url(grp)
                        if maps_url: st.link_button("🗺️ Route planen", maps_url)

                    c1, c2 = st.columns([1, 1])
                    c1.markdown("**Polter**"); c1.dataframe(grp[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                    
                    edited_stems = pd.DataFrame()
                    with c2:
                        if not match.empty:
                            st.markdown("**Stämme (Info editierbar)**")
                            cols_show = [c for c in ['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Info'] if c in match.columns]
                            edited_stems = st.data_editor(
                                match[cols_show], 
                                key=f"ed_st_b_{ut}", 
                                hide_index=True,
                                column_config={"Info": st.column_config.TextColumn("Info", width="small")}
                            )
                        else: st.caption("Keine Stämme.")
                    
                    if st.button("💾 Alles speichern (Notiz & Info)", key=f"sv_b_{ut}"):
                        apply_batch_updates(ut, new_note, pd.DataFrame(), edited_stems)
                        st.success("Gespeichert!"); st.rerun()
                    
                    st.divider()
                    if st.button("🗑️ Liste Löschen", key=f"dl_{ut}"):
                        delete_entry_by_timestamp(ut); st.rerun()

# --- TAB 3: TRANSPORT ---
with tab3:
    if st.button("🔄", key="r_t"): st.cache_data.clear()
    df_p, df_s = load_data_frames()
    df_pt = df_p[df_p['Status'] == 'Transport'] if not df_p.empty else pd.DataFrame()
    
    if df_pt.empty: st.info("Nichts im Transport.")
    else:
        st.subheader("🚛 Laufende Aufträge")
        df_pt = df_pt.sort_values("Datum_Upload", ascending=False)
        groups = df_pt.groupby(['Datum_Upload', 'Revier', 'Los_Nr'])
        
        for (ut, rev, los), grp in groups:
            done = len(grp[grp['Geliefert'] == True]); total = len(grp)
            note_val = grp['Notiz'].iloc[0] if 'Notiz' in grp.columns else ""
            match = df_s[df_s['Datum_Upload'] == ut].copy() if not df_s.empty else pd.DataFrame()
            belege = grp['Belege'].iloc[0] if 'Belege' in grp.columns else []
            
            ort = grp['Ort'].iloc[0] if 'Ort' in grp.columns else ""
            zert = grp['Zertifikat'].iloc[0] if 'Zertifikat' in grp.columns else ""
            datum_auf = grp['Datum_Aufnahme'].iloc[0] if 'Datum_Aufnahme' in grp.columns else ""
            
            final_title = get_list_title(ut, grp, match, rev, los, ort, zert, datum_auf)
            
            with st.expander(final_title):
                st.progress(done/total if total>0 else 0)
                show_files_section(belege)
                
                maps_url = get_google_maps_route_url(grp)
                if maps_url: st.link_button("🗺️ Route planen", maps_url)
                
                n_note = st.text_area("Notiz Fuhrmann:", value=note_val, key=f"note_t_{ut}")

                c1, c2 = st.columns([1, 1])
                edited_p = pd.DataFrame()
                edited_s = pd.DataFrame()

                with c1:
                    st.markdown("### 1. Polter Abhaken")
                    edited_p = st.data_editor(
                        grp[['Polter_Nr', 'Menge_Fm', 'Geliefert']],
                        column_config={"Geliefert": st.column_config.CheckboxColumn("Fertig?", default=False)},
                        hide_index=True, key=f"ed_p_t_{ut}"
                    )
                
                with c2:
                    st.markdown("### 2. Einzelstämme")
                    if not match.empty:
                        cols_s = [c for c in ['WNr', 'Holzart', 'Volumen_Fm', 'Geliefert', 'Info'] if c in match.columns]
                        edited_s = st.data_editor(
                            match[cols_s],
                            column_config={
                                "Geliefert": st.column_config.CheckboxColumn("Geliefert", default=False),
                                "Info": st.column_config.TextColumn("Info")
                            },
                            hide_index=True, key=f"ed_s_t_{ut}"
                        )
                    else: st.caption("Keine Einzelstämme.")

                v_pts = []
                for _, r in grp.iterrows():
                    la, lo = parse_gps_for_map(r.get('Lat','')), parse_gps_for_map(r.get('Lon',''))
                    if la > 0: v_pts.append({"lat": la, "lon": lo, "c": "gray" if r['Geliefert'] else "red"})
                if v_pts:
                    m = folium.Map([pd.DataFrame(v_pts).lat.mean(), pd.DataFrame(v_pts).lon.mean()], zoom_start=13)
                    for p in v_pts: folium.Marker([p['lat'], p['lon']], icon=folium.Icon(color=p['c'], icon="truck", prefix='fa')).add_to(m)
                    st_folium(m, width="100%", height=250, key=f"mp_t_{ut}")

                if st.button("💾 Alles speichern (Notiz & Haken)", key=f"sv_t_{ut}"):
                    apply_batch_updates(ut, n_note, edited_p, edited_s)
                    st.success("Gespeichert!"); st.rerun()
                
                if done == total and total > 0:
                    st.success("✅ Auftrag erledigt!")
                
                st.divider()
                if st.button("🗑️ Archivieren (Endgültig löschen)", key=f"arc_{ut}"):
                    delete_entry_by_timestamp(ut); st.rerun()
