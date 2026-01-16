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
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager Pro", page_icon="🌲", layout="wide")

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None
if 'current_file_link' not in st.session_state:
    st.session_state.current_file_link = None

# --- HELFER: ZAHLEN BEREINIGEN (Aggressiv) ---
def clean_number(value):
    """
    Wandelt alles in Float um.
    '1,50' -> 1.50
    '1.50' -> 1.50
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Ersetze Komma durch Punkt
        clean = value.replace(',', '.')
        # Entferne alles außer Zahlen und Punkt
        clean = "".join(c for c in clean if c.isdigit() or c == '.')
        try:
            return float(clean)
        except:
            return 0.0
    return 0.0

# --- AUTHENTIFIZIERUNG ---
def get_creds():
    if "gcp_service_account" not in st.secrets:
        st.error("Secrets fehlen!")
        return None
    return service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )

# --- GOOGLE DRIVE: DATEI HOCHLADEN ---
def upload_file_to_drive(uploaded_file, filename):
    """Lädt das PDF/Bild physisch in Drive hoch und gibt den Link zurück"""
    try:
        creds = get_creds()
        service = build('drive', 'v3', credentials=creds)
        folder_id = st.secrets["DRIVE_FOLDER_ID"]
        
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        
        # Mime-Type erkennen
        mimetype = uploaded_file.type
        
        # Stream erstellen
        media = MediaIoBaseUpload(uploaded_file, mimetype=mimetype)
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        return file.get('webViewLink')
        
    except Exception as e:
        st.error(f"Fehler beim Datei-Upload: {e}")
        return "Upload fehlgeschlagen"

# --- GOOGLE SHEETS VERBINDUNG ---
def get_spreadsheet():
    try:
        creds = get_creds()
        client = gspread.authorize(creds)
        return client.open("Forst_Datenbank")
    except Exception as e:
        st.error(f"Fehler beim Öffnen der Tabelle: {e}")
        return None

def save_to_sheets(data, file_link):
    sh = get_spreadsheet()
    if not sh: return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Sicherheits-Check: Falls data Liste ist, umwandeln
    if isinstance(data, list):
         data = {"polter": data, "meta": {}, "staemme": []}

    meta = data.get('meta', {})
    los = meta.get('los', 'Unbekannt')
    revier = meta.get('revier', 'Unbekannt')
    datum_aufnahme = meta.get('datum', timestamp.split(' ')[0])

    # --- BLATT 1: POLTER ---
    try:
        ws_polter = sh.worksheet("Polter_Uebersicht")
    except:
        ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=11)
        ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link", "Original_Datei"])

    polter_rows = []
    for p in data.get('polter', []):
        fm = clean_number(p.get('fm', 0))
        lat = clean_number(p.get('lat', 0))
        lon = clean_number(p.get('lon', 0))
        link = f"http://maps.google.com/?q={lat},{lon}" if lat != 0 else ""
        polter_rows.append([timestamp, datum_aufnahme, los, revier, p.get('nr'), fm, str(lat), str(lon), link, file_link])
    
    if polter_rows:
        ws_polter.append_rows(polter_rows)

    # --- BLATT 2: EINZELSTÄMME ---
    staemme_data = data.get('staemme', [])
    if staemme_data:
        try:
            ws_stamm = sh.worksheet("Einzelstaemme")
        except:
            ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=11)
            ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm", "Original_Datei"])

        stamm_rows = []
        for s in staemme_data:
            l = clean_number(s.get('l', 0))
            d = clean_number(s.get('d', 0))
            fm = clean_number(s.get('fm', 0))
            stamm_rows.append([timestamp, los, revier, s.get('wnr', ''), s.get('art', ''), l, d, s.get('klasse', ''), fm, file_link])
        
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

tab1, tab2 = st.tabs(["📸 Scan & Erfassung", "🗃️ Bestand (Übersicht)"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except:
        st.stop()

    uploaded_file = st.file_uploader("Holzliste (PDF)", type=["pdf", "jpg", "png"])

    if uploaded_file:
        # Reset bei neuer Datei
        if st.session_state.last_upload != uploaded_file.name:
            st.session_state.analyzed_data = None
            st.session_state.current_file_link = None
            st.session_state.last_upload = uploaded_file.name

        # Vorschau
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
                with st.spinner("Gemini arbeitet (Zahlen-Fix aktiv)..."):
                    try:
                        prompt = """
                        Analysiere diese Holzliste.
                        
                        WICHTIGSTE REGEL:
                        Wandle ALLE Komma-Zahlen (1,50) in Punkt-Zahlen (1.50) um. Das JSON darf keine Kommas als Dezimaltrenner haben.
                        
                        EXTRAHIERE:
                        1. meta: Los-Nummer, Revier, Datum der Aufnahme.
                        2. polter: GPS Koordinaten (Dezimalgrad!).
                        3. staemme: Tabelle mit WNr, Länge, Durchmesser, Güte, Fm.
                        
                        FORMAT (JSON Dictionary):
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
                        
                        # Fix falls Liste statt Dict kommt
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
        st.subheader("Vorschau Daten:")
        st.json(data.get('meta', {}))
        st.write(f"Polter gefunden: {len(data.get('polter', []))}")
        st.write(f"Stämme gefunden: {len(data.get('staemme', []))}")
        
        if st.button("💾 Speichern (inkl. Datei-Upload)"):
            with st.spinner("Lade Datei zu Drive & speichere Daten..."):
                # 1. Datei hochladen
                # Wir müssen den Pointer der Datei zurücksetzen, da er evtl schon gelesen wurde
                uploaded_file.seek(0)
                file_link = upload_file_to_drive(uploaded_file, uploaded_file.name)
                
                # 2. Daten speichern
                if save_to_sheets(data, file_link):
                    st.success(f"Erfolgreich gespeichert! Datei-Link: {file_link}")
                    st.session_state.analyzed_data = None # Reset nach Speichern
                    st.info("Wechsle jetzt zum Tab 'Bestand'.")

# --- TAB 2: BESTAND (NEUES DESIGN) ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    if df_polter.empty:
        st.info("Noch keine Daten vorhanden.")
    else:
        # --- KOPFZEILE: KARTE ---
        st.subheader("🗺️ Gesamtübersicht")
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

        # --- HAUPTTEIL: DIE "BUTTONS" (EXPANDER) ---
        st.divider()
        st.subheader("📂 Akten nach Revier & Datum")
        
        # Wir gruppieren die Daten: Ein "Block" pro Revier+Los+Datum
        # Wir erstellen eine Hilfsspalte für die Gruppierung
        if 'Los_Nr' in df_polter.columns and 'Revier' in df_polter.columns:
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            # Wir iterieren durch die Gruppen (Neueste zuerst wäre gut, hier standard sortiert)
            for (revier, los, datum), group in groups:
                
                # Berechnung der Summen für die Beschriftung des Buttons
                total_fm = group['Menge_Fm'].apply(clean_number).sum()
                file_link = group['Original_Datei'].iloc[0] if 'Original_Datei' in group.columns else "#"
                
                # DER BUTTON (EXPANDER)
                expander_title = f"🌲 Revier: {revier} | Los: {los} | Datum: {datum} | 📦 {total_fm:.2f} Fm"
                
                with st.expander(expander_title):
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        st.markdown(f"**Gesamtmenge:** {total_fm:.2f} Festmeter")
                        st.markdown(f"**Anzahl Polter:** {len(group)}")
                        st.markdown(f"🔗 [Original-Datei öffnen]({file_link})")
                        
                        st.write("Polter-Details:")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                    
                    with col2:
                        # Einzelstämme dazu finden
                        if not df_staemme.empty:
                            stamm_match = df_staemme[
                                (df_staemme['Los_Nr'].astype(str) == str(los)) & 
                                (df_staemme['Revier'].astype(str) == str(revier))
                            ]
                            if not stamm_match.empty:
                                st.write(f"🪵 {len(stamm_match)} Einzelstämme:")
                                st.dataframe(stamm_match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                            else:
                                st.info("Keine Einzelstämme erfasst.")
