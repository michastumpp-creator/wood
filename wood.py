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
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")

# --- INITIALISIERUNG SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None

# --- GOOGLE SHEETS VERBINDUNG ---
def get_google_sheet():
    """Verbindet sich mit der Google Tabelle"""
    if "gcp_service_account" not in st.secrets:
        st.error("Secrets fehlen!")
        return None
    
    try:
        # Anmeldung
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        client = gspread.authorize(creds)
        
        # Öffne die Tabelle mit dem Namen "Forst_Datenbank"
        # Falls du sie anders genannt hast, ändere den Namen hier!
        sheet = client.open("Forst_Datenbank").sheet1
        return sheet
    except Exception as e:
        st.error(f"Verbindung zur Tabelle fehlgeschlagen: {e}")
        st.info("Hast du die Tabelle 'Forst_Datenbank' erstellt und mit der Roboter-Email geteilt?")
        return None

def save_to_sheet(data):
    """Schreibt die analysierten Polter als Zeilen in die Tabelle"""
    sheet = get_google_sheet()
    if not sheet: return False
    
    try:
        # Prüfen ob Kopfzeile existiert, sonst erstellen
        if not sheet.row_values(1):
            header = ["Datum_Eintrag", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "GoogleMaps", "Datei_Info"]
            sheet.append_row(header)
        
        # Daten vorbereiten
        rows_to_add = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        meta = data.get('meta', {})
        los = meta.get('los', 'Unbekannt')
        revier = meta.get('revier', '-')
        
        for p in data.get('polter', []):
            lat = p.get('lat', 0)
            lon = p.get('lon', 0)
            maps_link = ""
            if lat and lon:
                maps_link = f"http://maps.google.com/?q={lat},{lon}"
            
            row = [
                timestamp,
                los,
                revier,
                p.get('nr', 0),
                p.get('fm', 0),
                str(lat), # Als Text speichern um Komma-Probleme zu vermeiden
                str(lon),
                maps_link,
                "Auto-Import"
            ]
            rows_to_add.append(row)
            
        # Alles auf einmal schreiben
        if rows_to_add:
            sheet.append_rows(rows_to_add)
            return True
        return False
        
    except Exception as e:
        st.error(f"Fehler beim Speichern: {e}")
        return False

def load_from_sheet():
    """Lädt alle Daten aus der Tabelle"""
    sheet = get_google_sheet()
    if not sheet: return []
    
    try:
        # Alle Datensätze laden (als Liste von Dictionaries)
        records = sheet.get_all_records()
        return records
    except Exception as e:
        st.error(f"Fehler beim Laden: {e}")
        return []

# --- APP START ---
st.title("🌲 Forst-Verwaltung (Google Sheets Backend)")

tab1, tab2 = st.tabs(["📸 Neue Liste erfassen", "📊 Bestands-Übersicht"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except:
        st.stop()

    uploaded_file = st.file_uploader("Holzliste hochladen (PDF/Foto)", type=["jpg", "png", "jpeg", "pdf"])

    if uploaded_file:
        # Reset bei neuer Datei
        if st.session_state.last_upload != uploaded_file.name:
            st.session_state.analyzed_data = None
            st.session_state.last_upload = uploaded_file.name

        # Datei vorbereiten
        file_content_gemini = None
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF: {uploaded_file.name}")
            file_content_gemini = types.Part.from_bytes(data=uploaded_file.getvalue(), mime_type="application/pdf")
        else:
            img = Image.open(uploaded_file)
            st.image(img, width=400)
            file_content_gemini = img

        # ANALYSE BUTTON
        if st.session_state.analyzed_data is None:
            if st.button("🚀 Jetzt Analysieren"):
                with st.spinner("Gemini liest die Tabelle..."):
                    try:
                        prompt = """
                        Extrahiere die Daten exakt als JSON.
                        
                        Struktur:
                        {
                            "meta": {"los": "String (z.B. 915...)", "revier": "String"},
                            "polter": [
                                {"nr": Int, "fm": Float, "lat": Float, "lon": Float}
                            ]
                        }
                        
                        WICHTIG:
                        1. Suche Koordinaten im Text (DMS Format).
                        2. Rechne sie in Dezimalgrad um (lat/lon).
                        3. Gib NUR das JSON zurück.
                        """
                        
                        response = client.models.generate_content(
                            model="models/gemini-2.0-flash",
                            contents=[prompt, file_content_gemini],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        
                        # JSON Parsen
                        clean_text = response.text.replace("```json", "").replace("```", "").strip()
                        data = json.loads(clean_text)
                        
                        st.session_state.analyzed_data = data
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"Fehler: {e}")

    # ERGEBNIS ANZEIGEN & SPEICHERN
    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        st.success(f"✅ Analyse fertig: {len(data.get('polter', []))} Polter gefunden.")
        st.dataframe(pd.DataFrame(data.get('polter', [])))
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 In Datenbank speichern"):
                with st.spinner("Schreibe in Google Tabelle..."):
                    success = save_to_sheet(data)
                    if success:
                        st.balloons()
                        st.success("Erfolgreich gespeichert!")
                        # Optional: Session State leeren
                        # st.session_state.analyzed_data = None
                        st.info("Wechsle zu Tab 2 um den Gesamtbestand zu sehen.")

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄 Tabelle aktualisieren"):
        st.cache_data.clear()
        
    st.write("Lade Daten aus 'Forst_Datenbank'...")
    records = load_from_sheet()
    
    if not records:
        st.warning("Die Datenbank ist leer oder nicht erreichbar.")
    else:
        df = pd.DataFrame(records)
        
        # 1. KPIs
        total_fm = df['Menge_Fm'].sum() if 'Menge_Fm' in df.columns else 0
        total_polter = len(df)
        
        k1, k2, k3 = st.columns(3)
        k1.metric("Gesamtmenge", f"{total_fm:.2f} Fm")
        k2.metric("Anzahl Polter", total_polter)
        k3.metric("Anzahl Lose", df['Los_Nr'].nunique() if 'Los_Nr' in df.columns else 0)
        
        # 2. Tabelle
        st.dataframe(df, use_container_width=True)
        
        # 3. Karte
        # Wir müssen sicherstellen, dass Lat/Lon als Zahlen erkannt werden
        valid_points = []
        for index, row in df.iterrows():
            try:
                lat = float(str(row['Lat']).replace(',', '.'))
                lon = float(str(row['Lon']).replace(',', '.'))
                
                if lat != 0 and lon != 0:
                    valid_points.append({
                        "lat": lat,
                        "lon": lon,
                        "info": f"Los: {row['Los_Nr']} | Polter {row['Polter_Nr']} ({row['Menge_Fm']} Fm)"
                    })
            except:
                continue
                
        if valid_points:
            map_df = pd.DataFrame(valid_points)
            st.subheader("📍 Karte aller Bestände")
            
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=12)
            
            for _, pt in map_df.iterrows():
                folium.Marker(
                    [pt['lat'], pt['lon']],
                    popup=pt['info'],
                    icon=folium.Icon(color="green", icon="tree", prefix='fa')
                ).add_to(m)
            
            st_folium(m, width=900, height=500)
        else:
            st.info("Keine gültigen GPS-Daten in der Tabelle gefunden.")
