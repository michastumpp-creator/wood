import streamlit as st
from google import genai
from google.genai import types
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

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager (Lokal)", page_icon="🌲", layout="wide")
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
        with open("system_prompt.txt", "r", encoding="utf-8") as f: return f.read()
    except: return None 

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
    except: return {"polter": [], "staemme": []}

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

    # Polter
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

    # Stämme
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

def update_db_from_editor(edited_df, timestamp, type="polter"):
    """
    Sichere Speicherfunktion: Prüft erst, ob Spalte existiert (vermeidet KeyError).
    """
    db = load_db()
    changed = False
    
    for index, row in edited_df.iterrows():
        if type == "polter":
            for p in db['polter']:
                if p.get('Datum_Upload') == timestamp and str(p.get('Polter_Nr')) == str(row['Polter_Nr']):
                    # Sicherer Zugriff: Nur wenn Spalte im Editor war
                    if 'Geliefert' in row and p.get('Geliefert') != row['Geliefert']:
                        p['Geliefert'] = row['Geliefert']; changed = True
                    break
                    
        elif type == "staemme":
            for s in db['staemme']:
                if s.get('Datum_Upload') == timestamp and str(s.get('WNr')) == str(row['WNr']):
                    # Sicherer Zugriff für Geliefert
                    if 'Geliefert' in row and s.get('Geliefert') != row['Geliefert']:
                        s['Geliefert'] = row['Geliefert']; changed = True
                    # Sicherer Zugriff für Info
                    if 'Info' in row and s.get('Info') != row['Info']:
                        s['Info'] = str(row['Info']); changed = True
                    break
                    
    if changed: save_db(db)

def update_global_note(timestamp, new_note):
    db = load_db()
    for p in db['polter']:
        if p.get('Datum_Upload') == timestamp: p['Notiz'] = new_note
    save_db(db)

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
                prompt = load_prompt() or """
                Du bist ein KI-Assistent. Analysiere exakt.
                1. META: "Gesamtmenge", "Stämme gezählt", "Revier Ort", "Zertifikat".
                2. STÄMME: Tabelle lesen. "K" -> klammer: true.
                3. POLTER: GPS als String.
                """
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
                try: 
                    r = client.models.generate_content(model="gemini-3-flash-preview", contents=f"Check: {json.dumps(agg)}. Summe ok? Kurz.")
                    st.session_state.messages.append({"role": "assistant", "content": r.text})
                except: pass
                st.rerun()

    if st.session_state.analyzed_data:
        d = st.session_state.analyzed_data
        st.divider(); st.subheader("💬 KI-Assistent")
        for m in st.session_state.messages: st.chat_message(m["role"]).write(m["content"])
        if u := st.chat_input("Frage..."):
            st.session_state.messages.append({"role": "user", "content": u}); st.chat_message("user").write(u)
            with st.spinner("..."):
                r = client.models.generate_content(model="gemini-3-flash-preview", contents=f"Daten: {json.dumps(d)}\nFrage: {u}\nAntworte kurz.")
                st.session_state.messages.append({"role": "assistant", "content": r.text}); st.rerun()
        
        st.divider()
        stems, v_stems = d.get('staemme', []), [s for s in d.get('staemme', []) if not s.get('klammer')]
        ds, dc = to_float(d.get('meta', {}).get('dokument_summe', 0)), int(to_float(d.get('meta', {}).get('dokument_anzahl_staemme', 0)))
        ss = sum([to_float(s.get('fm', 0)) for s in stems])
        c1, c2, c3 = st.columns(3)
        c1.metric("Festmeter", f"{ds:.2f} / {ss:.2f}", delta=round(ss-ds, 2))
        c2.metric("Stückzahl", f"{dc} / {len(v_stems)}", delta=len(v_stems)-dc)
        c3.metric("Polter", len(d.get('polter', [])))
        
        with st.expander("PDF-Check"):
            if uploaded_files:
                tabs = st.tabs([f.name for f in uploaded_files])
                for i, t in enumerate(tabs):
                    with t:
                        if uploaded_files[i].type=="application/pdf":
                            imgs = create_highlighted_pdf_images(uploaded_files[i], 0, 0, d.get('polter', []))
                            if imgs: 
                                cols = st.columns(len(imgs))
                                for j, im in enumerate(imgs): 
                                    with cols[j]: st.image(im, caption=f"S.{j+1}", use_container_width=True)
                        else: st.image(uploaded_files[i], width=300)
        with st.expander("Daten"): st.dataframe(pd.DataFrame(stems))
        if st.button("💾 Speichern"):
            if save_to_json(d): st.success("Gespeichert!"); st.session_state.analyzed_data = None; st.info("Im Bestand."); st.rerun()

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
                icon = "🚛 " if is_trans else "🌲 "
                suffix = " (BEIM FUHRMANN)" if is_trans else ""
                note_val = grp['Notiz'].iloc[0] if 'Notiz' in grp.columns else ""
                
                with st.expander(f"{icon}{rev} | Los {los} | {grp['Menge_Fm'].sum():.2f} Fm{suffix}"):
                    # NOTIZ FELD
                    new_note = st.text_area("Notiz:", value=note_val, key=f"note_b_{ut}", height=68)
                    if new_note != note_val: update_global_note(ut, new_note)

                    c_act, c_cnt = st.columns([1, 4])
                    with c_act:
                        if not is_trans and st.button("Start Transport", key=f"mt_{ut}"): move_to_transport(ut); st.rerun()
                        if st.button("Löschen", key=f"dl_{ut}"): delete_entry_by_timestamp(ut); st.rerun()
                    
                    c1, c2 = st.columns([1, 1])
                    c1.markdown("**Polter**"); c1.dataframe(grp[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                    
                    with c2:
                        match = df_s[df_s['Datum_Upload'] == ut].copy()
                        if not match.empty:
                            st.markdown("**Stämme (Info editierbar)**")
                            # Editor für Info (Geliefert ist hier unsichtbar, erzeugt also keinen Fehler)
                            cols_show = ['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Info']
                            edited_stems = st.data_editor(
                                match[cols_show], 
                                key=f"ed_st_b_{ut}", 
                                hide_index=True,
                                column_config={"Info": st.column_config.TextColumn("Info", width="small")}
                            )
                            update_db_from_editor(edited_stems, ut, "staemme")

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
            done = len(grp[grp['Geliefert']]); total = len(grp)
            note_val = grp['Notiz'].iloc[0] if 'Notiz' in grp.columns else ""
            
            with st.expander(f"🚛 {rev} | Los {los} | {done}/{total} Polter fertig"):
                st.progress(done/total if total>0 else 0)
                
                st.info(f"📋 **Notiz:** {note_val}")
                n_note = st.text_area("Update Notiz:", value=note_val, key=f"note_t_{ut}")
                if n_note != note_val: update_global_note(ut, n_note); st.rerun()

                c1, c2 = st.columns([1, 1])
                with c1:
                    st.markdown("### 1. Polter Abhaken")
                    edit_p = st.data_editor(
                        grp[['Polter_Nr', 'Menge_Fm', 'Geliefert']],
                        column_config={"Geliefert": st.column_config.CheckboxColumn("Fertig?", default=False)},
                        hide_index=True, key=f"ed_p_t_{ut}"
                    )
                    update_db_from_editor(edit_p, ut, "polter")
                
                with c2:
                    st.markdown("### 2. Einzelstämme")
                    match = df_s[df_s['Datum_Upload'] == ut].copy()
                    if not match.empty:
                        # Hier ist 'Geliefert' sichtbar, also darf es geupdated werden
                        edit_s = st.data_editor(
                            match[['WNr', 'Holzart', 'Volumen_Fm', 'Geliefert', 'Info']],
                            column_config={
                                "Geliefert": st.column_config.CheckboxColumn("Geliefert", default=False),
                                "Info": st.column_config.TextColumn("Info")
                            },
                            hide_index=True, key=f"ed_s_t_{ut}"
                        )
                        update_db_from_editor(edit_s, ut, "staemme")
                    else: st.caption("Keine Einzelstämme.")

                # Karte
                v_pts = []
                for _, r in grp.iterrows():
                    la, lo = parse_gps_for_map(r.get('Lat','')), parse_gps_for_map(r.get('Lon',''))
                    if la > 0: v_pts.append({"lat": la, "lon": lo, "c": "gray" if r['Geliefert'] else "red"})
                if v_pts:
                    m = folium.Map([pd.DataFrame(v_pts).lat.mean(), pd.DataFrame(v_pts).lon.mean()], zoom_start=13)
                    for p in v_pts: folium.Marker([p['lat'], p['lon']], icon=folium.Icon(color=p['c'], icon="truck", prefix='fa')).add_to(m)
                    st_folium(m, width="100%", height=250, key=f"mp_t_{ut}")
                
                if done == total and total > 0:
                    st.success("Auftrag (Polter) vollständig!")
                    if st.button("Archivieren", key=f"arc_{ut}"): delete_entry_by_timestamp(ut); st.rerun()
