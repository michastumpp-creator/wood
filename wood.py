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
import re

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None

# --- HELFER: ZAHLEN RETTEN ---
def clean_number(value, is_volume=False):
    if isinstance(value, (int, float)):
        val = float(value)
    elif isinstance(value, str):
        clean = value.replace(',', '.')
        clean = re.sub(r'[^\d.]', '', clean)
        try:
            val = float(clean)
        except:
            return 0.0
    else:
        return 0.0

    if is_volume and val > 15.0: 
        if 0.5 < (val / 100) < 15: return val / 100
        if 0.5 < (val / 10) < 15: return val / 10
            
    return val

# --- HELFER: KOORDINATEN RETTEN ---
def fix_coordinates(lat, lon):
    l1 = clean_number(lat)
    l2 = clean_number(lon)
    if l1 == 0 and l2 == 0: return 0.0, 0.0
    if l1 > l2: return l1, l2
    else: return l2, l1

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
    datum_aufnahme = str(meta.get('datum', timestamp.split(' ')[0]))

    # BLATT 1: POLTER
    try: ws_polter = sh.worksheet("Polter_Uebersicht")
    except: ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=10); ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link"])

    polter_rows = []
    for p in data.get('polter', []):
        fm = clean_number(p.get('fm', 0))
        lat, lon = fix_coordinates(p.get('lat', 0), p.get('lon', 0))
        link = f"http://maps.google.com/?q={lat},{lon}" if lat != 0 else ""
        polter_rows.append([timestamp, datum_aufnahme, los, revier, p.get('nr'), fm, str(lat), str(lon), link])
    if polter_rows: ws_polter.append_rows(polter_rows)

    # BLATT 2: EINZELSTÄMME
    staemme_data = data.get('staemme', [])
    if staemme_data:
        try: ws_stamm = sh.worksheet("Einzelstaemme")
        except: ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10); ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])

        stamm_rows = []
        for s in staemme_data:
            l = clean_number(s.get('l', 0))
            d = clean_number(s.get('d', 0))
            fm = clean_number(s.get('fm', 0), is_volume=True)
            stamm_rows.append([timestamp, los, revier, s.get('wnr', ''), s.get('art', ''), l, d, s.get('klasse', ''), fm])
        if stamm_rows: ws_stamm.append_rows(stamm_rows)

    return True

# --- NEU: DATEN LÖSCHEN ---
def delete_entry(los, revier, datum_aufnahme):
    sh = get_spreadsheet()
    if not sh: return False

    try:
        # 1. POLTER BEREINIGEN
        ws_p = sh.worksheet("Polter_Uebersicht")
        data_p = ws_p.get_all_records()
        df_p = pd.DataFrame(data_p)
        
        # Wir behalten alle Zeilen, die NICHT zum Lösch-Ziel gehören
        # Wir wandeln alles in Strings um, um Typ-Probleme (123 vs "123") zu vermeiden
        mask_p = (df_p['Los_Nr'].astype(str) == str(los)) & \
                 (df_p['Revier'].astype(str) == str(revier)) & \
                 (df_p['Datum_Aufnahme'].astype(str) == str(datum_aufnahme))
        
        df_p_clean = df_p[~mask_p] # Das Tilde ~ bedeutet "NICHT" (Invertierung)
        
        ws_p.clear() # Blatt leeren
        # Header + Daten schreiben
        ws_p.update([df_p_clean.columns.values.tolist()] + df_p_clean.values.tolist())

        # 2. STÄMME BEREINIGEN
        # Stämme haben evtl. kein "Datum_Aufnahme" in der Tabelle, daher löschen wir über Los & Revier
        try:
            ws_s = sh.worksheet("Einzelstaemme")
            data_s = ws_s.get_all_records()
            df_s = pd.DataFrame(data_s)
            
            mask_s = (df_s['Los_Nr'].astype(str) == str(los)) & \
                     (df_s['Revier'].astype(str) == str(revier))
            
            df_s_clean = df_s[~mask_s]
            
            ws_s.clear()
            ws_s.update([df_s_clean.columns.values.tolist()] + df_s_clean.values.tolist())
        except:
            pass # Falls Blatt nicht existiert oder leer ist, egal

        return True
    except Exception as e:
        st.error(f"Fehler beim Löschen: {e}")
        return False

def load_data_frames():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame(), pd.DataFrame()
    try: df_polter = pd.DataFrame(sh.worksheet("Polter_Uebersicht").get_all_records())
    except: df_polter = pd.DataFrame()
    try: df_staemme = pd.DataFrame(sh.worksheet("Einzelstaemme").get_all_records())
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
                        Du bist ein KI-Assistent für deutsche Forstwirtschaft. Analysiere dieses Dokument. Penibel wie ein Buchhalter.
                        
                        --- AUFGABE 1: EINZELSTÄMME FINDEN ---
                        Suche die Tabelle "ZUSAMMENSTELLUNG NACH WALDNUMMERN".
                        WICHTIG: Tabelle ist ZWEISPALTIG (Daten links UND rechts).
                        
                        Spalten-Kürzel sind meist:
                        - "WNr" = Waldnummer
                        - "Lä"  = Länge
                        - "DoR" = Durchmesser
                        - "FmoR" = Volumen/Fm
                        Achte auf korrekte menge. 123,45 Fm sind 123.45 Fm und nicht 1234.00. 
                        --- AUFGABE 2: SUMMEN & POLTER ---
                        - Suche  nach "Gesamtmenge".
                        - Suche Polter-Listen mit GPS.

                        --- JSON STRUKTUR ---
                        {
                            "meta": {"los": "String", "revier": "String", "datum": "String", "dokument_summe": Float},
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
        st.subheader("🕵️ Prüfung")
        doc_sum = clean_number(data.get('meta', {}).get('dokument_summe', 0))
        stamm_sum = sum([clean_number(s.get('fm', 0), True) for s in data.get('staemme', [])])
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Dokument", f"{doc_sum:.2f} Fm")
        c2.metric("Gefunden", f"{stamm_sum:.2f} Fm")
        if abs(doc_sum - stamm_sum) < 1.0 and doc_sum > 0: c3.success("✅ Stimmt überein")
        else: c3.warning("⚠️ Abweichung")

        with st.expander("Details"):
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
        # KARTE
        st.subheader("🗺️ Karte")
        valid_pts = []
        for _, row in df_polter.iterrows():
            try:
                lat, lon = fix_coordinates(row.get('Lat',0), row.get('Lon',0))
                if lat != 0:
                    valid_pts.append({"lat": lat, "lon": lon, "info": f"Revier {row['Revier']} | P{row['Polter_Nr']}"})
            except: pass
        if valid_pts:
            map_df = pd.DataFrame(valid_pts)
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=11)
            for _, pt in map_df.iterrows():
                folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width="100%", height=350)
        
        st.divider()
        st.subheader("📂 Akten")
        
        if 'Los_Nr' in df_polter.columns:
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                polter_sum = group['Menge_Fm'].apply(clean_number).sum()
                
                # Container für einen Eintrag
                with st.expander(f"🌲 {revier} | Los {los} | 📅 {datum} | 📦 {polter_sum:.2f} Fm"):
                    
                    # LÖSCH BUTTON
                    col_del, col_info = st.columns([1, 4])
                    with col_del:
                        # Wichtig: Unique Key für jeden Button!
                        if st.button(f"🗑️ Liste Löschen", key=f"del_{revier}_{los}_{datum}"):
                            with st.spinner("Lösche Daten..."):
                                if delete_entry(los, revier, datum):
                                    st.success("Gelöscht!")
                                    st.cache_data.clear() # Cache leeren
                                    st.rerun() # Seite neu laden

                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("**Polter:**")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                    with c2:
                        st.markdown("**Stämme:**")
                        if not df_staemme.empty:
                            match = df_staemme[(df_staemme['Los_Nr'].astype(str) == str(los))]
                            if not match.empty:
                                st.dataframe(match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                            else:
                                st.write("Keine Stämme.")
