import streamlit as st
from google import genai
from google.genai import types
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
from PIL import Image
import os
import glob
from datetime import datetime

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")
DATA_FOLDER = "holzlisten_db"
os.makedirs(DATA_FOLDER, exist_ok=True)

# --- FUNKTIONEN ---
def save_to_json(data, filename_base):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Dateinamen bereinigen
    clean_base = "".join([c for c in filename_base if c.isalnum() or c in (' ', '-', '_')]).rstrip()
    filename = f"{clean_base}_{timestamp}.json"
    filepath = os.path.join(DATA_FOLDER, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return filename

def load_all_lists():
    files = glob.glob(os.path.join(DATA_FOLDER, "*.json"))
    all_data = []
    for f in files:
        with open(f, 'r', encoding='utf-8') as file:
            try:
                content = json.load(file)
                content['datei'] = os.path.basename(f)
                all_data.append(content)
            except:
                pass
    return all_data

# --- APP START ---
st.title("🌲 Forst-Verwaltung")

tab1, tab2 = st.tabs(["📸 Neue Liste scannen", "🗂️ Archiv & Verwaltung"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        client = genai.Client(api_key=api_key)
    except:
        st.error("API Key fehlt in Secrets.")
        st.stop()

    uploaded_file = st.file_uploader("Foto oder PDF hochladen", type=["jpg", "png", "jpeg", "pdf"])

    if uploaded_file:
        # --- HIER IST DER FIX FÜR PDFS ---
        file_content_for_gemini = None
        
        # Fallunterscheidung: PDF oder Bild?
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF geladen: {uploaded_file.name}")
            # Wir lesen das PDF als Bytes ein
            file_bytes = uploaded_file.getvalue()
            file_content_for_gemini = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")
        else:
            # Es ist ein Bild
            image = Image.open(uploaded_file)
            st.image(image, caption="Vorschau", width=400)
            file_content_for_gemini = image

        # Analyse Button
        if st.button("Jetzt analysieren"):
            with st.spinner('Gemini 2.0 liest die Holzliste...'):
                
                # Angepasster Prompt für deine komplexe Liste
                prompt = """
                Du bist ein Forst-Assistent. Analysiere dieses Dokument (Holzliste).
                
                Extrahiere folgende Daten in ein JSON-Objekt:
                1. 'meta': Los-Nummer (oft zusammengesetzt z.B. 915/2025...), Revier, Datum.
                2. 'summen': Gesamtmenge (Fm) und Gesamtpreis.
                3. 'polter': Eine Liste aller Polter. WICHTIG: Suche nach Koordinaten (DMS Format wie 48°17'...).
                   Rechne diese Koordinaten UNBEDINGT in Dezimalgrad (lat/lon) um!
                   (Beispiel: 48°30'00" -> 48.5000).
                
                Struktur des JSONs:
                {
                    "meta": {"los": "String", "revier": "String", "datum": "String"},
                    "summen": {"fm": Float, "preis": Float},
                    "polter": [
                        {"nr": Int, "fm": Float, "lat": Float, "lon": Float, "ort": "String"}
                    ]
                }
                Gib NUR das JSON zurück.
                """
                
                try:
                    # Anfrage an Gemini
                    response = client.models.generate_content(
                        model="models/gemini-2.0-flash",
                        contents=[prompt, file_content_for_gemini],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    
                    if response.text:
                        clean_text = response.text.replace("```json", "").replace("```", "").strip()
                        data = json.loads(clean_text)
                        
                        st.success("Analyse erfolgreich!")
                        
                        # Vorschau der gefundenen Polter
                        if 'polter' in data and data['polter']:
                            st.write(f"Gefundene Polter: {len(data['polter'])}")
                            st.dataframe(pd.DataFrame(data['polter']))
                        else:
                            st.warning("Keine Polter mit Koordinaten gefunden.")
                        
                        # Speicher-Button (Session State Logik um mehrfaches Speichern zu verhindern wäre hier gut, aber wir halten es simpel)
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("💾 Speichern & Archivieren"):
                                # Wir nehmen die Los-Nummer als Dateinamen
                                filename_base = data.get('meta', {}).get('los', 'Holzliste')
                                saved_name = save_to_json(data, filename_base)
                                st.balloons()
                                st.success(f"Gespeichert als: {saved_name}")
                    else:
                        st.error("Leere Antwort von der KI.")
                        
                except Exception as e:
                    st.error(f"Fehler bei der Verarbeitung: {e}")

# --- TAB 2: VERWALTUNG ---
with tab2:
    st.header("🗂️ Aktenübersicht")
    
    all_records = load_all_lists()
    
    if not all_records:
        st.info("Noch keine Listen gespeichert. Gehe zu Tab 1.")
    else:
        # Übersichtstabelle
        overview_data = []
        for r in all_records:
            meta = r.get('meta', {})
            sums = r.get('summen', {})
            overview_data.append({
                "Datei": r.get('datei'),
                "Los": meta.get('los', '-'),
                "Revier": meta.get('revier', '-'),
                "Menge (Fm)": sums.get('fm', 0),
                "Preis (€)": sums.get('preis', 0)
            })
            
        st.dataframe(pd.DataFrame(overview_data), use_container_width=True)
        
        # --- GESAMTKARTE ---
        st.subheader("📍 Karte aller Polter")
        
        all_polter_coords = []
        for r in all_records:
            los_name = r.get('meta', {}).get('los', 'Unbekannt')
            for p in r.get('polter', []):
                if 'lat' in p and 'lon' in p and p['lat'] is not None:
                    all_polter_coords.append({
                        "lat": p['lat'],
                        "lon": p['lon'],
                        "info": f"<b>Los: {los_name}</b><br>Polter {p.get('nr')}<br>{p.get('fm')} Fm",
                        "fm": p.get('fm', 0)
                    })
        
        if all_polter_coords:
            df_map = pd.DataFrame(all_polter_coords)
            mid_lat = df_map['lat'].mean()
            mid_lon = df_map['lon'].mean()
            
            m = folium.Map(location=[mid_lat, mid_lon], zoom_start=11)
            
            for _, row in df_map.iterrows():
                folium.Marker(
                    [row['lat'], row['lon']],
                    popup=row['info'],
                    icon=folium.Icon(color="green", icon="tree", prefix='fa')
                ).add_to(m)
            
            st_folium(m, width=800, height=500)
        else:
            st.info("Noch keine Polter mit GPS-Daten im Archiv.")
