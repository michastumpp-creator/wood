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

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None

# --- HELFER: INPUT VERSTEHEN (Für Berechnungen im RAM) ---
def to_float(val):
    """
    Versucht, einen Wert für Python-Berechnungen (Summen) lesbar zu machen.
    Ändert aber NICHTS am Wert selbst (kein Teilen durch 100).
    """
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    if isinstance(val, str):
        try:
            # Komma zu Punkt für Python-Interna
            return float(val.replace(',', '.'))
        except:
            return 0.0
    return 0.0

# --- HELFER: PDF MARKIEREN ---
def create_highlighted_pdf_images(uploaded_file, text_summe, text_anzahl):
    uploaded_file.seek(0)
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    images = []
    
    search_terms = []
    # Wir suchen nach der Zahl so wie sie ist, und einmal mit Komma/Punkt getauscht
    if text_summe > 0:
        val_str = str(text_summe)
        search_terms.append({"val": val_str, "color": (1, 1, 0)}) 
        search_terms.append({"val": val_str.replace('.', ','), "color": (1, 1, 0)}) 
    
    if text_anzahl > 0:
        search_terms.append({"val": str(int(text_anzahl)), "color": (0, 1, 1)}) 

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

# --- DATEN SPEICHERN (PUNKT ERZWINGEN) ---
def save_to_sheets(data):
    sh = get_spreadsheet()
    if not sh: return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if isinstance(data, list): data = {"polter": data, "meta": {}, "staemme": []}

    meta = data.get('meta', {})
    los = str(meta.get('los', 'Unbekannt'))
    revier = str(meta.get('revier', 'Unbekannt'))
    datum_aufnahme = str(meta.get('datum', timestamp.split(' ')[0]))

    # FORMATIERUNG: ALLES MIT PUNKT (.)
    def fmt(val): 
        if val is None: return ""
        # 1. In String wandeln
        s = str(val)
        # 2. Komma durch Punkt ersetzen
        return s.replace(',', '.')

    # BLATT 1: POLTER
    try: ws_polter = sh.worksheet("Polter_Uebersicht")
    except: ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=10); ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link"])

    polter_rows = []
    for p in data.get('polter', []):
        lat = p.get('lat', 0)
        lon = p.get('lon', 0)
        
        # Link bauen (braucht Punkt, haben wir ja jetzt)
        # Wir nehmen die Werte so wie fmt sie ausgibt
        lat_clean = fmt(lat)
        lon_clean = fmt(lon)
        
        link = f"http://maps.google.com/?q={lat_clean},{lon_clean}"
        
        polter_rows.append([
            timestamp, datum_aufnahme, los, revier, p.get('nr'), 
            fmt(p.get('fm')), 
            lat_clean, 
            lon_clean, 
            link
        ])
    if polter_rows: ws_polter.append_rows(polter_rows, value_input_option='USER_ENTERED')

    # BLATT 2: EINZELSTÄMME
    staemme_data = data.get('staemme', [])
    if staemme_data:
        try: ws_stamm = sh.worksheet("Einzelstaemme")
        except: ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10); ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])

        stamm_rows = []
        for s in staemme_data:
            stamm_rows.append([
                timestamp, los, revier, s.get('wnr', ''), s.get('art', ''), 
                fmt(s.get('l')), 
                fmt(s.get('d')), 
                s.get('klasse', ''), 
                fmt(s.get('fm'))
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
    except Exception as e:
        st.error(f"Fehler: {e}")
        return False

# --- DATEN LADEN ---
def load_data_frames():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame(), pd.DataFrame()
    
    try:
        data_p = sh.worksheet("Polter_Uebersicht").get_all_records()
        df_polter = pd.DataFrame(data_p)
        # Wir wandeln alles in Floats für die Anzeige/Karte
        numeric_cols = ['Menge_Fm', 'Lat', 'Lon']
        for c in numeric_cols:
            if c in df_polter.columns: df_polter[c] = df_polter[c].apply(to_float)
    except: df_polter = pd.DataFrame()

    try:
        data_s = sh.worksheet("Einzelstaemme").get_all_records()
        df_staemme = pd.DataFrame(data_s)
        numeric_cols = ['Volumen_Fm', 'Laenge', 'Durchmesser']
        for c in numeric_cols:
            if c in df_staemme.columns: df_staemme[c] = df_staemme[c].apply(to_float)
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
                        # --- PROMPT ---
                        prompt = """
                        Du bist ein KI-Assistent für deutsche Forstwirtschaft. Analysiere dieses Dokument exakt.
                        
                        1. METADATEN:
                        Suche "Gesamtmenge" (Fm) und "Stämme gezählt".
                        
                        2. EINZELSTÄMME:
                        Tabelle "ZUSAMMENSTELLUNG NACH WALDNUMMERN". ZWEISPALTIG (Links & Rechts).
                        Spalten: "WNr", "Lä", "DoR", "FmoR".
                        
                        3. POLTER & GPS:
                        Suche Polter-Listen mit GPS.
                        
                        WICHTIGSTE REGEL: 
                        Speichere Zahlen so, wie sie mathematisch korrekt sind (1,37 Fm -> 1.37).
                        Nutze PUNKT als Dezimaltrenner im JSON.
                        
                        --- JSON STRUKTUR ---
                        {
                            "meta": {
                                "los": "String", "revier": "String", "datum": "String", 
                                "dokument_summe": Float, 
                                "dokument_anzahl_staemme": Int
                            },
                            "polter": [{"nr": Int, "fm": Float, "lat": Float, "lon": Float}],
                            "staemme": [{"wnr": "String", "art": "String", "l": Float, "d": Float, "klasse": "String", "fm": Float}]
                        }
                        """
                        response = client.models.generate_content(
                            model="gemini-3-flash-preview", 
                            contents=[prompt, content],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        clean = response.text.replace("```json", "").replace("```", "").strip()
                        raw = json.loads(clean)
                        data = {"polter": raw, "meta": {}, "staemme": []} if isinstance(raw, list) else raw
                        st.session_state.analyzed_data = data
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler: {e}")

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        st.divider()
        st.subheader("🕵️ Prüfung & Validierung")
        
        doc_sum = to_float(data.get('meta', {}).get('dokument_summe', 0))
        doc_count = int(to_float(data.get('meta', {}).get('dokument_anzahl_staemme', 0)))
        
        # PDF HIGHLIGHT
        if uploaded_file.type == "application/pdf":
            with st.expander("📄 PDF-Check (Visuell)", expanded=True):
                try:
                    marked_images = create_highlighted_pdf_images(uploaded_file, doc_sum, doc_count)
                    cols = st.columns(len(marked_images))
                    for idx, img in enumerate(marked_images):
                        with cols[idx]:
                            st.image(img, caption=f"Seite {idx+1}", use_container_width=True)
                except: pass

        stamm_sum = sum([to_float(s.get('fm', 0)) for s in data.get('staemme', [])])
        stamm_count = len(data.get('staemme', []))
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Festmeter-Check**")
            st.write(f"Soll: {doc_sum:.2f} Fm")
            st.write(f"Ist: {stamm_sum:.2f} Fm")
            diff = abs(doc_sum - stamm_sum)
            if doc_sum > 0:
                if diff < 1.0: st.success("✅ OK")
                else: st.error(f"⚠️ Diff: {diff:.2f}")
            
        with c2:
            st.markdown("**Stückzahl-Check**")
            st.write(f"Soll: {doc_count}")
            st.write(f"Ist: {stamm_count}")
            if doc_count > 0:
                if doc_count == stamm_count: st.success("✅ OK")
                else: st.error(f"⚠️ Diff: {doc_count - stamm_count}")
            
        with c3:
            st.metric("Polter", len(data.get('polter', [])))

        with st.expander("Details Stämme"):
            st.dataframe(pd.DataFrame(data.get('staemme', [])))

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
        st.subheader("📂 Akten")
        if 'Los_Nr' in df_polter.columns:
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                polter_sum = group['Menge_Fm'].sum()
                stamm_anzahl = 0
                match = pd.DataFrame()
                if not df_staemme.empty:
                    match = df_staemme[(df_staemme['Los_Nr'].astype(str) == str(los))]
                    stamm_anzahl = len(match)

                title = f"🌲 {revier} | Los {los} | 📅 {datum} | 📦 {polter_sum:.2f} Fm | 🪵 {stamm_anzahl} Stk"
                
                with st.expander(title):
                    col_del, col_info = st.columns([1, 4])
                    with col_del:
                        if st.button(f"🗑️ Liste Löschen", key=f"del_{revier}_{los}_{datum}"):
                            with st.spinner("Lösche Daten..."):
                                if delete_entry(los, revier, datum):
                                    st.success("Gelöscht!")
                                    st.cache_data.clear()
                                    st.rerun()

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        st.markdown("**Polter & GPS**")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                        valid_pts = []
                        for _, row in group.iterrows():
                            # Wenn Lat/Lon gültige Zahlen sind, zeige sie
                            if isinstance(row['Lat'], (int, float)) and row['Lat'] != 0:
                                valid_pts.append({"lat": row['Lat'], "lon": row['Lon'], "info": f"P{row['Polter_Nr']}"})
                        
                        if valid_pts:
                            map_df = pd.DataFrame(valid_pts)
                            map_key = f"map_{revier}_{los}_{datum}"
                            # Automatischer Zoom auf die Punkte
                            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=13)
                            for _, pt in map_df.iterrows():
                                folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
                            st_folium(m, width="100%", height=250, key=map_key)
                    with c2:
                        st.markdown(f"**Einzelstämme ({stamm_anzahl}):**")
                        if not match.empty:
                            st.dataframe(match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                        else:
                            st.write("Keine Einzelstämme gespeichert.")
