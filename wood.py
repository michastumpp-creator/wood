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

# --- HELFER: ZAHLEN BEREINIGEN (Sehr streng) ---
def clean_number(value):
    """
    Macht aus JEDEM Input einen sauberen Float-Wert.
    '1,50' -> 1.5
    '1.50' -> 1.5
    'ca. 10,5' -> 10.5
    """
    if isinstance(value, (int, float)):
        return float(value)
    
    if isinstance(value, str):
        # 1. Komma zu Punkt
        clean = value.replace(',', '.')
        # 2. Alle Buchstaben entfernen, nur Zahlen und Punkt behalten
        # Regex: Behalte Ziffern und Punkt
        clean = re.sub(r'[^\d.]', '', clean)
        
        # 3. Falls zwei Punkte da sind (Fehler), nimm den ersten
        if clean.count('.') > 1:
            clean = clean.split('.')[0] + '.' + clean.split('.')[1]

        try:
            return float(clean)
        except:
            return 0.0
    return 0.0

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

def save_to_sheets(data):
    sh = get_spreadsheet()
    if not sh: return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Fallback falls KI Liste statt Dict liefert
    if isinstance(data, list):
         data = {"polter": data, "meta": {}, "staemme": []}

    meta = data.get('meta', {})
    los = str(meta.get('los', 'Unbekannt'))
    revier = str(meta.get('revier', 'Unbekannt'))
    datum_aufnahme = str(meta.get('datum', timestamp.split(' ')[0]))

    # --- BLATT 1: POLTER ---
    try:
        ws_polter = sh.worksheet("Polter_Uebersicht")
    except:
        # Erstelle Blatt neu (OHNE Datei-Link Spalte)
        ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=10)
        ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link"])

    polter_rows = []
    for p in data.get('polter', []):
        fm = clean_number(p.get('fm', 0))
        lat = clean_number(p.get('lat', 0))
        lon = clean_number(p.get('lon', 0))
        link = f"http://maps.google.com/?q={lat},{lon}" if lat != 0 else ""
        
        # Speichere Zahlen explizit als String mit Punkt für Google Sheets Import
        polter_rows.append([timestamp, datum_aufnahme, los, revier, p.get('nr'), fm, str(lat), str(lon), link])
    
    if polter_rows:
        ws_polter.append_rows(polter_rows)

    # --- BLATT 2: EINZELSTÄMME ---
    staemme_data = data.get('staemme', [])
    if staemme_data:
        try:
            ws_stamm = sh.worksheet("Einzelstaemme")
        except:
            # Erstelle Blatt neu (OHNE Datei-Link Spalte)
            ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10)
            ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])

        stamm_rows = []
        for s in staemme_data:
            l = clean_number(s.get('l', 0))
            d = clean_number(s.get('d', 0))
            fm = clean_number(s.get('fm', 0))
            stamm_rows.append([timestamp, los, revier, s.get('wnr', ''), s.get('art', ''), l, d, s.get('klasse', ''), fm])
        
        if stamm_rows:
            ws_stamm.append_rows(stamm_rows)

    return True

def load_data_frames():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame(), pd.DataFrame()
    
    try:
        data_p = sh.worksheet("Polter_Uebersicht").get_all_records()
        df_polter = pd.DataFrame(data_p)
    except:
        df_polter = pd.DataFrame()
        
    try:
        data_s = sh.worksheet("Einzelstaemme").get_all_records()
        df_staemme = pd.DataFrame(data_s)
    except:
        df_staemme = pd.DataFrame()
        
    return df_polter, df_staemme

# --- APP START ---
st.title("🌲 Forst-Verwaltung")

tab1, tab2 = st.tabs(["📸 Scan & Erfassung", "🗃️ Bestand"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except:
        st.stop()

    uploaded_file = st.file_uploader("Holzliste (PDF)", type=["pdf", "jpg", "png"])

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

        # ANALYSE
        if st.session_state.analyzed_data is None:
            if st.button("🚀 Analysieren"):
                with st.spinner("Gemini arbeitet (Kommas -> Punkte)..."):
                    try:
                        prompt = """
                        Analysiere diese Holzliste.
                        
                        REGELN:
                        1. Wandle ALLE Komma-Zahlen (1,50) in Punkt-Zahlen (1.50) um.
                        2. JSON Format (keine Listen als Root).
                        
                        JSON STRUKTUR:
                        {
                            "meta": {"los": "String", "revier": "String", "datum": "String"},
                            "polter": [{"nr": Int, "fm": Float, "lat": Float, "lon": Float}],
                            "staemme": [{"wnr": "String", "art": "String", "l": Float, "d": Float, "klasse": "String", "fm": Float}]
                        }
                        """
                        response = client.models.generate_content(
                            model="models/gemini-2.0-flash",
                            contents=[prompt, content],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        clean_text = response.text.replace("```json", "").replace("```", "").strip()
                        raw_data = json.loads(clean_text)
                        
                        # Listen-Fix
                        if isinstance(raw_data, list):
                            data = {"polter": raw_data, "meta": {}, "staemme": []}
                        else:
                            data = raw_data

                        st.session_state.analyzed_data = data
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler: {e}")

    # SPEICHERN
    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        # Kleine Vorschau
        c1, c2 = st.columns(2)
        c1.metric("Polter gefunden", len(data.get('polter', [])))
        c2.metric("Stämme gefunden", len(data.get('staemme', [])))
        
        if st.button("💾 Daten in Tabelle speichern"):
            with st.spinner("Speichere..."):
                if save_to_sheets(data):
                    st.success("Erfolgreich gespeichert!")
                    st.session_state.analyzed_data = None 
                    st.info("Daten sind jetzt im Reiter 'Bestand' sichtbar.")

# --- TAB 2: BESTAND (EXPANDER ANSICHT) ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    if df_polter.empty:
        st.info("Noch keine Daten vorhanden.")
    else:
        # KARTE OBEN
        st.subheader("🗺️ Gesamtkarte")
        valid_pts = []
        for _, row in df_polter.iterrows():
            try:
                lat = clean_number(row['Lat'])
                lon = clean_number(row['Lon'])
                if lat != 0:
                    valid_pts.append({"lat": lat, "lon": lon, "info": f"Revier {row['Revier']} | P{row['Polter_Nr']}"})
            except: pass
            
        if valid_pts:
            map_df = pd.DataFrame(valid_pts)
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=11)
            for _, pt in map_df.iterrows():
                folium.Marker([pt['lat'], pt['lon']], popup=pt['info']).add_to(m)
            st_folium(m, width="100%", height=300)

        # BUTTON-ANSICHT (EXPANDER)
        st.divider()
        st.subheader("📂 Akten")
        
        # Prüfen ob Spalten da sind
        if 'Los_Nr' in df_polter.columns and 'Revier' in df_polter.columns:
            # Sortieren damit neueste oben sind (wenn Datum sauber, sonst einfach so)
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                # Summen berechnen
                total_fm = group['Menge_Fm'].apply(clean_number).sum()
                
                # Der "Button"
                title = f"🌲 Revier: {revier} | Los: {los} | 📅 {datum} | Menge: {total_fm:.2f} Fm"
                
                with st.expander(title):
                    c1, c2 = st.columns([1, 1])
                    
                    with c1:
                        st.markdown("**Polter-Liste:**")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                        
                    with c2:
                        # Passende Stämme suchen
                        if not df_staemme.empty and 'Los_Nr' in df_staemme.columns:
                            stamm_match = df_staemme[
                                (df_staemme['Los_Nr'].astype(str) == str(los)) & 
                                (df_staemme['Revier'].astype(str) == str(revier))
                            ]
                            if not stamm_match.empty:
                                st.markdown(f"**Einzelstämme ({len(stamm_match)}):**")
                                st.dataframe(stamm_match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                            else:
                                st.write("Keine Einzelstämme zu diesem Los.")
