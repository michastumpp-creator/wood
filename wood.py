import streamlit as st
from google import genai
from google.genai import types
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
from PIL import Image

# --- SEITEN KONFIGURATION ---
st.set_page_config(page_title="Forst-Koordinator", page_icon="🌲", layout="centered")

st.title("🌲 Forst-Koordinator")
st.write("System bereit (Paid Mode).")

# --- GEDÄCHTNIS INITIALISIEREN (Session State) ---
# Das verhindert die Endlosschleife!
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_uploaded_filename' not in st.session_state:
    st.session_state.last_uploaded_filename = ""

# --- API KEY LADEN ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    client = genai.Client(api_key=api_key)
except (FileNotFoundError, KeyError):
    st.error("⚠️ API Key fehlt in den Secrets.")
    st.stop()

# --- UPLOAD BEREICH ---
uploaded_file = st.file_uploader("Foto aufnehmen oder auswählen...", type=["jpg", "png", "jpeg"])

if uploaded_file is not None:
    # 1. PRÜFEN: Ist das ein NEUES Bild?
    # Wenn der Dateiname anders ist als beim letzten Mal, löschen wir das Gedächtnis.
    if uploaded_file.name != st.session_state.last_uploaded_filename:
        st.session_state.analyzed_data = None
        st.session_state.last_uploaded_filename = uploaded_file.name
    
    # Bild immer anzeigen
    image = Image.open(uploaded_file)
    st.image(image, caption="Aktuelles Foto", use_container_width=True)

    # 2. LOGIK: Nur API abfragen, wenn wir NOCH KEINE Daten im Gedächtnis haben
    if st.session_state.analyzed_data is None:
        
        with st.spinner('Gemini 2.0 analysiert einmalig...'):
            try:
                prompt = """
                Du bist ein Assistent für die Forstwirtschaft.
                Analysiere dieses Bild einer Tabelle.
                1. Suche alle Zeilen mit Koordinaten (Breite/Länge).
                2. WICHTIG: Rechne Koordinaten EXAKT in Dezimalgrad (Decimal Degrees) um.
                3. Gib mir NUR ein sauberes JSON zurück (Kein Markdown).
                
                Format:
                [
                  {"name": "Polter 1", "lat": 48.12345, "lon": 9.12345},
                  {"name": "Polter 2", "lat": 48.67890, "lon": 9.67890}
                ]
                """
                
                # API Abfrage (kostet Geld, daher nur einmal pro Bild!)
                response = client.models.generate_content(
                    model="models/gemini-2.0-flash",
                    contents=[prompt, image],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                if response.text:
                    clean_text = response.text.replace("```json", "").replace("```", "").strip()
                    data = json.loads(clean_text)
                    
                    if data:
                        # ERFOLG: Wir speichern das Ergebnis im Gedächtnis!
                        st.session_state.analyzed_data = pd.DataFrame(data)
                        st.success(f"✅ {len(data)} Standorte gefunden und gespeichert!")
                    else:
                        st.warning("Keine Daten im Bild gefunden.")
                
            except Exception as e:
                st.error(f"Fehler bei der Analyse: {e}")

    # 3. ANZEIGE: Wir zeigen Daten an (egal ob frisch geholt oder aus dem Gedächtnis)
    if st.session_state.analyzed_data is not None:
        df = st.session_state.analyzed_data
        
        # Karte bauen
        mid_lat = df['lat'].mean()
        mid_lon = df['lon'].mean()
        m = folium.Map(location=[mid_lat, mid_lon], zoom_start=14)
        
        for index, row in df.iterrows():
            nav_link = f"https://www.google.com/maps/search/?api=1&query={row['lat']},{row['lon']}"
            
            popup_html = f"""
            <div style="font-family: sans-serif; font-size: 14px; width: 150px;">
                <b>📍 {row.get('name', 'Holzpolter')}</b><br>
                <hr style="margin: 5px 0;">
                <a href="{nav_link}" target="_blank" 
                   style="background-color: #4CAF50; color: white; padding: 6px 12px; text-decoration: none; border-radius: 4px; display: inline-block;">
                   🚀 Navigation
                </a>
            </div>
            """
            
            folium.Marker(
                [row['lat'], row['lon']],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=row.get('name', 'Polter'),
                icon=folium.Icon(color="green", icon="tree", prefix='fa')
            ).add_to(m)

        # WICHTIG: Karte anzeigen
        # Da die Daten jetzt im Cache sind, wird die Karte beim Zoomen nicht neu berechnet!
        st_folium(m, width=700, height=500)
        
        with st.expander("Daten ansehen"):
            st.dataframe(df)

else:
    # Wenn kein Bild da ist, löschen wir das Gedächtnis für den nächsten Upload
    st.session_state.analyzed_data = None
    st.session_state.last_uploaded_filename = ""
