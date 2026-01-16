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

# --- GOOGLE SHEETS VERBINDUNG ---
def get_spreadsheet():
    """Verbindet sich mit der Google Tabelle"""
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
        st.error(f"Konnte 'Forst_Datenbank' nicht öffnen: {e}")
        return None

def clean_number(value):
    """
    Wandelt deutsche Komma-Zahlen in echte Floats um.
    Beispiel: "1,49" -> 1.49
    """
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        # Ersetze Komma durch Punkt und entferne alles was keine Zahl ist (außer Punkt)
        clean = value.replace(',', '.').strip()
        try:
            return float(clean)
        except:
            return 0.0
    return 0.0

def save_to_sheets(data):
    """Speichert Daten in Google Sheets"""
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
        # Hier wenden wir die Komma-Reinigung an
        fm = clean_number(p.get('fm', 0))
        lat = clean_number(p.get('lat', 0))
        lon = clean_number(p.get('lon', 0))
        
        link = f"http://maps.google.com/?q={lat},{lon}" if lat != 0 else ""
        
        # Wir speichern Lat/Lon als String mit Punkt, damit Google Sheets es kapiert
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
            # Auch hier Kommas bereinigen
            l = clean_number(s.get('l', 0))
            d = clean_number(s.get('d', 0)) # Durchmesser ist meist int, aber sicher ist sicher
            fm = clean_number(s.get('fm', 0))
            
            stamm_rows.append([
                timestamp, 
                los, 
                revier, 
                s.get('wnr', ''),
                s.get('art', ''),
                l,
                d,
                s.get('klasse', ''),
                fm
            ])
        
        if stamm_rows:
            ws_stamm.append_rows(stamm_rows)

    return True

def load_data_frames():
    sh = get_spreadsheet()
    if not sh: return None, None
    
    try:
        df_polter = pd.DataFrame(sh.worksheet("Polter_Uebersicht").get_all_records())
    except:
        df_polter = pd.DataFrame()
        
    try:
        df_staemme = pd.DataFrame(sh.worksheet("Einzelstaemme").get_all_records())
    except:
        df_staemme = pd.DataFrame()
        
    return df_polter, df_staemme

# --- APP ---
st.title("🌲 Forst-Verwaltung Pro")

tab1, tab2 = st.tabs(["📸 Scan & Erfassung", "📊 Bestandsdaten"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except:
        st.stop()

    uploaded_file = st.file_uploader("Holzliste (PDF empfohlen)", type=["pdf", "jpg", "png"])

    if uploaded_file:
        if st.session_state.last_upload != uploaded_file.name:
            st.session_state.analyzed_data = None
            st.session_state.last_upload = uploaded_file.name

        content = None
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF geladen: {uploaded_file.name}")
            content = types.Part.from_bytes(data=uploaded_file.getvalue(), mime_type="application/pdf")
        else:
            img = Image.open(uploaded_file)
            st.image(img, width=400)
            content = img

        if st.session_state.analyzed_data is None:
            if st.button("🚀 Liste komplett analysieren"):
                with st.spinner("Gemini liest Polter UND Einzelstämme..."):
                    try:
                        # --- PROMPT MIT KOMMA-INSTRUKTION ---
                        prompt = """
                        Analysiere diese Holzliste (Deutsch).
                        
                        REGEL FÜR ZAHLEN:
                        Das Dokument verwendet KOMMAS als Dezimaltrenner (z.B. 1,49 oder 48,1234).
                        Du musst diese für das JSON zwingend in PUNKTE umwandeln!
                        Beispiel: "1,49" wird zu 1.49 (Float).
                        
                        AUFGABE:
                        1. Suche Metadaten: Los-Nummer, Revier.
                        2. Suche POLTER mit GPS Koordinaten. Rechne DMS (Grad Minuten Sekunden) in Dezimalgrad um.
                        3. Suche EINZELSTÄMME (Tabelle mit WNr, Länge, Durchmesser, etc.).
                        
                        JSON STRUKTUR:
                        {
                            "meta": {"los": "String", "revier": "String"},
                            "polter": [
                                {"nr": Int, "fm": Float, "lat": Float, "lon": Float}
                            ],
                            "staemme": [
                                {"wnr": "String", "art": "String", "l": Float, "d": Int, "klasse": "String", "fm": Float}
                            ]
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

    # ANZEIGE
    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        st.subheader("1. Gefundene Polter (GPS)")
        st.dataframe(pd.DataFrame(data.get('polter', [])))
        
        st.subheader(f"2. Gefundene Einzelstämme ({len(data.get('staemme', []))} Stück)")
        st.dataframe(pd.DataFrame(data.get('staemme', [])))
        
        if st.button("💾 Alles in Datenbank speichern"):
            with st.spinner("Speichere in Google Sheets..."):
                if save_to_sheets(data):
                    st.balloons()
                    st.success("Erfolgreich gespeichert!")
                    st.info("Siehe Tab 2 für die Gesamtübersicht.")

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    # POLTER
    st.header("📊 Polter Übersicht")
    if not df_polter.empty:
        # Karte bauen (mit Komma-Fix)
        valid_pts = []
        for _, row in df_polter.iterrows():
            try:
                # Doppelte Sicherheit: Auch beim Lesen aus Google Sheets nochmal checken
                lat = clean_number(row['Lat'])
                lon = clean_number(row['Lon'])
                
                if lat != 0 and lon != 0:
                    valid_pts.append({"lat": lat, "lon": lon, "info": f"P{row['Polter_Nr']} ({row['Menge_Fm']} Fm)"})
            except: pass
            
        if valid_pts:
            map_df = pd.DataFrame(valid_pts)
            # Karte zentrieren
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=13)
            for _, pt in map_df.iterrows():
                folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width=900, height=400)
        
        st.dataframe(df_polter, use_container_width=True)
    else:
        st.info("Keine Polter-Daten.")

    # STÄMME
    st.header("🪵 Einzelstamm-Liste")
    if not df_staemme.empty:
        all_lose = df_staemme['Los_Nr'].unique()
        selected_los = st.selectbox("Nach Los filtern:", ["Alle"] + list(all_lose))
        
        df_show = df_staemme if selected_los == "Alle" else df_staemme[df_staemme['Los_Nr'] == selected_los]
        
        st.dataframe(df_show, use_container_width=True)
        
        # Berechnung der Summe (auch hier sicherstellen, dass es Floats sind)
        try:
            # Spalte Volumen_Fm bereinigen falls nötig
            summe = df_show['Volumen_Fm'].apply(clean_number).sum()
            st.metric("Summe Volumen (Auswahl)", f"{summe:.2f} Fm")
        except:
            st.error("Konnte Summe nicht berechnen (Format-Fehler).")
    else:
        st.info("Keine Einzelstämme gespeichert.")
