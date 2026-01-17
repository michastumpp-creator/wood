import streamlit as st
from google import genai
from google.genai import types
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
from PIL import Image
from google.oauth2 import service_account
import gspread
from datetime import datetime
import fitz  # PyMuPDF
import io
import re

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload_count' not in st.session_state:
    st.session_state.last_upload_count = 0
if 'messages' not in st.session_state:
    st.session_state.messages = []

# --- HELFER: INPUT VERSTEHEN ---
def to_float(val):
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    if isinstance(val, str):
        try: return float(val.replace(',', '.'))
        except: return 0.0
    return 0.0

# --- HELFER: GPS ---
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

# --- HELFER: PDF MARKIEREN ---
def create_highlighted_pdf_images(uploaded_file, text_summe, text_anzahl, polter_liste):
    # Da wir die Datei mehrfach lesen, müssen wir den Pointer resetten
    uploaded_file.seek(0)
    try:
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    except: return [] # Falls kein PDF (z.B. Bild)
    
    images = []
    search_terms = []
    
    if text_summe > 0:
        val_str = str(text_summe)
        search_terms.append({"val": val_str, "color": (1, 1, 0)}) 
        search_terms.append({"val": val_str.replace('.', ','), "color": (1, 1, 0)}) 
    
    if text_anzahl > 0:
        search_terms.append({"val": str(int(text_anzahl)), "color": (0, 1, 1)}) 

    for p in polter_liste:
        raw_lat = p.get('lat', '')
        raw_lon = p.get('lon', '')
        if raw_lat: search_terms.append({"val": str(raw_lat), "color": (0, 1, 0)})
        if raw_lon: search_terms.append({"val": str(raw_lon), "color": (0, 1, 0)})

    # Max 2 Seiten pro Datei visualisieren
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

# --- GOOGLE SHEETS ---
def get_spreadsheet():
    if "gcp_service_account" not in st.secrets:
        st.error("Secrets fehlen!")
        return None
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        client = gspread.authorize(creds)
        return client.open("Forst_Datenbank")
    except Exception as e:
        st.error(f"Fehler: {e}")
        return None

def save_to_sheets(data):
    sh = get_spreadsheet()
    if not sh: return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Meta nehmen wir vom "Haupt"-Datensatz (Sammel-Objekt)
    meta = data.get('meta', {})
    los = str(meta.get('los', 'Unbekannt'))
    revier = str(meta.get('revier', 'Unbekannt'))
    ort = str(meta.get('revier_ort', ''))
    zert = str(meta.get('zertifikat', ''))
    datum_aufnahme = str(meta.get('datum', timestamp.split(' ')[0]))

    def fmt(val): return str(val).replace(',', '.') if val is not None else ""

    # 1. POLTER
    ws_polter = None
    try:
        ws_polter = sh.worksheet("Polter_Uebersicht")
    except:
        try:
            ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=15)
            ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link", "Ort", "Zertifikat"])
        except: return False

    try:
        headers = ws_polter.row_values(1)
        if "Ort" not in headers: ws_polter.update_cell(1, len(headers)+1, "Ort")
        headers = ws_polter.row_values(1) 
        if "Zertifikat" not in headers: ws_polter.update_cell(1, len(headers)+1, "Zertifikat")
    except: pass

    polter_rows = []
    for p in data.get('polter', []):
        lat_text = str(p.get('lat', ''))
        lon_text = str(p.get('lon', ''))
        try:
            l_lat = parse_gps_for_map(lat_text)
            l_lon = parse_gps_for_map(lon_text)
            link = f"http://maps.google.com/?q={l_lat},{l_lon}"
        except: link = ""
        
        polter_rows.append([
            timestamp, datum_aufnahme, los, revier, p.get('nr'), 
            fmt(p.get('fm')), lat_text, lon_text, link, ort, zert
        ])
    if polter_rows: ws_polter.append_rows(polter_rows, value_input_option='USER_ENTERED')

    # 2. STÄMME
    stems = data.get('staemme', [])
    if stems:
        ws_stamm = None
        try:
            ws_stamm = sh.worksheet("Einzelstaemme")
        except:
            try:
                ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10)
                ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])
            except: return False

        stamm_rows = []
        for s in stems:
            wnr = s.get('wnr', '')
            if s.get('klammer'): wnr = f"{wnr} (K)"
            stamm_rows.append([
                timestamp, los, revier, wnr, s.get('art', ''), 
                fmt(s.get('l')), fmt(s.get('d')), s.get('klasse', ''), fmt(s.get('fm'))
            ])
        if stamm_rows: ws_stamm.append_rows(stamm_rows, value_input_option='USER_ENTERED')

    return True

def delete_entry(los, revier, datum_aufnahme):
    sh = get_spreadsheet()
    if not sh: return False
    try:
        ws_p = sh.worksheet("Polter_Uebersicht")
        data_p = ws_p.get_all_records()
        df_p = pd.DataFrame(data_p)
        mask_p = (df_p['Los_Nr'].astype(str) == str(los)) & (df_p['Revier'].astype(str) == str(revier)) & (df_p['Datum_Aufnahme'].astype(str) == str(datum_aufnahme))
        df_p_clean = df_p[~mask_p]
        ws_p.clear()
        ws_p.update([df_p_clean.columns.values.tolist()] + df_p_clean.values.tolist())

        try:
            ws_s = sh.worksheet("Einzelstaemme")
            data_s = ws_s.get_all_records()
            df_s = pd.DataFrame(data_s)
            mask_s = (df_s['Los_Nr'].astype(str) == str(los)) & (df_s['Revier'].astype(str) == str(revier))
            df_s_clean = df_s[~mask_s]
            ws_s.clear()
            ws_s.update([df_s_clean.columns.values.tolist()] + df_s_clean.values.tolist())
        except: pass
        return True
    except: return False

def load_data_frames():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame(), pd.DataFrame()
    try:
        data_p = sh.worksheet("Polter_Uebersicht").get_all_records()
        df_polter = pd.DataFrame(data_p)
        if not df_polter.empty and 'Menge_Fm' in df_polter.columns:
            df_polter['Menge_Fm'] = df_polter['Menge_Fm'].apply(to_float)
    except: df_polter = pd.DataFrame()
    try:
        data_s = sh.worksheet("Einzelstaemme").get_all_records()
        df_staemme = pd.DataFrame(data_s)
        if not df_staemme.empty and 'Volumen_Fm' in df_staemme.columns:
            df_staemme['Volumen_Fm'] = df_staemme['Volumen_Fm'].apply(to_float)
    except: df_staemme = pd.DataFrame()
    return df_polter, df_staemme

# --- APP START ---
st.title("🌲 Forst-Verwaltung")

tab1, tab2 = st.tabs(["📸 Scan & Analyse", "🗃️ Bestand"])

# --- TAB 1: SCANNER ---
with tab1:
    try: client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except: st.stop()

    # MULTI-FILE UPLOAD
    uploaded_files = st.file_uploader("Holzlisten hochladen (Mehrere möglich)", type=["pdf", "jpg", "png"], accept_multiple_files=True)

    if uploaded_files:
        # Reset Logic wenn neue Dateien kommen
        if st.session_state.last_upload_count != len(uploaded_files):
            st.session_state.analyzed_data = None
            st.session_state.messages = []
            st.session_state.last_upload_count = len(uploaded_files)

        if st.session_state.analyzed_data is None:
            if st.button(f"🚀 {len(uploaded_files)} Dateien Analysieren"):
                
                # Container für gesammelte Daten
                aggregated_data = {
                    "meta": {}, 
                    "polter": [], 
                    "staemme": [],
                    # Wir speichern hier temporäre Summen für den Soll-Vergleich
                    "total_soll_summe": 0.0,
                    "total_soll_anzahl": 0.0
                }
                
                progress_bar = st.progress(0)
                
                for idx, uploaded_file in enumerate(uploaded_files):
                    with st.spinner(f"Analysiere Datei {idx+1}/{len(uploaded_files)}: {uploaded_file.name}..."):
                        
                        # Inhalt vorbereiten
                        content = None
                        uploaded_file.seek(0) # Sicherstellen, dass wir am Anfang sind
                        if uploaded_file.type == "application/pdf":
                            content = types.Part.from_bytes(data=uploaded_file.read(), mime_type="application/pdf")
                        else:
                            img = Image.open(uploaded_file)
                            content = img

                        try:
                            prompt = """
                            Du bist ein KI-Assistent für Forstwirtschaft. Analysiere das Dokument exakt.
                            
                            1. METADATEN:
                            - "Gesamtmenge" (Fm), "Stämme gezählt" (auf DIESER Seite/Datei).
                            - "Revier Ort": Suche die Adresse des Reviers. Extrahiere NUR den Ortsnamen neben der PLZ (z.B. "Inneringen").
                            - "Zertifikat": Suche nach "FSC", "PEFC".
                            - "Los", "Revier", "Datum".
                            
                            2. EINZELSTÄMME:
                            Tabelle "ZUSAMMENSTELLUNG NACH WALDNUMMERN". Spalten: WNr, Lä, DoR, FmoR.
                            Wenn "K" Spalte/Markierung -> klammer: true.
                            
                            3. POLTER & GPS:
                            Suche Polter-Listen mit GPS. Extrahiere den String exakt (z.B. "48°17'06,71").
                            
                            --- JSON STRUKTUR ---
                            {
                                "meta": {
                                    "los": "String", "revier": "String", "revier_ort": "String", "zertifikat": "String",
                                    "datum": "String", "dokument_summe": Float, "dokument_anzahl_staemme": Int
                                },
                                "polter": [{"nr": Int, "fm": Float, "lat": "String", "lon": "String"}],
                                "staemme": [{"wnr": "String", "klammer": Boolean, "art": "String", "l": Float, "d": Float, "klasse": "String", "fm": Float}]
                            }
                            """
                            response = client.models.generate_content(
                                model="gemini-3-flash-preview", 
                                contents=[prompt, content],
                                config=types.GenerateContentConfig(response_mime_type="application/json")
                            )
                            clean = response.text.replace("```json", "").replace("```", "").strip()
                            single_file_data = json.loads(clean)
                            
                            # DATEN ZUSAMMENFÜHREN
                            
                            # Meta: Wir nehmen die Meta-Daten vom ersten File, updaten aber Datum/Ort falls später besser gefunden
                            if not aggregated_data["meta"]:
                                aggregated_data["meta"] = single_file_data.get("meta", {})
                            else:
                                # Falls spätere Files bessere Infos haben (z.B. Zertifikat war auf Seite 1 nicht da)
                                new_meta = single_file_data.get("meta", {})
                                if not aggregated_data["meta"].get("zertifikat") and new_meta.get("zertifikat"):
                                    aggregated_data["meta"]["zertifikat"] = new_meta["zertifikat"]
                            
                            # Summen addieren (für den Check)
                            aggregated_data["total_soll_summe"] += to_float(single_file_data.get("meta", {}).get("dokument_summe", 0))
                            aggregated_data["total_soll_anzahl"] += to_float(single_file_data.get("meta", {}).get("dokument_anzahl_staemme", 0))
                            
                            # Listen erweitern
                            aggregated_data["polter"].extend(single_file_data.get("polter", []))
                            aggregated_data["staemme"].extend(single_file_data.get("staemme", []))
                            
                        except Exception as e:
                            st.error(f"Fehler bei Datei {uploaded_file.name}: {e}")
                    
                    progress_bar.progress((idx + 1) / len(uploaded_files))

                # Die berechneten Gesamtsummen in das Meta-Objekt schreiben, damit die Anzeige stimmt
                aggregated_data["meta"]["dokument_summe"] = aggregated_data["total_soll_summe"]
                aggregated_data["meta"]["dokument_anzahl_staemme"] = aggregated_data["total_soll_anzahl"]
                
                st.session_state.analyzed_data = aggregated_data
                
                # --- KI CHECK NACH DEM MERGE ---
                check_prompt = f"""
                Ich habe {len(uploaded_files)} Dateien analysiert und zusammengefügt.
                Hier ist das Gesamtergebnis: {json.dumps(aggregated_data)}
                
                Prüfe kurz:
                1. Passt die Summe der Einzelstämme zur addierten "dokument_summe" ({aggregated_data['total_soll_summe']})?
                2. Gibt es Auffälligkeiten?
                Antworte kurz und direkt.
                """
                try:
                    check_resp = client.models.generate_content(model="gemini-3-flash-preview", contents=check_prompt)
                    st.session_state.messages.append({"role": "assistant", "content": check_resp.text})
                except: pass
                
                st.rerun()

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        # --- CHAT UI ---
        st.divider()
        st.subheader("💬 KI-Assistent")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
        
        if user_input := st.chat_input("Frage etwas zum Ergebnis..."):
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"): st.write(user_input)
            with st.spinner("..."):
                chat_prompt = f"Daten: {json.dumps(data)}\nFrage: {user_input}\nAntworte kurz."
                response = client.models.generate_content(model="gemini-3-flash-preview", contents=chat_prompt)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                st.rerun()
        
        # --- DATA DISPLAY ---
        st.divider()
        
        all_stems = data.get('staemme', [])
        valid_stems = [s for s in all_stems if not s.get('klammer', False)]
        
        # Soll-Werte (wurden oben beim Scannen schon addiert)
        doc_sum = to_float(data.get('meta', {}).get('dokument_summe', 0))
        doc_count = int(to_float(data.get('meta', {}).get('dokument_anzahl_staemme', 0)))
        
        stamm_sum = sum([to_float(s.get('fm', 0)) for s in all_stems]) 
        
        meta = data.get('meta', {})
        zertifikat = meta.get('zertifikat', '')
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Festmeter (Soll/Ist)", f"{doc_sum:.2f} / {stamm_sum:.2f}", delta=round(stamm_sum-doc_sum, 2))
        c2.metric("Stückzahl (Ohne K)", f"{doc_count} / {len(valid_stems)}", delta=len(valid_stems)-doc_count)
        
        z_label = f"Polter ({zertifikat})" if zertifikat else "Polter"
        c3.metric(z_label, len(data.get('polter', [])))

        # --- VISUELLE PRÜFUNG (FÜR JEDE DATEI) ---
        with st.expander("📄 PDF-Check (Visuell - Alle Dateien)"):
            if uploaded_files:
                # Wir iterieren nochmal über die Files für die Anzeige
                file_tabs = st.tabs([f.name for f in uploaded_files])
                for idx, tab in enumerate(file_tabs):
                    with tab:
                        f = uploaded_files[idx]
                        if f.type == "application/pdf":
                            try:
                                # Wir übergeben hier keine "Soll"-Werte pro Datei, da wir nur die Gesamtsumme im Data-Objekt haben.
                                # Aber wir können die Koordinaten markieren, wenn wir wissen, welche zu welcher Datei gehören.
                                # Vereinfachung: Wir suchen einfach alle gefundenen Koordinaten in allen PDFs.
                                marked_images = create_highlighted_pdf_images(f, 0, 0, data.get('polter', []))
                                
                                if marked_images:
                                    cols = st.columns(len(marked_images))
                                    for i, img in enumerate(marked_images):
                                        with cols[i]: st.image(img, caption=f"Seite {i+1}", use_container_width=True)
                                else:
                                    st.info("Keine relevanten Markierungen auf den ersten Seiten gefunden.")
                            except Exception as e:
                                st.warning(f"Vorschau nicht möglich: {e}")
                        else:
                            st.image(f, width=300)

        with st.expander("Details Tabelle"):
            st.dataframe(pd.DataFrame(all_stems))

        if st.button("💾 Speichern"):
            with st.spinner("Speichere..."):
                if save_to_sheets(data):
                    st.success("Gespeichert!")
                    st.session_state.analyzed_data = None
                    st.info("Daten sind im Bestand.")

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    if df_polter.empty:
        st.info("Keine Daten.")
    else:
        st.subheader("📊 Übersicht")
        c_stats, c_map = st.columns([1, 1]) 
        
        with c_stats:
            if not df_staemme.empty:
                stats = df_staemme.groupby('Holzart')['Volumen_Fm'].sum().sort_values(ascending=False)
                st.dataframe(stats, height=200, use_container_width=True)
                st.caption(f"**Gesamt: {stats.sum():.2f} Fm**")
            else: st.info("Leer")

        with c_map:
            all_pts = []
            for _, row in df_polter.iterrows():
                lat = parse_gps_for_map(row.get('Lat', ''))
                lon = parse_gps_for_map(row.get('Lon', ''))
                if lat > 0:
                    info = f"{row.get('Ort','')} | Los {row['Los_Nr']}"
                    all_pts.append({"lat": lat, "lon": lon, "info": info})
            
            if all_pts:
                map_df = pd.DataFrame(all_pts)
                m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=9)
                for _, pt in map_df.iterrows():
                    folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="blue", icon="tree", prefix='fa')).add_to(m)
                st_folium(m, width="100%", height=200, key="global_map_compact")
            else:
                st.caption("Keine GPS Daten.")

        st.divider()
        st.subheader("📂 Akten")
        
        if 'Los_Nr' in df_polter.columns:
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                polter_sum = group['Menge_Fm'].sum()
                ort_val = group['Ort'].iloc[0] if 'Ort' in group.columns else ""
                ort_label = f" ({ort_val})" if ort_val and str(ort_val) != "nan" else ""
                zert_val = group['Zertifikat'].iloc[0] if 'Zertifikat' in group.columns else ""
                zert_label = f" [{zert_val}]" if zert_val and str(zert_val) != "nan" else ""

                stem_summary = ""
                stamm_anzahl = 0
                match = pd.DataFrame()
                
                if not df_staemme.empty:
                    match = df_staemme[(df_staemme['Los_Nr'].astype(str) == str(los))]
                    if not match.empty:
                        non_k = match[~match['WNr'].astype(str).str.contains(r'\(K\)', na=False)]
                        stamm_anzahl = len(non_k)
                        counts = non_k['Holzart'].value_counts().head(3)
                        summary_parts = [f"{art}: {c}" for art, c in counts.items()]
                        stem_summary = " | " + ", ".join(summary_parts)

                title = f"🌲 {revier}{ort_label}{zert_label} | Los {los} | 📅 {datum} | 📦 {polter_sum:.2f} Fm | 🪵 {stamm_anzahl} Stk{stem_summary}"
                
                with st.expander(title):
                    col_del, col_info = st.columns([1, 4])
                    with col_del:
                        if st.button(f"🗑️", key=f"del_{revier}_{los}_{datum}"):
                            with st.spinner("..."):
                                if delete_entry(los, revier, datum):
                                    st.success("Weg!")
                                    st.cache_data.clear()
                                    st.rerun()

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        st.markdown("**Polter**")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                        v_pts = []
                        for _, row in group.iterrows():
                            lat = parse_gps_for_map(row.get('Lat', ''))
                            lon = parse_gps_for_map(row.get('Lon', ''))
                            if lat > 0: v_pts.append({"lat": lat, "lon": lon, "info": f"P{row['Polter_Nr']}"})
                        if v_pts:
                            m_df = pd.DataFrame(v_pts)
                            m = folium.Map(location=[m_df.lat.mean(), m_df.lon.mean()], zoom_start=13)
                            for _, pt in m_df.iterrows():
                                folium.Marker([pt['lat'], pt['lon']], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
                            st_folium(m, width="100%", height=150, key=f"map_{los}")

                    with c2:
                        if not match.empty:
                            st.markdown(f"**Einzelstämme**")
                            st.dataframe(match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                        else:
                            st.caption("Keine Stämme.")
