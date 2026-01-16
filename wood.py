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
st.write("Lade ein Foto der Holzliste hoch. Die App findet den Weg.")

# --- API KEY AUTOMATISCH LADEN ---
# Wir holen den Schlüssel sicher aus den Streamlit Secrets (Cloud)
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except (FileNotFoundError, KeyError):
    # Falls der Key fehlt (z.B. beim ersten Start), zeigen wir eine Hilfe an
    st.error("⚠️ API Key fehlt!")
    st.info("""
    So richtest du es ein:
    1. Gehe in deine App auf share.streamlit.io
    2. Klicke auf 'Settings' -> 'Secrets'
    3. Füge ein: GOOGLE_API_KEY = "Dein-AIza-Schlüssel"
    """)
    st.stop() # Stoppt die App hier, bis der Key da ist

# Client starten (mit dem geheimen Key)
client = genai.Client(api_key=api_key)

# --- UPLOAD BEREICH ---
uploaded_file = st.file_uploader("Foto aufnehmen oder auswählen...", type=["jpg", "png", "jpeg"])

if uploaded_file is not None:
    try:
        # Bild anzeigen
        image = Image.open(uploaded_file)
        st.image(image, caption="Dein Foto", use_container_width=True)
        
        with st.spinner('Gemini wertet die Koordinaten aus...'):
            
            # --- PROMPT AN DIE KI ---
            prompt = """
            Du bist ein Assistent für die Forstwirtschaft.
            Analysiere dieses Bild einer Tabelle.
            
            DEINE AUFGABE:
            1. Suche alle Zeilen mit Koordinaten (Breite/Länge).
            2. Die Koordinaten sind meistens im Format Grad°Minuten'Sekunden" (DMS).
            3. WICHTIG: Rechne diese Koordinaten EXAKT in Dezimalgrad (Decimal Degrees) um.
            4. Extrahiere auch Namen oder Nummern (z.B. Polter-Nr, Abteilung), falls vorhanden.
            
            FORMATIERUNG:
            Gib mir NUR ein sauberes JSON zurück. 
            Kein Markdown, kein Text davor oder danach.
            
            Das JSON muss exakt so aussehen:
            [
              {"name": "Polter 1 (oder Nummer)", "lat": 48.12345, "lon": 9.12345},
              {"name": "Polter 2", "lat": 48.67890, "lon": 9.67890}
            ]
            """
            
            # --- API ABFRAGE ---
            # Wir nutzen 'gemini-flash-latest' wegen dem hohen Limit
            response = client.models.generate_content(
                model="models/gemini-flash-latest",
                contents=[prompt, image],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            # --- DATEN VERARBEITEN ---
            if response.text:
                # Bereinigen falls Markdown ```json enthalten ist
                clean_text = response.text.replace("```json", "").replace("```", "").strip()
                data = json.loads(clean_text)
                
                if data:
                    df = pd.DataFrame(data)
                    st.success(f"✅ {len(data)} Standorte gefunden!")
                    
                    # Karte zentrieren (Mittelpunkt aller Punkte)
                    mid_lat = df['lat'].mean()
                    mid_lon = df['lon'].mean()
                    m = folium.Map(location=[mid_lat, mid_lon], zoom_start=14)
                    
                    # Marker auf die Karte setzen
                    for index, row in df.iterrows():
                        # Navigations-Link für Android/Google Maps
                        nav_link = f"[https://www.google.com/maps/search/?api=1&query=](https://www.google.com/maps/search/?api=1&query=){row['lat']},{row['lon']}"
                        
                        # Popup Design
                        popup_html = f"""
                        <div style="font-family: sans-serif; font-size: 14px; width: 150px;">
                            <b>📍 {row.get('name', 'Holzpolter')}</b><br>
                            <hr style="margin: 5px 0;">
                            <a href="{nav_link}" target="_blank" 
                               style="background-color: #4CAF50; color: white; padding: 6px 12px; text-decoration: none; border-radius: 4px; display: inline-block;">
                               🚀 Navigation starten
                            </a>
                        </div>
                        """
                        
                        folium.Marker(
                            [row['lat'], row['lon']],
                            popup=folium.Popup(popup_html, max_width=300),
                            tooltip=row.get('name', 'Polter'),
                            icon=folium.Icon(color="green", icon="tree", prefix='fa')
                        ).add_to(m)

                    # Karte anzeigen
                    st_folium(m, width=700, height=500)
                    
                    # Tabelle darunter anzeigen
                    with st.expander("Genaue Daten ansehen"):
                        st.dataframe(df)
                        
                else:
                    st.warning("Die KI konnte keine Koordinaten im Bild lesen. War das Foto scharf?")
            else:
                st.error("Keine Antwort vom Google Server erhalten.")
                
    except Exception as e:
        st.error(f"Ein Fehler ist aufgetreten: {e}")

else:
    # Kleiner Hinweis wenn noch kein Bild da ist
    st.info("Bitte oben ein Foto hochladen.")
