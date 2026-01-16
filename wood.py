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

# --- INITIALISIERUNG SESSION STATE (Das Gedächtnis) ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None

# --- DRIVE FUNKTIONEN ---
def get_drive_service():
    if "gcp_service_account" not in st.secrets:
        st.error("Secrets fehlen!")
        return None
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def upload_to_drive(data, filename_base):
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_base = "".join([c for c in filename_base if c.isalnum() or c in (' ', '-', '_')]).rstrip()
        filename = f"{clean_base}_{timestamp}.json"
        
        json_str = json.dumps(data, indent=4, ensure_ascii=False)
        file_stream = io.BytesIO(json_str.encode('utf-8'))
        
        metadata = {'name': filename, 'parents': [folder_id]}
        media = MediaIoBaseUpload(file_stream, mimetype='application/json')
        
        service.files().create(body=metadata, media_body=media).execute()
        return filename
    except Exception as e:
        st.error(f"Upload Fehler: {e}")
        return None

def list_drive_files():
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]
        
        # Wir suchen ALLES, egal welcher Typ
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            pageSize=50,
            fields="files(id, name)"
        ).execute()
        
        files = results.get('files', [])
        data_list = []
        
        for f in files:
            try:
                # Versuche Inhalt zu lesen
                content_bytes = service.files().get_media(fileId=f['id']).execute()
                content = json.loads(content_bytes.decode('utf-8'))
                content['datei'] = f['name']
                data_list.append(content)
            except:
                continue # Datei war kein JSON, ignorieren
                
        return data_list
    except Exception as e:
        st.error(f"Fehler beim Laden: {e}")
        return []

# --- APP START ---
st.title("🌲 Forst-Verwaltung (Cloud)")

tab1, tab2 = st.tabs(["📸 Neue Liste", "🗂️ Archiv"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except:
        st.stop()

    uploaded_file = st.file_uploader("Datei hochladen", type=["jpg", "png", "jpeg", "pdf"])

    # Wenn eine neue Datei kommt, Gedächtnis löschen
    if uploaded_file:
        if st.session_state.last_upload != uploaded_file.name:
            st.session_state.analyzed_data = None
            st.session_state.last_upload = uploaded_file.name

        # Anzeigen
        file_content_gemini = None
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF: {uploaded_file.name}")
            file_content_gemini = types.Part.from_bytes(data=uploaded_file.getvalue(), mime_type="application/pdf")
        else:
            img = Image.open(uploaded_file)
            st.image(img, width=400)
            file_content_gemini = img

        # ANALYSE BUTTON
        # Nur zeigen, wenn wir noch keine Daten haben
        if st.session_state.analyzed_data is None:
            if st.button("🚀 Jetzt Analysieren"):
                with st.spinner("Gemini arbeitet..."):
                    try:
                        prompt = """
                        Extrahiere Daten als JSON:
                        {
                            "meta": {"los": "String", "revier": "String", "datum": "String"},
                            "summen": {"fm": Float},
                            "polter": [{"nr": Int, "fm": Float, "lat": Float, "lon": Float}]
                        }
                        Rechne Koordinaten in Dezimalgrad um!
                        """
                        response = client.models.generate_content(
                            model="models/gemini-2.0-flash",
                            contents=[prompt, file_content_gemini],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        # Ergebnis im Gedächtnis speichern!
                        data = json.loads(response.text.replace("```json", "").replace("```", ""))
                        st.session_state.analyzed_data = data
                        st.rerun() # Seite neu laden, um Ergebnis anzuzeigen
                    except Exception as e:
                        st.error(f"Fehler: {e}")

    # ERGEBNIS ANZEIGEN & SPEICHERN
    # Das hier wird immer angezeigt, solange Daten im Gedächtnis sind
    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        st.success("Daten bereit zum Speichern!")
        st.dataframe(pd.DataFrame(data.get('polter', [])))
        
        # SPEICHER BUTTON
        if st.button("💾 In Google Drive speichern"):
            with st.spinner("Lade hoch..."):
                name = data.get('meta', {}).get('los', 'Holzliste')
                res = upload_to_drive(data, name)
                if res:
                    st.success(f"Gespeichert: {res}")
                    st.info("Gehe jetzt in Tab 2 'Archiv' und klicke Aktualisieren.")
                    # Optional: Gedächtnis löschen nach Speichern
                    # st.session_state.analyzed_data = None 

# --- TAB 2: ARCHIV ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    st.write("Lade aus Drive...")
    records = list_drive_files()
    
    if not records:
        st.warning("Noch nichts gefunden. Hast du in Tab 1 schon auf 'Speichern' geklickt?")
    else:
        # Tabelle
        overview = []
        for r in records:
            overview.append({
                "Datei": r.get('datei'),
                "Los": r.get('meta', {}).get('los'),
                "Menge": r.get('summen', {}).get('fm')
            })
        st.dataframe(pd.DataFrame(overview))
        
        # Karte
        points = []
        for r in records:
            for p in r.get('polter', []):
                if p.get('lat') and p.get('lon'):
                    points.append({
                        "lat": p['lat'], "lon": p['lon'],
                        "info": f"Los: {r.get('meta', {}).get('los')} (P{p.get('nr')})"
                    })
                    
        if points:
            df = pd.DataFrame(points)
            m = folium.Map(location=[df.lat.mean(), df.lon.mean()], zoom_start=12)
            for _, row in df.iterrows():
                folium.Marker([row['lat'], row['lon']], popup=row['info']).add_to(m)
            st_folium(m, width=800, height=500)
