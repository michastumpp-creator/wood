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

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager Pro", page_icon="🌲", layout="wide")

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None

# --- HELFER: ZAHLEN BEREINIGEN ---
def clean_number(value):
    """Macht aus '1,50' -> 1.50 (Float)"""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        clean = value.replace(',', '.').strip()
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
    meta = data.get('meta', {})
    los = meta.get('los', 'Unbekannt')
    revier = meta.get('revier', '-')

    # --- BLATT 1: POLTER ---
    try:
        ws_polter = sh.worksheet("Polter_Uebersicht")
    except:
        ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=10)
        ws_polter.append_row(["Datum", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link"])

    polter_rows = []
    for p in data.get('polter', []):
        fm = clean_number(p.get('fm', 0))
        lat = clean_number(p.get('lat', 0))
        lon = clean_number(p.get('lon', 0))
        link = f"http://maps.google.com/?q={lat},{lon}" if lat != 0 else ""
        polter_rows.append([timestamp, los, revier, p.get('nr'), fm, str(lat), str(lon), link])
    
    if polter_rows:
        ws_polter.append_rows(polter_rows)

    # --- BLATT 2: EINZELSTÄMME ---
    staemme_data = data.get('staemme', [])
    if staemme_data:
        try:
            ws_stamm = sh.worksheet("Einzelstaemme")
        except:
            ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10)
            ws_stamm.append_row(["Datum", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])

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
st.title("🌲 Forst-Verwaltung Pro")

tab1, tab2 = st.tabs(["📸 Scan & Erfassung", "📊 Bestandsdaten"])

# --- TAB 1 ---
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

        if st.session_state.analyzed_data is None:
            if st.button("🚀 Analysieren"):
                with st.spinner("Gemini arbeitet (Kommas -> Punkte)..."):
                    try:
                        prompt = """
                        Analysiere diese Holzliste.
                        REGEL: Wandle Kommas in Zahlen zwingend in PUNKTE um (1,50 -> 1.50).
                        
                        Suche:
                        1. Metadaten (Los, Revier)
                        2. Polter (GPS in Dezimalgrad!)
                        3. Einzelstämme (WNr, Länge, Durchmesser, Güte, Fm)
                        
                        JSON:
                        {
                            "meta": {"los": "String", "revier": "String"},
                            "polter": [{"nr": Int, "fm": Float, "lat": Float, "lon": Float}],
                            "staemme": [{"wnr": "String", "art": "String", "l": Float, "d": Int, "klasse": "String", "fm": Float}]
                        }
                        """
                        response = client.models.generate_content(
                            model="models/gemini-2.0-flash",
                            contents=[prompt, content],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        clean_text = response.text.replace("```json", "").replace("```", "").strip()
                        st.session_state.analyzed_data = json.loads(clean_text)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler: {e}")

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        st.subheader("Vorschau:")
        st.dataframe(pd.DataFrame(data.get('polter', [])))
        st.dataframe(pd.DataFrame(data.get('staemme', [])))
        
        if st.button("💾 Speichern"):
            if save_to_sheets(data):
                st.success("Gespeichert!")
                st.info("Wechsle zu Tab 2.")

# --- TAB 2 (HIER WAR DER FEHLER) ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    # 1. POLTER KARTE
    st.subheader("🗺️ Polter Karte")
    if not df_polter.empty and 'Lat' in df_polter.columns:
        valid_pts = []
        for _, row in df_polter.iterrows():
            try:
                lat = clean_number(row['Lat'])
                lon = clean_number(row['Lon'])
                if lat != 0:
                    valid_pts.append({"lat": lat, "lon": lon, "info": f"P{row.get('Polter_Nr','')}"})
            except: pass
            
        if valid_pts:
            map_df = pd.DataFrame(valid_pts)
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=13)
            for _, pt in map_df.iterrows():
                folium.Marker([pt['lat'], pt['lon']], popup=pt['info']).add_to(m)
            st_folium(m, width=900, height=400)
    else:
        st.info("Keine Polter-Daten gefunden.")

    # 2. EINZELSTÄMME (FEHLER BEHOBEN)
    st.subheader("🪵 Einzelstämme")
    
    # WICHTIG: Wir prüfen erst, ob die Spalte existiert!
    if not df_staemme.empty and 'Los_Nr' in df_staemme.columns:
        all_lose = df_staemme['Los_Nr'].unique()
        selected_los = st.selectbox("Filter Los:", ["Alle"] + list(all_lose))
        
        if selected_los == "Alle":
            df_show = df_staemme
        else:
            df_show = df_staemme[df_staemme['Los_Nr'] == selected_los]
            
        st.dataframe(df_show, use_container_width=True)
        
        if 'Volumen_Fm' in df_show.columns:
             summe = df_show['Volumen_Fm'].apply(clean_number).sum()
             st.metric("Summe Volumen", f"{summe:.2f} Fm")
    else:
        st.warning("Noch keine Einzelstämme gespeichert (oder Tabelle leer).")
        st.write("Tipp: Lade in Tab 1 eine Liste hoch und klicke 'Speichern'.")
