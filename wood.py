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
    """Reinigt normale Zahlen."""
    if isinstance(value, (int, float)):
        val = float(value)
    elif isinstance(value, str):
        clean = value.replace(',', '.')
        clean = re.sub(r'[^\d.]', '', clean)
        try:
            val = float(clean)
        except:
            return 0.0
    else:
        return 0.0

    # Volumen-Plausibilität
    if is_volume and val > 20.0:
        if 0.5 < (val / 100) < 20: return val / 100
        if 0.5 < (val / 10) < 20: return val / 10
    return val

# --- HELFER: GPS MATHE-GENIE (DMS -> DEZIMAL) ---
def parse_dms_to_decimal(val):
    """
    Wandelt "48°17'06,71" korrekt um.
    """
    if isinstance(val, (int, float)):
        return float(val)

    val_str = str(val).strip()
    
    # Regex für Grad, Minuten, Sekunden
    matches = re.findall(r'(\d+)[^\d]+(\d+)[^\d]+(\d+[,.]\d+)', val_str)
    
    if matches:
        try:
            d = float(matches[0][0])
            m = float(matches[0][1])
            s = float(matches[0][2].replace(',', '.'))
            return d + (m / 60.0) + (s / 3600.0)
        except:
            pass
            
    # Fallback: Versuche es als normale Zahl zu lesen
    return clean_number(val)

def fix_coordinates(lat, lon):
    """
    Zwingt Koordinaten in den deutschen Raum (Anti-Mongolei-Funktion).
    """
    # 1. Erstmal sauber parsen (auch DMS)
    l1 = parse_dms_to_decimal(lat)
    l2 = parse_dms_to_decimal(lon)

    if l1 == 0 and l2 == 0: return 0.0, 0.0

    # 2. Skalieren (Millionen-Zahlen runterbrechen)
    # Solange eine Zahl > 180 ist, ist sie keine Koordinate -> Teilen!
    def scale_down(v):
        if v == 0: return 0
        while v > 180:
            v /= 10.0
        return v
    
    l1 = scale_down(l1)
    l2 = scale_down(l2)

    # 3. ZUORDNUNG (Wer ist Latitude, wer Longitude?)
    # Deutschland:
    # Latitude (Breite/Hochwert)  ~ 47.0 bis 55.0
    # Longitude (Länge/Rechtswert) ~ 6.0 bis 15.0
    
    final_lat = 0.0
    final_lon = 0.0
    
    # Check l1
    is_l1_lat = (47 <= l1 <= 55)
    is_l1_lon = (5 <= l1 <= 15)
    
    # Check l2
    is_l2_lat = (47 <= l2 <= 55)
    is_l2_lon = (5 <= l2 <= 15)
    
    # Logik-Puzzle:
    if is_l1_lat and is_l2_lon:
        final_lat, final_lon = l1, l2
    elif is_l2_lat and is_l1_lon:
        final_lat, final_lon = l2, l1 # Tausch
    else:
        # Notfall-Plan (Mongolei-Fix):
        # Wenn eine Zahl ~48 ist und die andere ~92 (Mongolei),
        # dann ist die 92 wahrscheinlich eine falsch gelesene 9.2!
        
        # Wir suchen den Wert, der nahe 48 ist -> Das ist Lat
        if abs(l1 - 48) < abs(l2 - 48):
            final_lat = l1
            final_lon = l2
        else:
            final_lat = l2
            final_lon = l1
            
        # Wenn Longitude immer noch > 15 (z.B. 92.0), teilen wir sie weiter
        while final_lon > 15.0:
            final_lon /= 10.0
            
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

# --- DATEN SPEICHERN ---
def save_to_sheets(data):
    sh = get_spreadsheet()
    if not sh: return False
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if isinstance(data, list): data = {"polter": data, "meta": {}, "staemme": []}

    meta = data.get('meta', {})
    los = str(meta.get('los', 'Unbekannt'))
    revier = str(meta.get('revier', 'Unbekannt'))
    datum_aufnahme = str(meta.get('datum', timestamp.split(' ')[0]))

    def to_german(val): return str(val).replace('.', ',')

    # BLATT 1: POLTER
    try: ws_polter = sh.worksheet("Polter_Uebersicht")
    except: ws_polter = sh.add_worksheet(title="Polter_Uebersicht", rows=100, cols=10); ws_polter.append_row(["Datum_Upload", "Datum_Aufnahme", "Los_Nr", "Revier", "Polter_Nr", "Menge_Fm", "Lat", "Lon", "Maps_Link"])

    polter_rows = []
    for p in data.get('polter', []):
        fm = clean_number(p.get('fm', 0))
        # HIER WIRD GERECHNET
        lat, lon = fix_coordinates(p.get('lat', 0), p.get('lon', 0))
        
        link = f"http://maps.google.com/?q={lat},{lon}" if lat != 0 else ""
        polter_rows.append([
            timestamp, datum_aufnahme, los, revier, p.get('nr'), 
            to_german(fm), to_german(lat), to_german(lon), link
        ])
    if polter_rows: ws_polter.append_rows(polter_rows, value_input_option='USER_ENTERED')

    # BLATT 2: EINZELSTÄMME
    staemme_data = data.get('staemme', [])
    if staemme_data:
        try: ws_stamm = sh.worksheet("Einzelstaemme")
        except: ws_stamm = sh.add_worksheet(title="Einzelstaemme", rows=1000, cols=10); ws_stamm.append_row(["Datum_Upload", "Los_Nr", "Revier", "WNr", "Holzart", "Laenge", "Durchmesser", "Gue_Kl", "Volumen_Fm"])

        stamm_rows = []
        for s in staemme_data:
            l = clean_number(s.get('l', 0))
            d = clean_number(s.get('d', 0))
            fm = clean_number(s.get('fm', 0), is_volume=True)
            stamm_rows.append([
                timestamp, los, revier, s.get('wnr', ''), s.get('art', ''), 
                to_german(l), to_german(d), s.get('klasse', ''), to_german(fm)
            ])
        if stamm_rows: ws_stamm.append_rows(stamm_rows, value_input_option='USER_ENTERED')

    return True

# --- DATEN LÖSCHEN ---
def delete_entry(los, revier, datum_aufnahme):
    sh = get_spreadsheet()
    if not sh: return False
    try:
        ws_p = sh.worksheet("Polter_Uebersicht")
        data_p = ws_p.get_all_records()
        df_p = pd.DataFrame(data_p)
        mask_p = (df_p['Los_Nr'].astype(str) == str(los)) & (df_p['Revier'].astype(str) == str(revier)) & (df_p['Datum_Aufnahme'].astype(str) == str(datum_aufnahme))
        df_p_clean = df_p[~mask_p]
        ws_p.clear()
        ws_p.update([df_p_clean.columns.values.tolist()] + df_p_clean.values.tolist())

        try:
            ws_s = sh.worksheet("Einzelstaemme")
            data_s = ws_s.get_all_records()
            df_s = pd.DataFrame(data_s)
            mask_s = (df_s['Los_Nr'].astype(str) == str(los)) & (df_s['Revier'].astype(str) == str(revier))
            df_s_clean = df_s[~mask_s]
            ws_s.clear()
            ws_s.update([df_s_clean.columns.values.tolist()] + df_s_clean.values.tolist())
        except: pass
        return True
    except Exception as e:
        st.error(f"Fehler: {e}")
        return False

# --- DATEN LADEN ---
def load_data_frames():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame(), pd.DataFrame()
    
    # Polter
    try:
        data_p = sh.worksheet("Polter_Uebersicht").get_all_records()
        df_polter = pd.DataFrame(data_p)
        if not df_polter.empty:
            if 'Menge_Fm' in df_polter.columns:
                df_polter['Menge_Fm'] = df_polter['Menge_Fm'].apply(lambda x: clean_number(x, is_volume=True))
            
            # Koordinaten: Hier nutzen wir die gleiche Logik wie beim Speichern
            if 'Lat' in df_polter.columns and 'Lon' in df_polter.columns:
                coords = df_polter.apply(lambda row: fix_coordinates(row.get('Lat',0), row.get('Lon',0)), axis=1)
                df_polter['Lat'] = [c[0] for c in coords]
                df_polter['Lon'] = [c[1] for c in coords]
    except: df_polter = pd.DataFrame()

    # Stämme
    try:
        data_s = sh.worksheet("Einzelstaemme").get_all_records()
        df_staemme = pd.DataFrame(data_s)
        if not df_staemme.empty:
            if 'Volumen_Fm' in df_staemme.columns:
                df_staemme['Volumen_Fm'] = df_staemme['Volumen_Fm'].apply(lambda x: clean_number(x, is_volume=True))
            for col in ['Laenge', 'Durchmesser']:
                if col in df_staemme.columns:
                    df_staemme[col] = df_staemme[col].apply(clean_number)
    except: df_staemme = pd.DataFrame()
    
    return df_polter, df_staemme

# --- APP START ---
st.title("🌲 Forst-Verwaltung")

tab1, tab2 = st.tabs(["📸 Scan & Analyse", "🗃️ Bestand"])

# --- TAB 1: SCANNER ---
with tab1:
    try: client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
    except: st.stop()

    uploaded_file = st.file_uploader("Holzliste hochladen (PDF)", type=["pdf", "jpg", "png"])

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
            if st.button("🚀 Analysieren (Gemini 3 Preview)"):
                with st.spinner("Analyse läuft..."):
                    try:
                        # --- PROMPT ---
                        prompt = """
                        Du bist ein KI-Assistent für deutsche Forstwirtschaft. Analysiere dieses Dokument exakt.
                        
                        --- AUFGABE 1: METADATEN & STAMM-ANZAHL ---
                        Suche "Gesamtmenge" (Fm) und "Stämme gezählt" (oder "Waldnummern gezählt").
                        
                        --- AUFGABE 2: EINZELSTÄMME ---
                        Suche die Tabelle "ZUSAMMENSTELLUNG NACH WALDNUMMERN" oder ähnlich.
                        WICHTIG: Die Tabelle ist oft ZWEISPALTIG. Lese alle Spalten!
                        Spalten: "WNr", "Lä", "DoR", "FmoR" (Volumen). nur stämme mit waldnummer zählen und aufnehmen. oft gibt es eine zusammenfassung der holzarten oder qualitäten. diese nicht als einzelstämme aufnehmen.
                        
                        --- AUFGABE 3: POLTER & GPS ---
                        Suche Polter-Listen mit GPS. 
                        ACHTUNG: Format ist oft DMS (Grad Minute Sekunde). Extrahiere den String exakt so wie er da steht, z.B. "48°17'06,71".
                        
                        --- JSON STRUKTUR ---
                        {
                            "meta": {
                                "los": "String", "revier": "String", "datum": "String", 
                                "dokument_summe": Float, 
                                "dokument_anzahl_staemme": Int
                            },
                            "polter": [{"nr": Int, "fm": Float, "lat": "String (Raw)", "lon": "String (Raw)"}],
                            "staemme": [{"wnr": "String", "art": "String", "l": Float, "d": Float, "klasse": "String", "fm": Float}]
                        }
                        """
                        response = client.models.generate_content(
                            model="gemini-3-flash-preview", 
                            contents=[prompt, content],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        clean = response.text.replace("```json", "").replace("```", "").strip()
                        raw = json.loads(clean)
                        data = {"polter": raw, "meta": {}, "staemme": []} if isinstance(raw, list) else raw
                        st.session_state.analyzed_data = data
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler: {e}")

    if st.session_state.analyzed_data:
        data = st.session_state.analyzed_data
        
        st.divider()
        st.subheader("🕵️ Prüfung & Validierung")
        
        # Daten holen
        doc_sum = clean_number(data.get('meta', {}).get('dokument_summe', 0))
        doc_count = int(clean_number(data.get('meta', {}).get('dokument_anzahl_staemme', 0)))
        
        stamm_sum = sum([clean_number(s.get('fm', 0), True) for s in data.get('staemme', [])])
        stamm_count = len(data.get('staemme', []))
        
        # 3 Spalten für Checks
        c1, c2, c3 = st.columns(3)
        
        # Check 1: Festmeter
        with c1:
            st.markdown("**Festmeter-Check**")
            st.write(f"Soll: {doc_sum:.2f} Fm")
            st.write(f"Ist: {stamm_sum:.2f} Fm")
            diff = abs(doc_sum - stamm_sum)
            if doc_sum > 0:
                if diff < 1.0: st.success(f"✅ OK (Diff: {diff:.2f})")
                else: st.error(f"⚠️ Fehler (Diff: {diff:.2f})")
            else: st.info("Keine Soll-Menge gefunden")
            
        # Check 2: Anzahl Stämme
        with c2:
            st.markdown("**Stückzahl-Check**")
            st.write(f"Soll: {doc_count} Stk")
            st.write(f"Ist: {stamm_count} Stk")
            if doc_count > 0:
                if doc_count == stamm_count: st.success("✅ Passt genau")
                else: 
                    diff_count = doc_count - stamm_count
                    st.error(f"⚠️ Es fehlen {diff_count} Stämme!" if diff_count > 0 else f"⚠️ Zu viele ({abs(diff_count)})!")
            else: st.info("Keine Soll-Anzahl gefunden")
            
        # Check 3: Polter
        with c3:
            st.markdown("**Polter**")
            st.metric("Gefunden", len(data.get('polter', [])))

        with st.expander("Details Stämme"):
            st.dataframe(pd.DataFrame(data.get('staemme', [])))

        if st.button("💾 Speichern"):
            with st.spinner("Speichere..."):
                if save_to_sheets(data):
                    st.success("Gespeichert!")
                    st.session_state.analyzed_data = None
                    st.info("Daten sind im Bestand.")

# --- TAB 2: BESTAND ---
with tab2:
    if st.button("🔄 Aktualisieren"):
        st.cache_data.clear()
        
    df_polter, df_staemme = load_data_frames()
    
    if df_polter.empty:
        st.info("Keine Daten.")
    else:
        # KARTE
        st.subheader("🗺️ Karte")
        valid_pts = []
        for _, row in df_polter.iterrows():
            try:
                # Hier greift die intelligente Reparatur
                lat, lon = fix_coordinates(row.get('Lat',0), row.get('Lon',0))
                
                # Filter: Nur anzeigen wenn in DE (Lat > 47)
                if lat > 47:
                    valid_pts.append({"lat": lat, "lon": lon, "info": f"Revier {row['Revier']} | P{row['Polter_Nr']}"})
            except: pass
            
        if valid_pts:
            map_df = pd.DataFrame(valid_pts)
            # Zoom auf Mittelpunkt der Daten
            m = folium.Map(location=[map_df.lat.mean(), map_df.lon.mean()], zoom_start=11)
            for _, pt in map_df.iterrows():
                folium.Marker([pt['lat'], pt['lon']], popup=pt['info'], icon=folium.Icon(color="green", icon="tree", prefix='fa')).add_to(m)
            st_folium(m, width="100%", height=350)
        
        st.divider()
        st.subheader("📂 Akten")
        
        if 'Los_Nr' in df_polter.columns:
            groups = df_polter.groupby(['Revier', 'Los_Nr', 'Datum_Aufnahme'])
            
            for (revier, los, datum), group in groups:
                polter_sum = group['Menge_Fm'].sum()
                
                with st.expander(f"🌲 {revier} | Los {los} | 📅 {datum} | 📦 {polter_sum:.2f} Fm"):
                    
                    col_del, col_info = st.columns([1, 4])
                    with col_del:
                        if st.button(f"🗑️ Liste Löschen", key=f"del_{revier}_{los}_{datum}"):
                            with st.spinner("Lösche Daten..."):
                                if delete_entry(los, revier, datum):
                                    st.success("Gelöscht!")
                                    st.cache_data.clear()
                                    st.rerun()

                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("**Polter:**")
                        st.dataframe(group[['Polter_Nr', 'Menge_Fm', 'Lat', 'Lon']], hide_index=True)
                    with c2:
                        st.markdown("**Stämme:**")
                        if not df_staemme.empty:
                            match = df_staemme[(df_staemme['Los_Nr'].astype(str) == str(los))]
                            if not match.empty:
                                st.dataframe(match[['WNr', 'Holzart', 'Laenge', 'Durchmesser', 'Volumen_Fm']], hide_index=True)
                            else:
                                st.write("Keine Stämme.")
