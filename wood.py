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
import re

# --- KONFIGURATION ---
st.set_page_config(page_title="Forst-Manager", page_icon="🌲", layout="wide")

# --- SESSION STATE ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'last_upload' not in st.session_state:
    st.session_state.last_upload = None

# --- HELFER: ZAHLEN RETTEN ---
def clean_number(value, is_volume=False):
    """
    Reinigt Zahlen und fängt Logikfehler ab.
    z.B. 1,370 -> 1.37
    """
    if isinstance(value, (int, float)):
        val = float(value)
    elif isinstance(value, str):
        # 1. Komma zu Punkt
        clean = value.replace(',', '.')
        # 2. Alles weg was keine Zahl/Punkt ist
        clean = re.sub(r'[^\d.]', '', clean)
        try:
            val = float(clean)
        except:
            return 0.0
    else:
        return 0.0

    # PLAUSIBILITÄTS-CHECK FÜR EINZELSTÄMME
    # Ein einzelner Baumstamm in Deutschland hat selten > 10 Festmeter.
    # Wenn wir 137.0 erhalten, war es wahrscheinlich 1.37
    if is_volume and val > 15.0: 
        # Versuch: Teile durch 100 (z.B. 137 -> 1.37)
        if 0.5 < (val / 100) < 15:
            return val / 100
        # Versuch: Teile durch 10 (z.B. 13.7 -> 1.37 - eher selten, aber möglich)
        if 0.5 < (val / 10) < 15:
            return val / 10
            
    return val

# --- HELFER: KOORDINATEN RETTEN ---
def fix_coordinates(lat, lon):
    """
    Stellt sicher, dass Lat/Lon in Deutschland liegen.
    Deutschland ca: Lat 47-55, Lon 5-15
    """
    l1 = clean_number(lat)
    l2 = clean_number(lon)
    
    # Wenn beides 0 ist, abbrechen
    if l1 == 0 and l2 == 0: return 0.0, 0.0

    final_lat = 0.0
    final_lon = 0.0

    # Logik: Welcher Wert ist welcher?
    # Latitude (Breite) in DE ist immer größer (ca 48-52) als Longitude (ca 8-12)
    if l1 > l2:
        final_lat = l1
        final_lon = l2
    else:
        final_lat = l2 # Tausch
        final_lon = l1
        
    return final_lat, final_lon

# --- GOOGLE SHEETS VERBINDUNG ---
def get_spreadsheet():
    if "gcp_service_account" not in st.secrets:
        st.error("Secrets fehlen!")
        return None
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        client = gspread.authorize(creds)
        return client.open("Forst_Datenbank")
    except Exception as e:
        st.error(f"Fehler beim Öffnen der Tabelle: {e}")
        return None

def save_to_sheets(data):
    sh = get_spreadsheet()
    if not sh: return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    if isinstance(data, list): data = {"polter": data, "meta": {}, "staemme": []}

    meta = data.get('meta', {})
    los = str(meta.get('los', 'Unbekannt'))
    revier = str(meta.get('revier', 'Unbekannt'))
    datum_aufnahme = str(meta.get('datum', timestamp.split(' ')[0]))

    # --- BLATT 1: POLTER ---
    try:
        ws_polter = sh.worksheet("Polter_Uebersicht")
    except:
        ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=10)
        ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link"])

    polter_rows = []
    for p in data.get('polter', []):
        fm = clean_number(p.get('fm', 0))
        # Koordinaten prüfen und tauschen falls nötig
        raw_lat = p.get('lat', 0)
        raw_lon = p.get('lon', 0)
        lat, lon = fix_coordinates(raw_lat, raw_lon)
        
        link = f"http://maps.google.com/?q={lat},{lon}" if lat != 0 else ""
        polter_rows.append([timestamp, datum_aufnahme, los, revier, p.get('nr'), fm, str(lat), str(lon), link])
    
    if polter_rows:
        ws_polter.append_rows(polter_rows)

    # --- BLATT 2: EINZELSTÄMME ---
    staemme_data = data.get('staemme', [])
    if staemme_data:
        try:
            ws_stamm = sh.worksheet("Einzelstaemme")
        except:
            ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10)
            ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])

        stamm_rows = []
        for s in staemme_data:
            l = clean_number(s.get('l', 0))
            d = clean_number(s.get('d', 0))
            # HIER GREIFT DER ZAHLEN-FIX (is_volume=True)
            fm = clean_number(s.get('fm', 0), is_volume=True)
            
            stamm_rows.append([timestamp, los, revier, s.get('wnr', ''), s.get('art', ''), l, d, s.get('klasse', ''), fm])
        
        if stamm_rows:
            ws_stamm.append_rows(stamm_rows)

    return True

def load_data_frames():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame(), pd.DataFrame()
    try:
        data_p = sh.worksheet("Polter_Uebersicht").get_all_records()
        df_polter = pd.DataFrame(data_p)
    except: df_polter = pd.DataFrame()
    try:
        data_s = sh.worksheet("Einzelstaemme").get_all_records()
        df_staemme = pd.DataFrame(data_s)
    except: df_staemme = pd.DataFrame()
    return df_polter, df_staemme

# --- APP START ---
st.title("🌲 Forst-Verwaltung")

tab1, tab2 = st.tabs(["📸 Scan & Erfassung", "🗃️ Bestand (Buttons)"])

# --- TAB 1: SCANNER ---
with tab1:
    try:
        client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except: st.stop()

    uploaded_file = st.file_uploader("Holzliste (PDF)", type=["pdf", "jpg", "png"])

    if uploaded_file:
        if st.session_state.last_upload != uploaded_file.name:
            st.session_state.analyzed_data = None
            st.session_state.last_upload = uploaded_file.name

        content = None
        if uploaded_file.type == "application/pdf":
            st.info(f"📄 PDF: {uploaded_file.name}")
            content = types.Part.from_bytes(data=uploaded_file.getvalue(), mime_type="application/pdf")
        else:
            img = Image.open(uploaded_file)
            st.image(img, width=400)
            content = img

        if st.session_state.analyzed_data is None:
            if st.button("🚀 Analysieren"):
                with st.spinner("Gemini arbeitet (Zahlen & GPS Fix)..."):
                    try:
                        prompt = """
                        Analysiere diese Holzliste für den deutschen Forst.
                        
                        WICHTIGSTE REGELN:
                        1. ZAHLEN: "1,370" bedeutet "EINS KOMMA DREI SIEBEN" (1.37). Das ist NICHT Tausend! Ignoriere Tausendertrennzeichen. Ausgabe immer mit Punkt (1.37).
                        2. KOORDINATEN: Suche Lat (ca 47-54°) und Lon (ca 6-15°). Rechne DMS (Grad,Min,Sek) EXAKT in Dezimalgrad um.
                        
                        EXTRAHIERE:
                        - meta: Los, Revier, Datum
                        - polter: Liste mit GPS
                        - staemme: Liste mit WNr, Länge, Durchmesser, Güte, Volumen(Fm)
                        
                        JSON STRUKTUR:
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
                        raw = json.loads(clean_text)
                        data = {"polter": raw, "meta": {}, "staemme": []} if isinstance(raw, list) else raw
                        
                        st.session_state.analyzed_data = data
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler: {e}")

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        c1, c2 = st.columns(2)
        c1.metric("Polter", len(data.get('polter', [])))
        c2.metric("Stämme", len(data.get('staemme', [])))
        
        st.write("Vorschau (Erste 5 Stämme):")
        st.dataframe(pd.DataFrame(data.get('staemme', [])).head(5))

        if st.button("💾 In Google Tabelle speichern"):
            with st.spinner("Speichere..."):
                if save_to_sheets(data):
                    st.success("Gespeichert!")
                    st.session_state.analyzed_data = None
                    st.info("Wechsle jetzt zu 'Bestand'.")

# --- TAB 2: BESTAND (BUTTON DESIGN) ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    if df_polter.empty:
        st.info("Keine Daten.")
    else:
        # KARTE (Oben fixiert)
        st.subheader("🗺️ Gesamtkarte")
        valid_pts = []
        for _, row in df_polter.iterrows():
            try:
                # Koordinaten reparieren beim Laden
                lat, lon = fix_coordinates(row.get('Lat',0), row.get('Lon',0))
                if lat != 0:
                    valid_pts.append({"lat": lat, "lon": lon, "info": f"Revier {row['Revier']} | P{row['Polter_Nr']}"})
            except: pass
            
        if valid_pts:
            map_df = pd.DataFrame(valid_pts)
            # Mittelpunkt DE falls leer, sonst Daten-Mittelpunkt
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=11)
            for _, pt in map_df.iterrows():
                folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width="100%", height=350)
        
        st.divider()
        st.subheader("📂 Reviere & Lose")
        
        # GRUPPIERUNG FÜR BUTTONS
        if 'Los_Nr' in df_polter.columns and 'Revier' in df_polter.columns:
            # Erstelle eine eindeutige Gruppe
            # Wir nehmen das Datum aus der Polter-Tabelle
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                total_fm = group['Menge_Fm'].apply(clean_number).sum()
                
                # DER BUTTON (Expander)
                label = f"🌲 Revier {revier} | Los {los} | 📅 {datum} | 📦 {total_fm:.2f} Fm"
                
                with st.expander(label):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("### 🚜 Polter")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                        
                    with c2:
                        st.markdown("### 🪵 Einzelstämme")
                        # Passende Stämme filtern
                        if not df_staemme.empty and 'Los_Nr' in df_staemme.columns:
                            stamm_match = df_staemme[
                                (df_staemme['Los_Nr'].astype(str) == str(los)) & 
                                (df_staemme['Revier'].astype(str) == str(revier))
                            ]
                            if not stamm_match.empty:
                                vol = stamm_match['Volumen_Fm'].apply(lambda x: clean_number(x, True)).sum()
                                st.write(f"Summe: {vol:.2f} Fm ({len(stamm_match)} Stk)")
                                st.dataframe(stamm_match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                            else:
                                st.warning("Keine Einzelstämme erfasst.")
