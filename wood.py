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
from google import genai
from google.genai import types

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")
DB_FILE = "forst_daten.json"

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

# --- LOKALE DATENBANK ---
def load_db():
    if not os.path.exists(DB_FILE): return {"polter": [], "staemme": []}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {"polter": [], "staemme": []}

def save_db(db_data):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        st.error(f"Fehler: {e}")
        return False

# --- DATEN OPERATIONEN ---
def save_to_json(data):
    db = load_db()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            "Status": "Bestand", "Geliefert": False, "Notiz": ""
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

# --- BATCH UPDATE FUNKTION (Speichert alles auf einmal) ---
def apply_batch_updates(timestamp, new_note, polter_df, staemme_df):
    """
    Nimmt die bearbeiteten DataFrames und speichert alles in einem Rutsch.
    """
    db = load_db()
    
    # 1. Notiz Update
    for p in db['polter']:
        if p.get('Datum_Upload') == timestamp:
            p['Notiz'] = new_note

    # 2. Polter Update (Geliefert Status)
    if not polter_df.empty:
        for index, row in polter_df.iterrows():
            for p in db['polter']:
                if p.get('Datum_Upload') == timestamp and str(p.get('Polter_Nr')) == str(row['Polter_Nr']):
                    if 'Geliefert' in row: p['Geliefert'] = row['Geliefert']
                    break
    
    # 3. Stämme Update (Geliefert & Info)
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
        for col, val in [('Status', 'Bestand'), ('Geliefert', False), ('Notiz', '')]:
            if col not in df_p.columns: df_p[col] = val
    df_s = pd.DataFrame(db['staemme'])
    if not df_s.empty:
        if 'Volumen_Fm' in df_s.columns: df_s['Volumen_Fm'] = df_s['Volumen_Fm'].apply(to_float)
        for col, val in [('Status', 'Bestand'), ('Geliefert', False), ('Info', '')]:
            if col not in df_s.columns: df_s[col] = val
    return df_p, df_s

# --- SEITENLEISTE ---
with st.sidebar:
    st.header("Datenbank Status")
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f: d = json.load(f)
            st.success(f"🟢 OK ({len(d.get('polter', []))} Polter)")
        except:
            st.error("🔴 Datei beschädigt!")
            if st.button("🗑️ Reset DB"):
                os.remove(DB_FILE); st.rerun()
    else: st.info("⚪ Leer")

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
        doc_sum = to_float(data.get('meta', {}).get('dokument_summe', 0))
        stamm_sum = sum([to_float(s.get('fm', 0)) for s in stems]) 
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Festmeter", f"{doc_sum:.2f} / {stamm_sum:.2f}", delta=round(stamm_sum-doc_sum, 2))
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
            if save_to_json(data):
                st.success("Gespeichert!")
                st.session_state.analyzed_data = None
                st.rerun()

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄", key="r_b"): st.cache_data.clear()
    df_p, df_s = load_data_frames()
    
    if df_p.empty: st.info("Leer.")
    else:
        st.subheader("📊 Lager")
        c_st, c_mp = st.columns([1, 1])
        with c_st:
            if not df_s.empty and 'Holzart' in df_s.columns:
                stats = df_s.groupby('Holzart')['Volumen_Fm'].sum().sort_values(ascending=False)
                st.dataframe(stats, height=150, use_container_width=True); st.caption(f"Gesamt: {stats.sum():.2f} Fm")
        with c_mp:
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
                icon, suffix = ("🚛 ", " (BEIM FUHRMANN)") if is_trans else ("🌲 ", "")
                note_val = grp['Notiz'].iloc[0] if 'Notiz' in grp.columns else ""
                
                with st.expander(f"{icon}{rev} | Los {los} | {grp['Menge_Fm'].sum():.2f} Fm{suffix}"):
                    # Eingabe-Widgets (KEIN AUTO-SAVE!)
                    new_note = st.text_area("Notiz:", value=note_val, key=f"note_b_{ut}", height=68)
                    
                    c_act, c_cnt = st.columns([1, 4])
                    with c_act:
                        if not is_trans and st.button("Start Transport", key=f"mt_{ut}"):
                            move_to_transport(ut); st.rerun()
                        if st.button("Löschen", key=f"dl_{ut}"):
                            delete_entry_by_timestamp(ut); st.rerun()

                    c1, c2 = st.columns([1, 1])
                    c1.markdown("**Polter**"); c1.dataframe(grp[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                    
                    match = df_s[df_s['Datum_Upload'] == ut].copy() if not df_s.empty else pd.DataFrame()
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
                    
                    # DER SPEICHER BUTTON
                    if st.button("💾 Änderungen speichern", key=f"sv_b_{ut}"):
                        apply_batch_updates(ut, new_note, pd.DataFrame(), edited_stems)
                        st.success("Gespeichert!"); st.rerun()

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
            
            with st.expander(f"🚛 {rev} | Los {los} | {done}/{total} Polter fertig"):
                st.progress(done/total if total>0 else 0)
                n_note = st.text_area("Notiz:", value=note_val, key=f"note_t_{ut}")

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
                    match = df_s[df_s['Datum_Upload'] == ut].copy() if not df_s.empty else pd.DataFrame()
                    if not match.empty:
                        # Spalten sicherstellen
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

                # Karte anzeigen...
                
                # DER SPEICHER BUTTON
                if st.button("💾 Änderungen speichern", key=f"sv_t_{ut}"):
                    apply_batch_updates(ut, n_note, edited_p, edited_s)
                    st.success("Gespeichert!")
                    st.rerun()
                
                if done == total and total > 0:
                    st.success("✅ Auftrag erledigt!")
                    if st.button("Archivieren", key=f"arc_{ut}"):
                        delete_entry_by_timestamp(ut); st.rerun()
