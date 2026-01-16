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

# --- GOOGLE DRIVE VERBINDUNG ---
def get_drive_service():
    # Wir laden die Credentials aus den Secrets
    creds_dict = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def upload_to_drive(data, filename_base):
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]
        
        # Dateinamen vorbereiten
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_base = "".join([c for c in filename_base if c.isalnum() or c in (' ', '-', '_')]).rstrip()
        filename = f"{clean_base}_{timestamp}.json"
        
        # JSON in Bytes umwandeln
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
        st.error(f"Fehler beim Upload: {e}")
        return None

def list_drive_files():
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]
        
        # Suche nach JSON Dateien in diesem Ordner
        query = f"'{folder_id}' in parents and mimeType = 'application/json' and trashed = false"
        
        results = service.files().list(
            q=query,
            pageSize=100,
            fields="nextPageToken, files(id, name)"
        ).execute()
        
        files = results.get('files', [])
        
        all_data = []
        for file in files:
            # Dateiinhalt herunterladen
            request = service.files().get_media(fileId=file['id'])
            file_content = request.execute()
            
            try:
                content = json.loads(file_content.decode('utf-8'))
                content['datei'] = file['name']
                content['drive_id'] = file['id']
                all_data.append(content)
            except:
                pass
                
        return all_data
    except Exception as e:
        st.error(f"Fehler beim Laden aus Drive: {e}")
        return []

# --- APP START ---
st.title("🌲 Forst-Verwaltung (Google Drive)")

tab1, tab2 = st.tabs(["📸 Neue Liste scannen", "☁️ Drive Archiv"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        client = genai.Client(api_key=api_key)
    except:
        st.error("API Key fehlt.")
        st.stop()

    uploaded_file = st.file_uploader("Foto oder PDF hochladen", type=["jpg", "png", "jpeg", "pdf"])

    if uploaded_file:
        file_content_for_gemini = None
        
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF: {uploaded_file.name}")
            file_bytes = uploaded_file.getvalue()
            file_content_for_gemini = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")
        else:
            image = Image.open(uploaded_file)
            st.image(image, caption="Vorschau", width=400)
            file_content_for_gemini = image

        if st.button("Analysieren"):
            with st.spinner('Gemini wertet aus...'):
                prompt = """
                Extrahiere Daten aus dieser Holzliste als JSON.
                Struktur:
                {
                    "meta": {"los": "String", "revier": "String", "datum": "String"},
                    "summen": {"fm": Float, "preis": Float},
                    "polter": [{"nr": Int, "fm": Float, "lat": Float, "lon": Float}]
                }
                WICHTIG: Rechne Koordinaten in Dezimalgrad um!
                """
                
                try:
                    response = client.models.generate_content(
                        model="models/gemini-2.0-flash",
                        contents=[prompt, file_content_for_gemini],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    
                    if response.text:
                        clean_text = response.text.replace("```json", "").replace("```", "").strip()
                        data = json.loads(clean_text)
                        
                        st.success("Analyse fertig!")
                        
                        # Vorschau
                        if 'polter' in data:
                            st.dataframe(pd.DataFrame(data['polter']))
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("☁️ In Google Drive speichern"):
                                with st.spinner("Lade hoch..."):
                                    filename = data.get('meta', {}).get('los', 'Holzliste')
                                    res = upload_to_drive(data, filename)
                                    if res:
                                        st.success(f"Gespeichert in Drive: {res}")
                                        st.balloons()
                                        
                except Exception as e:
                    st.error(f"Fehler: {e}")

# --- TAB 2: DRIVE ARCHIV ---
with tab2:
    if st.button("🔄 Archiv aktualisieren"):
        st.cache_data.clear()
        
    st.header("🗂️ Dein Google Drive Ordner")
    
    with st.spinner("Lade Listen aus der Cloud..."):
        all_records = list_drive_files()
    
    if not all_records:
        st.info("Dein Drive Ordner ist leer oder noch nicht verbunden.")
    else:
        overview_data = []
        for r in all_records:
            meta = r.get('meta', {})
            sums = r.get('summen', {})
            overview_data.append({
                "Datei": r.get('datei'),
                "Los": meta.get('los', '-'),
                "Menge": f"{sums.get('fm', 0)} Fm",
                "Datum": meta.get('datum', '-')
            })
            
        st.dataframe(pd.DataFrame(overview_data), use_container_width=True)
        
        # Karte
        st.subheader("📍 Karte aller Bestände")
        all_polter_coords = []
        for r in all_records:
            los_name = r.get('meta', {}).get('los', '?')
            for p in r.get('polter', []):
                if 'lat' in p and 'lon' in p and p['lat']:
                    all_polter_coords.append({
                        "lat": p['lat'],
                        "lon": p['lon'],
                        "info": f"Los: {los_name} | P{p.get('nr')}"
                    })
        
        if all_polter_coords:
            df_map = pd.DataFrame(all_polter_coords)
            mid_lat = df_map['lat'].mean()
            mid_lon = df_map['lon'].mean()
            m = folium.Map(location=[mid_lat, mid_lon], zoom_start=11)
            for _, row in df_map.iterrows():
                folium.Marker([row['lat'], row['lon']], popup=row['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width=800, height=500)
