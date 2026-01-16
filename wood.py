import streamlit as st
from google import genai
from google.genai import types
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
from PIL import Image
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from datetime import datetime

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager Cloud", page_icon="🌲", layout="wide")

# --- HILFSFUNKTIONEN FÜR GOOGLE DRIVE ---
def get_drive_service():
    """Verbindet sich mit Google Drive als Service Account"""
    if "gcp_service_account" not in st.secrets:
        st.error("Fehler: 'gcp_service_account' fehlt in den Secrets.")
        return None
    
    creds_dict = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def upload_to_drive(data, filename_base):
    """Lädt ein JSON direkt in den Drive Ordner hoch"""
    try:
        service = get_drive_service()
        if not service: return None
        
        folder_id = st.secrets["DRIVE_FOLDER_ID"]
        
        # Dateinamen generieren (z.B. 915_2025...json)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_base = "".join([c for c in filename_base if c.isalnum() or c in (' ', '-', '_')]).rstrip()
        filename = f"{clean_base}_{timestamp}.json"
        
        # JSON erstellen
        json_str = json.dumps(data, indent=4, ensure_ascii=False)
        file_stream = io.BytesIO(json_str.encode('utf-8'))
        
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        
        media = MediaIoBaseUpload(file_stream, mimetype='application/json')
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        return filename
    except Exception as e:
        st.error(f"Upload Fehler: {e}")
        return None

def list_drive_files():
    """Lädt alle gespeicherten Listen aus dem Ordner"""
    try:
        service = get_drive_service()
        if not service: return []
        
        folder_id = st.secrets["DRIVE_FOLDER_ID"]
        
        # Nur JSON Dateien suchen, die nicht im Papierkorb sind
        query = f"'{folder_id}' in parents and mimeType = 'application/json' and trashed = false"
        
        results = service.files().list(
            q=query,
            pageSize=100,
            fields="nextPageToken, files(id, name)"
        ).execute()
        
        files = results.get('files', [])
        
        all_data = []
        for file in files:
            # Inhalt herunterladen
            request = service.files().get_media(fileId=file['id'])
            file_content = request.execute()
            
            try:
                content = json.loads(file_content.decode('utf-8'))
                content['datei'] = file['name']
                content['drive_id'] = file['id']
                all_data.append(content)
            except:
                continue # Kaputte Dateien überspringen
                
        return all_data
    except Exception as e:
        st.error(f"Fehler beim Lesen aus Drive: {e}")
        return []

# --- APP OBERFLÄCHE ---
st.title("🌲 Forst-Verwaltung (Cloud)")

# --- DIAGNOSE BUTTON (Nur zur Sicherheit) ---
with st.expander("🛠️ Verbindung testen (bei Problemen hier klicken)"):
    if st.button("Testlauf starten"):
        try:
            service = get_drive_service()
            fid = st.secrets["DRIVE_FOLDER_ID"]
            # Versuch, den Ordner zu lesen
            f = service.files().get(fileId=fid).execute()
            st.success(f"✅ Zugriff OK! Verbunden mit Ordner: '{f.get('name')}'")
            st.info("Wenn der Ordner leer ist, hast du noch nichts gespeichert.")
        except Exception as e:
            st.error(f"❌ Zugriff verweigert: {e}")
            st.warning("Prüfe: Hast du den Ordner mit der 'client_email' geteilt?")


tab1, tab2 = st.tabs(["📸 Neue Liste scannen", "🗂️ Archiv (Drive)"])

# --- TAB 1: SCANNER ---
with tab1:
    # API Key Prüfung
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        client = genai.Client(api_key=api_key)
    except:
        st.error("⚠️ API Key fehlt in den Secrets!")
        st.stop()

    uploaded_file = st.file_uploader("Foto oder PDF hochladen", type=["jpg", "png", "jpeg", "pdf"])

    if uploaded_file:
        file_content_for_gemini = None
        
        # PDF oder Bild Unterscheidung
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF erkannt: {uploaded_file.name}")
            file_bytes = uploaded_file.getvalue()
            file_content_for_gemini = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")
        else:
            image = Image.open(uploaded_file)
            st.image(image, caption="Vorschau", width=400)
            file_content_for_gemini = image

        if st.button("🚀 Jetzt analysieren"):
            with st.spinner('Gemini 2.0 liest die Daten...'):
                prompt = """
                Du bist ein Assistent für die Forstwirtschaft. Analysiere diese Holzliste.
                
                EXTRAHIERE FOLGENDE DATEN als JSON:
                1. "meta": Los-Nummer (oft Format 915/...), Revier, Datum.
                2. "summen": Gesamtmenge (Fm) und Gesamtpreis (falls vorhanden).
                3. "polter": Liste aller Polter.
                   WICHTIG: Suche Koordinaten (z.B. 48°17'...).
                   Rechne sie UNBEDINGT in Dezimalgrad um (lat/lon).
                
                JSON STRUKTUR (Halte dich exakt daran):
                {
                    "meta": {"los": "String", "revier": "String", "datum": "String"},
                    "summen": {"fm": Float, "preis": Float},
                    "polter": [
                        {"nr": Int, "fm": Float, "lat": Float, "lon": Float}
                    ]
                }
                """
                
                try:
                    # Aufruf an Gemini
                    response = client.models.generate_content(
                        model="models/gemini-2.0-flash",
                        contents=[prompt, file_content_for_gemini],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    
                    if response.text:
                        # Bereinigung
                        clean_text = response.text.replace("```json", "").replace("```", "").strip()
                        data = json.loads(clean_text)
                        
                        st.success("Analyse erfolgreich!")
                        
                        # Ergebnis anzeigen
                        if 'polter' in data:
                            st.dataframe(pd.DataFrame(data['polter']))
                        
                        # SPEICHER-BUTTON
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("💾 In Google Drive speichern"):
                                with st.spinner("Lade hoch..."):
                                    # Name aus Los-Nummer oder Dateiname
                                    base_name = data.get('meta', {}).get('los', 'Unbekannt')
                                    saved_name = upload_to_drive(data, base_name)
                                    
                                    if saved_name:
                                        st.balloons()
                                        st.success(f"Gespeichert als: {saved_name}")
                                        st.info("Wechsle jetzt zum Reiter 'Archiv', um es zu sehen.")
                                    
                except Exception as e:
                    st.error(f"Fehler bei der Analyse: {e}")

# --- TAB 2: ARCHIV ---
with tab2:
    if st.button("🔄 Archiv aktualisieren"):
        st.cache_data.clear()
    
    st.write("Lade Daten aus Google Drive...")
    all_records = list_drive_files()
    
    if not all_records:
        st.info("Dein Drive-Ordner ist leer (oder nicht erreichbar). Speichere erst eine Liste in Tab 1.")
    else:
        # 1. Tabelle
        st.subheader("📋 Liste aller Bestände")
        overview_data = []
        for r in all_records:
            meta = r.get('meta', {})
            sums = r.get('summen', {})
            overview_data.append({
                "Datei": r.get('datei'),
                "Los": meta.get('los', '-'),
                "Revier": meta.get('revier', '-'),
                "Menge (Fm)": sums.get('fm', 0),
                "Datum": meta.get('datum', '-')
            })
        st.dataframe(pd.DataFrame(overview_data), use_container_width=True)
        
        # 2. Karte
        st.subheader("📍 Karte aller Polter")
        all_polter_coords = []
        
        for r in all_records:
            los_name = r.get('meta', {}).get('los', '?')
            for p in r.get('polter', []):
                # Nur Polter mit echten Koordinaten anzeigen
                if 'lat' in p and 'lon' in p and p['lat'] is not None and p['lon'] is not None:
                    # Check auf 0.0 Koordinaten (Fehlerfilter)
                    if p['lat'] != 0 and p['lon'] != 0:
                        all_polter_coords.append({
                            "lat": p['lat'],
                            "lon": p['lon'],
                            "info": f"<b>Los: {los_name}</b><br>Polter {p.get('nr')}<br>{p.get('fm')} Fm"
                        })
        
        if all_polter_coords:
            df_map = pd.DataFrame(all_polter_coords)
            # Mittelpunkt finden
            mid_lat = df_map['lat'].mean()
            mid_lon = df_map['lon'].mean()
            
            m = folium.Map(location=[mid_lat, mid_lon], zoom_start=12)
            
            for _, row in df_map.iterrows():
                folium.Marker(
                    [row['lat'], row['lon']],
                    popup=row['info'],
                    icon=folium.Icon(color="green", icon="tree", prefix='fa')
                ).add_to(m)
            
            st_folium(m, width=900, height=500)
        else:
            st.warning("Es wurden noch keine Polter mit GPS-Daten gespeichert.")
