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
DATA_FOLDER = "holzlisten_db" # Unser "Ordner-Datenbank"
os.makedirs(DATA_FOLDER, exist_ok=True) # Ordner erstellen, falls nicht da

# --- FUNKTIONEN ---
def save_to_json(data, filename_base):
    """Speichert die analysierten Daten als JSON Datei"""
    # Timestamp hinzufügen damit man nichts überschreibt
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_base}_{timestamp}.json"
    filepath = os.path.join(DATA_FOLDER, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return filename

def load_all_lists():
    """Lädt alle gespeicherten JSON Dateien"""
    files = glob.glob(os.path.join(DATA_FOLDER, "*.json"))
    all_data = []
    for f in files:
        with open(f, 'r', encoding='utf-8') as file:
            try:
                content = json.load(file)
                # Wir fügen den Dateinamen als Referenz hinzu
                content['datei'] = os.path.basename(f)
                all_data.append(content)
            except:
                pass
    return all_data

# --- APP START ---
st.title("🌲 Forst-Verwaltung (No-SQL)")

# Wir nutzen Tabs für die Übersicht
tab1, tab2 = st.tabs(["📸 Neue Liste scannen", "🗂️ Archiv & Verwaltung"])

# --- TAB 1: SCANNER (Dein bestehender Code + Speicher-Button) ---
with tab1:
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        client = genai.Client(api_key=api_key)
    except:
        st.error("API Key fehlt.")
        st.stop()

    uploaded_file = st.file_uploader("Foto/PDF hochladen", type=["jpg", "png", "jpeg", "pdf"])

    if uploaded_file:
        image = Image.open(uploaded_file) # (Bei PDF bräuchte man PDF-Konverter, hier vereinfacht für Bild)
        st.image(image, caption="Vorschau", width=400)
        
        if st.button("Jetzt analysieren"):
            with st.spinner('Gemini extrahiert Daten...'):
                prompt = """
                Extrahiere Daten aus dieser Holzliste.
                Erstelle ein JSON mit folgender Struktur:
                {
                    "los_nummer": "String (z.B. 915/2025...)",
                    "revier": "String",
                    "datum": "String",
                    "polter": [
                        {"nr": "Int", "fm": "Float", "lat": "Float", "lon": "Float"}
                    ],
                    "summe_fm": "Float"
                }
                WICHTIG: Rechne Koordinaten in Dezimalgrad um!
                """
                
                try:
                    response = client.models.generate_content(
                        model="models/gemini-2.0-flash",
                        contents=[prompt, image],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    
                    data = json.loads(response.text.replace("```json", "").replace("```", ""))
                    st.success("Daten erkannt!")
                    
                    # Vorschau Tabelle
                    df_polter = pd.DataFrame(data.get('polter', []))
                    st.dataframe(df_polter)
                    
                    # SPEICHER BUTTON
                    if st.button("💾 In Akte speichern"):
                        saved_name = save_to_json(data, "Holzliste")
                        st.success(f"Gespeichert unter: {saved_name}")
                        
                except Exception as e:
                    st.error(f"Fehler: {e}")

# --- TAB 2: VERWALTUNG (Das Dashboard) ---
with tab2:
    st.header("🗂️ Gespeicherte Listen")
    
    all_records = load_all_lists()
    
    if not all_records:
        st.info("Noch keine Listen gespeichert.")
    else:
        # Übersichtstabelle bauen
        overview_data = []
        for r in all_records:
            overview_data.append({
                "Datei": r.get('datei'),
                "Los": r.get('los_nummer', 'Unbekannt'),
                "Datum": r.get('datum', '-'),
                "Menge (Fm)": r.get('summe_fm', 0),
                "Revier": r.get('revier', '-')
            })
            
        df_overview = pd.DataFrame(overview_data)
        st.dataframe(df_overview, use_container_width=True)
        
        # Detail-Ansicht und Gesamt-Karte
        st.subheader("📍 Gesamtkarte aller Bestände")
        
        # Wir sammeln ALLE Polter aus ALLEN Dateien für eine große Karte
        all_polter_coords = []
        for r in all_records:
            for p in r.get('polter', []):
                # Prüfen ob Koordinaten da sind
                if 'lat' in p and 'lon' in p:
                    all_polter_coords.append({
                        "lat": p['lat'],
                        "lon": p['lon'],
                        "info": f"Los: {r.get('los_nummer')} | Polter {p.get('nr')}"
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
            
        # Export Funktion
        if st.button("📊 Export als Excel (Alle Daten)"):
            # Hier könnte man df_overview zu Excel konvertieren
            st.info("Export-Funktion hier einfügen...")
