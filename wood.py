import streamlit as st
from google import genai
from google.genai import types
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
from PIL import Image

# --- Konfiguration der Seite ---
st.set_page_config(page_title="Forst-Koordinator", page_icon="🌲", layout="centered")

st.title("🌲 Forst-Koordinator")
st.write("Lade ein Foto der Tabelle hoch. Das System liest die Koordinaten und zeigt sie auf der Karte.")

# --- API Key Eingabe ---
api_key_input = st.text_input("Gib deinen API Key ein:", type="password")

if api_key_input:
    # Leerzeichen entfernen (passiert oft beim Kopieren)
    api_key = api_key_input.strip()
    
    # Client starten
    client = genai.Client(api_key=api_key)
    
    uploaded_file = st.file_uploader("Foto auswählen...", type=["jpg", "png", "jpeg"])

    if uploaded_file is not None:
        try:
            image = Image.open(uploaded_file)
            st.image(image, caption="Dein Foto", use_container_width=True)
            
            with st.spinner('Gemini 2.0 analysiert die Holzliste...'):
                
                prompt = """
                Du bist ein Assistent für die Forstwirtschaft.
                Analysiere dieses Bild einer Tabelle.
                1. Suche alle Zeilen mit Koordinaten (Breite/Länge).
                2. Die Koordinaten sind meistens im Format Grad°Minuten'Sekunden" (DMS).
                3. WICHTIG: Rechne diese Koordinaten EXAKT in Dezimalgrad (Decimal Degrees) um.
                4. Gib mir NUR ein JSON zurück. Keine Markdown-Formatierung (kein ```json).
                
                Das JSON muss exakt so aussehen:
                [
                  {"name": "Polter 1", "lat": 48.12345, "lon": 9.12345},
                  {"name": "Polter 2", "lat": 48.67890, "lon": 9.67890}
                ]
                """
                
                # HIER IST DIE ÄNDERUNG: Wir nutzen dein verfügbares Modell
                response = client.models.generate_content(
                    model="models/gemini-flash-latest",
                    contents=[prompt, image],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                if response.text:
                    # JSON bereinigen (falls doch Markdown dabei ist)
                    clean_text = response.text.replace("```json", "").replace("```", "").strip()
                    data = json.loads(clean_text)
                    
                    if data:
                        df = pd.DataFrame(data)
                        st.success(f"{len(data)} Standorte gefunden!")
                        
                        # Karte zentrieren
                        mid_lat = df['lat'].mean()
                        mid_lon = df['lon'].mean()
                        m = folium.Map(location=[mid_lat, mid_lon], zoom_start=14)
                        
                        # Marker setzen
                        for index, row in df.iterrows():
                            # Google Maps Link
                            gmaps_link = f"[https://www.google.com/maps/search/?api=1&query=](https://www.google.com/maps/search/?api=1&query=){row['lat']},{row['lon']}"
                            
                            popup_html = f"""
                            <div style="font-family: sans-serif; font-size: 14px;">
                                <b>{row.get('name', 'Holzpolter')}</b><br>
                                <a href="{gmaps_link}" target="_blank" style="color: blue; text-decoration: none;">
                                ➤ Navigation starten
                                </a>
                            </div>
                            """
                            folium.Marker(
                                [row['lat'], row['lon']],
                                popup=folium.Popup(popup_html, max_width=300),
                                icon=folium.Icon(color="green", icon="tree", prefix='fa')
                            ).add_to(m)

                        st_folium(m, width=700, height=500)
                        
                        # Tabelle anzeigen
                        st.write("Gefundene Daten:")
                        st.dataframe(df)
                    else:
                        st.error("Konnte keine gültigen Daten extrahieren.")
                else:
                    st.error("Keine Antwort vom Server.")
                    
        except Exception as e:
            st.error(f"Fehler: {e}")

else:
    st.info("Bitte gib oben deinen Schlüssel ein, um zu starten.")
