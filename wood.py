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
    if is_volume and val > 15.0: 
        if 0.5 < (val / 100) < 15: return val / 100
        if 0.5 < (val / 10) < 15: return val / 10
            
    return val

# --- HELFER: KOORDINATEN RETTEN ---
def fix_coordinates(lat, lon):
    l1 = clean_number(lat)
    l2 = clean_number(lon)
    if l1 == 0 and l2 == 0: return 0.0, 0.0

    if l1 > l2:
        return l1, l2
    else:
        return l2, l1 # Tausch

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

tab1, tab2 = st.tabs(["📸 Scan & Erfassung", "🗃️ Bestand"])

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
            if st.button("🚀 Analysieren & Prüfen"):
                with st.spinner("Gemini rechnet und prüft..."):
                    try:
                        # --- PROMPT MIT MATHE-AUFGABE ---
                        prompt = """
                        Analysiere diese Forst-Holzliste exakt.
                        
                        1. SUCHAUFTRAG "GESAMTMENGE":
                           Suche irgendwo im Dokument (meist Seite 1) nach der "Gesamtmenge FmoR" oder "Summe" oder gesamt.
                           Extrahiere diesen Wert als 'dokument_summe'.
                        
                        2. SUCHAUFTRAG "POLTER & STÄMME":
                           - Polter (Nummer, Fm, GPS).
                           - Einzelstämme (aus Tabelle "Zusammenstellung nach Waldnummern").
                           
                        3. ZAHLEN-REGELN:
                           - "1,370" = 1.37
                           - "47,64" = 47.64
                        
                        JSON STRUKTUR:
                        {
                            "meta": {"los": "String", "revier": "String", "datum": "String", "dokument_summe": Float},
                            "polter": [{"nr": Int, "fm": Float, "lat": Float, "lon": Float}],
                            "staemme": [{"wnr": "String", "art": "String", "l": Float, "d": Float, "klasse": "String", "fm": Float}]
                        }
                        """
                        response = client.models.generate_content(
                            model="gemini-3-pro-preview",
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
        
        # --- DER MATHE-CHECK ---
        st.divider()
        st.subheader("🕵️ Plausibilitäts-Check")
        
        # 1. Was steht auf dem Papier?
        doc_sum = clean_number(data.get('meta', {}).get('dokument_summe', 0))
        
        # 2. Was haben wir gefunden (Summe der Polter)?
        polter_list = data.get('polter', [])
        calc_sum = sum([clean_number(p.get('fm', 0)) for p in polter_list])
        
        # 3. Differenz berechnen
        diff = abs(doc_sum - calc_sum)
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Soll (lt. Dokument)", f"{doc_sum:.2f} Fm")
        c2.metric("Ist (Summe Polter)", f"{calc_sum:.2f} Fm")
        
        # Anzeige der Ampel
        if doc_sum > 0:
            if diff < 0.5: # Toleranz 0.5 Fm
                c3.success(f"✅ Stimmt! (Diff: {diff:.2f})")
            else:
                c3.error(f"⚠️ Abweichung! (Diff: {diff:.2f})")
                st.warning("Achtung: Die Summe der erkannten Polter passt nicht zur Gesamtsumme auf dem Papier. Prüfe, ob ein Polter fehlt oder eine Zahl falsch gelesen wurde (z.B. 137 statt 1.37).")
        else:
            c3.info("❓ Keine Gesamtsumme im Text gefunden.")

        # --- NORMALE ANZEIGE ---
        st.divider()
        c_a, c_b = st.columns(2)
        c_a.write(f"**Gefundene Polter:** {len(polter_list)}")
        c_b.write(f"**Gefundene Stämme:** {len(data.get('staemme', []))}")
        
        with st.expander("Details ansehen"):
            st.dataframe(pd.DataFrame(data.get('polter', [])))
            st.dataframe(pd.DataFrame(data.get('staemme', [])))

        if st.button("💾 Speichern"):
            with st.spinner("Speichere..."):
                if save_to_sheets(data):
                    st.success("Gespeichert!")
                    st.session_state.analyzed_data = None
                    st.info("Wechsle jetzt zu 'Bestand'.")

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    if df_polter.empty:
        st.info("Keine Daten.")
    else:
        # KARTE
        st.subheader("🗺️ Gesamtkarte")
        valid_pts = []
        for _, row in df_polter.iterrows():
            try:
                lat, lon = fix_coordinates(row.get('Lat',0), row.get('Lon',0))
                if lat != 0:
                    valid_pts.append({"lat": lat, "lon": lon, "info": f"Revier {row['Revier']} | P{row['Polter_Nr']}"})
            except: pass
            
        if valid_pts:
            map_df = pd.DataFrame(valid_pts)
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=11)
            for _, pt in map_df.iterrows():
                folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width="100%", height=350)
        
        st.divider()
        st.subheader("📂 Reviere & Lose")
        
        if 'Los_Nr' in df_polter.columns and 'Revier' in df_polter.columns:
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                total_fm = group['Menge_Fm'].apply(clean_number).sum()
                
                label = f"🌲 Revier {revier} | Los {los} | 📅 {datum} | 📦 {total_fm:.2f} Fm"
                
                with st.expander(label):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("### 🚜 Polter")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                        
                    with c2:
                        st.markdown("### 🪵 Einzelstämme")
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
