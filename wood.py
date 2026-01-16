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
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None

# --- HELFER: INPUT VERSTEHEN ---
def to_float(val):
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    if isinstance(val, str):
        try:
            return float(val.replace(',', '.'))
        except:
            return 0.0
    return 0.0

# --- HELFER: KOORDINATEN FÜR KARTE ---
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
    uploaded_file.seek(0)
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
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

    for page_num, page in enumerate(doc):
        if page_num > 2: break
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

# --- GOOGLE SHEETS VERBINDUNG ---
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
        st.error(f"Fehler beim Öffnen der Tabelle: {e}")
        return None

# --- DATEN SPEICHERN ---
def save_to_sheets(data):
    sh = get_spreadsheet()
    if not sh: return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if isinstance(data, list): data = {"polter": data, "meta": {}, "staemme": []}

    meta = data.get('meta', {})
    los = str(meta.get('los', 'Unbekannt'))
    revier = str(meta.get('revier', 'Unbekannt'))
    waldort = str(meta.get('waldort', '')) # Neuer Wert
    datum_aufnahme = str(meta.get('datum', timestamp.split(' ')[0]))

    def fmt(val): 
        if val is None: return ""
        return str(val).replace(',', '.')

    # 1. POLTER
    try: 
        ws_polter = sh.worksheet("Polter_Uebersicht")
        # Header checken/erweitern für Waldort
        headers = ws_polter.row_values(1)
        if "Waldort" not in headers:
            ws_polter.update_cell(1, len(headers)+1, "Waldort")
    except: 
        ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=11)
        ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link", "Waldort"])

    polter_rows = []
    for p in data.get('polter', []):
        lat_text = str(p.get('lat', ''))
        lon_text = str(p.get('lon', ''))
        try:
            l_lat = parse_gps_for_map(lat_text)
            l_lon = parse_gps_for_map(lon_text)
            link = f"http://maps.google.com/?q={l_lat},{l_lon}"
        except: link = ""
        
        # Reihenfolge muss zum Header passen. Waldort ist jetzt ganz hinten oder Spalte 10/11.
        # Wir hängen Waldort einfach hinten dran, wenn die Tabelle Standard ist.
        polter_rows.append([
            timestamp, datum_aufnahme, los, revier, p.get('nr'), 
            fmt(p.get('fm')), lat_text, lon_text, link, waldort
        ])
    if polter_rows: ws_polter.append_rows(polter_rows, value_input_option='USER_ENTERED')

    # 2. STÄMME
    stems = data.get('staemme', [])
    if stems:
        try: ws_stamm = sh.worksheet("Einzelstaemme")
        except: ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10); ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])

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

# --- DATEN LÖSCHEN ---
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

# --- DATEN LADEN ---
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

    uploaded_file = st.file_uploader("Holzliste hochladen (PDF)", type=["pdf", "jpg", "png"])

    if uploaded_file:
        if st.session_state.last_upload != uploaded_file.name:
            st.session_state.analyzed_data = None
            st.session_state.last_upload = uploaded_file.name

        content = None
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF: {uploaded_file.name}")
            content = types.Part.from_bytes(data=uploaded_file.getvalue(), mime_type="application/pdf")
        else:
            img = Image.open(uploaded_file)
            st.image(img, width=400)
            content = img

        if st.session_state.analyzed_data is None:
            if st.button("🚀 Analysieren (Gemini 3 Preview)"):
                with st.spinner("Analyse läuft..."):
                    try:
                        prompt = """
                        Du bist ein KI-Assistent für deutsche Forstwirtschaft.
                        
                        1. METADATEN:
                        Suche "Gesamtmenge" (Fm), "Stämme gezählt".
                        Suche auch den "Waldort" oder "Distrikt" (z.B. "Scheiterwald" oder "Kalkofen").
                        
                        2. EINZELSTÄMME:
                        Tabelle "ZUSAMMENSTELLUNG NACH WALDNUMMERN". Spalten: WNr, Lä, DoR, FmoR.
                        Wenn Spalte "K" vorhanden oder "K" bei Nummer steht -> klammer: true.
                        
                        3. POLTER & GPS:
                        Suche Polter-Listen mit GPS. Extrahiere den GPS-String exakt (z.B. "48°17'06,71").
                        
                        --- JSON STRUKTUR ---
                        {
                            "meta": {
                                "los": "String", "revier": "String", "waldort": "String", "datum": "String", 
                                "dokument_summe": Float, "dokument_anzahl_staemme": Int
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
                        data = json.loads(clean)
                        st.session_state.analyzed_data = data
                        st.rerun()
                    except Exception as e: st.error(f"Fehler: {e}")

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        st.divider()
        st.subheader("🕵️ Prüfung")
        
        doc_sum = to_float(data.get('meta', {}).get('dokument_summe', 0))
        doc_count = int(to_float(data.get('meta', {}).get('dokument_anzahl_staemme', 0)))
        
        all_stems = data.get('staemme', [])
        valid_stems = [s for s in all_stems if not s.get('klammer', False)]
        stamm_sum = sum([to_float(s.get('fm', 0)) for s in all_stems]) # Summe inkl. K
        
        if uploaded_file.type == "application/pdf":
            with st.expander("📄 PDF-Check (Visuell)", expanded=True):
                try:
                    marked_images = create_highlighted_pdf_images(uploaded_file, doc_sum, doc_count, data.get('polter', []))
                    cols = st.columns(len(marked_images))
                    for idx, img in enumerate(marked_images):
                        with cols[idx]: st.image(img, caption=f"Seite {idx+1}", use_container_width=True)
                except: pass

        c1, c2, c3 = st.columns(3)
        c1.metric("Festmeter (Soll/Ist)", f"{doc_sum} / {stamm_sum:.2f}", delta=round(stamm_sum-doc_sum, 2))
        c2.metric("Stückzahl (Ohne K)", f"{doc_count} / {len(valid_stems)}", delta=len(valid_stems)-doc_count)
        c3.metric("Polter", len(data.get('polter', [])))

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
        # --- COCKPIT: KARTE & STATISTIK ---
        st.subheader("📊 Gesamtübersicht")
        col_stats, col_map = st.columns([1, 2])
        
        with col_stats:
            if not df_staemme.empty:
                st.markdown("**Festmeter nach Baumart**")
                # Summiere Volumen pro Holzart (inkl. K, wie gewünscht)
                stats = df_staemme.groupby('Holzart')['Volumen_Fm'].sum().sort_values(ascending=False)
                st.dataframe(stats, height=250)
                st.caption(f"Gesamt: {stats.sum():.2f} Fm")
            else:
                st.info("Keine Stammdaten.")

        with col_map:
            st.markdown("**🗺️ Alle Lagerorte**")
            all_pts = []
            for _, row in df_polter.iterrows():
                lat = parse_gps_for_map(row.get('Lat', ''))
                lon = parse_gps_for_map(row.get('Lon', ''))
                if lat > 0:
                    info = f"Los {row['Los_Nr']} | P{row['Polter_Nr']}"
                    all_pts.append({"lat": lat, "lon": lon, "info": info})
            
            if all_pts:
                map_df = pd.DataFrame(all_pts)
                m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=10)
                for _, pt in map_df.iterrows():
                    folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="blue", icon="tree", prefix='fa')).add_to(m)
                st_folium(m, width="100%", height=300, key="global_map")
            else:
                st.warning("Keine gültigen GPS-Daten gefunden.")

        st.divider()
        st.subheader("📂 Akten")
        
        if 'Los_Nr' in df_polter.columns:
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                # Metadaten holen
                polter_sum = group['Menge_Fm'].sum()
                waldort = group['Waldort'].iloc[0] if 'Waldort' in group.columns else ""
                if str(waldort) == "nan": waldort = ""
                
                ort_label = f" ({waldort})" if waldort else ""
                
                # Stämme für dieses Los holen
                match = pd.DataFrame()
                stem_summary = ""
                
                if not df_staemme.empty:
                    match = df_staemme[(df_staemme['Los_Nr'].astype(str) == str(los))]
                    if not match.empty:
                        # Baumarten zählen (OHNE K für die Anzeige im Titel)
                        # Wir filtern hier 'K' aus der WNr raus, falls wir es so gespeichert haben "(K)"
                        # Oder wir filtern einfach alles raus was "(K)" im Namen hat
                        non_k_match = match[~match['WNr'].astype(str).str.contains(r'\(K\)', na=False)]
                        
                        counts = non_k_match['Holzart'].value_counts()
                        # Format "Bu: 12, Fi: 5"
                        summary_parts = [f"{art}: {count}" for art, count in counts.items()]
                        stem_summary = " | 🌳 " + ", ".join(summary_parts)

                title = f"🌲 {revier}{ort_label} | Los {los} | 📅 {datum} | 📦 {polter_sum:.2f} Fm {stem_summary}"
                
                with st.expander(title):
                    col_del, col_info = st.columns([1, 4])
                    with col_del:
                        if st.button(f"🗑️ Liste Löschen", key=f"del_{revier}_{los}_{datum}"):
                            with st.spinner("Lösche..."):
                                if delete_entry(los, revier, datum):
                                    st.success("Gelöscht!")
                                    st.cache_data.clear()
                                    st.rerun()

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        st.markdown("**Polter & GPS**")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                        
                        # Mini-Karte
                        valid_pts = []
                        for _, row in group.iterrows():
                            lat = parse_gps_for_map(row.get('Lat', ''))
                            lon = parse_gps_for_map(row.get('Lon', ''))
                            if lat > 0: valid_pts.append({"lat": lat, "lon": lon, "info": f"P{row['Polter_Nr']}"})
                        
                        if valid_pts:
                            map_df = pd.DataFrame(valid_pts)
                            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=13)
                            for _, pt in map_df.iterrows():
                                folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
                            st_folium(m, width="100%", height=250, key=f"map_{revier}_{los}_{datum}")

                    with c2:
                        if not match.empty:
                            st.markdown(f"**Einzelstämme ({len(match)} inkl. K):**")
                            st.dataframe(match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                        else:
                            st.write("Keine Einzelstämme.")
