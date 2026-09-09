import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import folium
from streamlit_folium import st_folium
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import io
import requests

try:
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    HAS_GEOPY = True
except ImportError:
    HAS_GEOPY = False

# --- 0. [Constants & Schema] ---
PROJECTS_SCHEMA = [
    "현장", "소장", "용량 (MW)", "위치", "안전 등급", "공정", 
    "구조물 공정율", "전기 공정율", "공사 시작일", "종료일", 
    "위도", "경도", "예산 (KRW)", "실제 집행 비용 (KRW)"
]
MANAGERS_SCHEMA = ["현장", "소장", "예정 구조물", "예정 전기", "누적 구조물", "누적 전기", "총 인원"]

COLUMN_MAPPING = {
    "현장": ["현장", "현장명", "현장 이름", "현장명(명)", "대상현장"],
    "소장": ["소장", "현장소장", "소장명", "담당자", "이름", "성함", "성명"],
    "위치": ["위치", "현장위치", "지역"],
    "위도": ["위도", "lat", "latitude"],
    "경도": ["경도", "lon", "longitude"],
    "공사 시작일": ["공사 시작일", "시작일", "공사시작일", "시작 예정일"],
    "종료일": ["종료일", "종료(예정)일", "종료예정일", "종료일(예정)"],
    "안전 등급": ["안전 등급", "안전등급", "안전", "안전상태"],
    "공정": ["공정", "진행상태", "공정상태", "프로젝트상태"],
    "구조물 공정율": ["구조물 공정율", "구조물공정율", "구조물%", "구조물 공정"],
    "전기 공정율": ["전기 공정율", "전기공정율", "전기%", "전기 공정"],
    "예정 구조물": ["예정 구조물", "예정 구조물 인원", "계획 구조물", "예정 구조"],
    "예정 전기": ["예정 전기", "예정 전기 인원", "계획 전기", "예정 전기"],
    "누적 구조물": ["누적 구조물", "누적 구조물 인원", "실적 구조물", "누적 구조"],
    "누적 전기": ["누적 전기", "누적 전기 인원", "실적 전기", "누적 전기"],
    "총 인원": ["총 인원", "합계 인원", "전체 인원", "투입인원", "투입인원수"],
    "용량 (MW)": ["용량 (MW)", "용량(MW)", "MW", "용량", "규모"],
    "예산 (KRW)": ["예산", "예산(원)", "Budget", "예산금액"],
    "실제 집행 비용 (KRW)": ["실제 집행 비용", "실제비용", "집행액", "실제 사용액", "Actual Cost"]
}

NUMERIC_COLS = ["용량 (MW)", "구조물 공정율", "전기 공정율", "예정 구조물", "예정 전기", 
                "누적 구조물", "누적 전기", "총 인원", "예산 (KRW)", "실제 집행 비용 (KRW)"]

# --- 1. [Engine] 최적화된 데이터 엔진 ---

def calculate_managers_totals(df):
    if df.empty: return df
    for col in ["누적 구조물", "누적 전기"]:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    df["총 인원"] = df["누적 구조물"] + df["누적 전기"]
    return df

def standardize_dataframe(df, schema):
    if df.empty: return pd.DataFrame(columns=schema)
    
    # 1. Column Mapping
    new_columns = {}
    for col in df.columns:
        clean_col = str(col).strip()
        found = False
        for standard_name, aliases in COLUMN_MAPPING.items():
            if clean_col in aliases:
                new_columns[col] = standard_name
                found = True
                break
        if not found:
            new_columns[col] = col
    df = df.rename(columns=new_columns)

    # 2. Schema Alignment & Typing
    new_df = pd.DataFrame(index=df.index, columns=schema)
    for col in schema:
        if col in df.columns:
            if col in NUMERIC_COLS:
                new_df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            elif col in ["공사 시작일", "종료일"]:
                new_df[col] = pd.to_datetime(df[col], errors='coerce')
            elif col in ["위도", "경도"]:
                new_df[col] = pd.to_numeric(df[col], errors='coerce').fillna(36.5)
            else:
                new_df[col] = df[col].fillna("").astype(object)
        else:
            if col in NUMERIC_COLS: new_df[col] = 0.0
            elif col in ["공사 시작일", "종료일"]: new_df[col] = datetime.now()
            elif col in ["위도", "경도"]: new_df[col] = 36.5
            else: new_df[col] = ""
    
    # 3. Final Cleanup
    if "공사 시작일" in new_df.columns: new_df["공사 시작일"] = new_df["공사 시작일"].fillna(datetime.now())
    if "종료일" in new_df.columns: new_df["종료일"] = new_df["종료일"].fillna(datetime.now())
    if "총 인원" in new_df.columns: new_df = calculate_managers_totals(new_df)
    return new_df

# --- [Weather Engine] 최적화: 캐싱 및 호출 제어 ---
@st.cache_data(ttl=600)
def fetch_weather_info(location="Seoul"):
    try:
        url = f"https://wttr.in/{location}?format=j1"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            data = response.json()
            curr = data['current_condition'][0]
            return {
                "temp": curr['temp_C'],
                "desc": curr['weatherDesc'][0]['value'],
                "humidity": curr['humidity']
            }
    except:
        pass
    return None

# --- [Geocoding Engine] ---
def geocode_all_addresses(df):
    if not HAS_GEOPY:
        return df, "⚠️ geopy 라이브러리가 필요합니다."
    new_df = df.copy()
    geolocator = Nominatim(user_agent="construction_pms_agent")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    updated_count = 0
    for idx, row in new_df.iterrows():
        addr = row['위치']
        if addr and pd.notnull(addr):
            try:
                location = geocode(addr)
                if location:
                    new_df.at[idx, '위도'] = location.latitude
                    new_df.at[idx, '경도'] = location.longitude
                    updated_count += 1
            except: continue
    msg = f"✅ {updated_count}개 위치 업데이트 완료!" if updated_count > 0 else "❌ 변경 사항 없음"
    return new_df, msg

# --- 2. Google Sheets Connection ---
@st.cache_resource
def connect_to_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        if "gcp_json" in st.secrets:
            creds_dict = json.loads(st.secrets["gcp_json"])
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace('\\n', '\n')
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key("1p-m_7hhsKMRacNlejARKExVtTfQrkAaJpZ_zm7_EZco") 
        return sheet
    except Exception as e:
        st.error(f"연결 실패: {e}")
        return None

# --- 3. Data Loading (Optimized) ---
@st.cache_data(ttl=300)
def load_data_from_sheet():
    sheet = connect_to_gsheets()
    if sheet is None: return pd.DataFrame(columns=MANAGERS_SCHEMA), pd.DataFrame(columns=PROJECTS_SCHEMA)
    try:
        raw_m = pd.DataFrame(sheet.worksheet("managers").get_all_records())
        managers_df = standardize_dataframe(raw_m, MANAGERS_SCHEMA)
        raw_p = pd.DataFrame(sheet.worksheet("projects").get_all_records())
        projects_df = standardize_dataframe(raw_p, PROJECTS_SCHEMA)
        return managers_df, projects_df
    except Exception as e:
        st.error(f"데이터 로드 중 오류 발생: {e}")
        return pd.DataFrame(columns=MANAGERS_SCHEMA), pd.DataFrame(columns=PROJECTS_SCHEMA)

def save_data_to_sheet(sheet, managers_df, projects_df):
    try:
        managers_df = calculate_managers_totals(managers_df.copy())
        m_clean = managers_df.astype(object).where(pd.notnull(managers_df), None)
        p_clean = projects_df.astype(object).where(pd.notnull(projects_df), None)
        
        for col in ['공사 시작일', '종료일']:
            if col in p_clean.columns:
                p_clean[col] = p_clean[col].apply(lambda x: x.strftime('%Y-%m-%d') if isinstance(x, (datetime, pd.Timestamp)) else x)
        
        ws_m = sheet.worksheet("managers")
        ws_m.clear()
        ws_m.update([m_clean.columns.tolist()] + m_clean.values.tolist())
        
        ws_p = sheet.worksheet("projects")
        ws_p.clear()
        ws_p.update([p_clean.columns.tolist()] + p_clean.values.tolist())
        
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

# --- 4. Auth & Utils ---
def handle_auth():
    st.sidebar.title("🔐 접속 권한")
    auth_mode = st.sidebar.radio("접속 모드", ["조회자 (읽기 전용)", "관리자 (수정/관리용)"], key="auth_radio")
    user_role = "viewer"
    if auth_mode == "관리자 (수정/관리용)":
        password = st.sidebar.text_input("비밀번호", type="password", key="admin_pw_input")
        if password == st.secrets.get("ADMIN_PW", "1931"):
            user_role = "admin"
            st.sidebar.success("✅ 관리자 모드")
        elif password != "":
            st.sidebar.error("❌ 비밀번호 오류")
    return user_role

def export_to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    return output.getvalue()

# --- 5. Main App ---
st.set_page_config(page_title="스타쏠라 프로젝트 관리", layout="wide")

sheet = connect_to_gsheets()
if sheet:
    # Initial Data Load
    managers_df, projects_df = load_data_from_sheet()
    
    # Sync Session State for Master Editor
    if 'master_p_df' not in st.session_state or st.session_state.get('needs_sync', False):
        st.session_state.master_p_df = projects_df
        st.session_state.needs_sync = False

    user_role = handle_auth()
    st.title("🏗️ 스타쏠라 프로젝트 관리")

    # Admin Sidebar Tools
    if user_role == "admin":
        with st.sidebar.expander("🚀 신규 현장 등록", expanded=False):
            with st.form("quick_add", clear_on_submit=True):
                n_name = st.text_input("📍 현장명 *")
                n_mgr = st.text_input("👤 소장")
                n_mw = st.number_input("⚡ 용량 (MW)", min_value=0.0, step=0.1)
                n_loc = st.text_input("🗺️ 위치")
                if st.form_submit_button("🆕 생성"):
                    if not n_name: st.error("현장명 필수!")
                    elif n_name in projects_df['현장'].values: st.error("중복 현장!")
                    else:
                        new_p = {col: "" for col in PROJECTS_SCHEMA}
                        new_p.update({"현장": n_name, "소장": n_mgr, "용량 (MW)": n_mw, "위치": n_loc, "안전 등급": "정상", "공정": "준비 중", "공사 시작일": datetime.now(), "종료일": datetime.now(), "위도": 36.5, "경도": 127.5, "예산 (KRW)": 0, "실제 집행 비용 (KRW)": 0})
                        new_m = {col: 0 for col in MANAGERS_SCHEMA}
                        new_m.update({"현장": n_name, "소장": n_mgr})
                        up_p = pd.concat([projects_df, pd.DataFrame([new_p])], ignore_index=True)
                        up_m = pd.concat([managers_df, pd.DataFrame([new_m])], ignore_index=True)
                        if save_data_to_sheet(sheet, up_m, up_p):
                            st.success("생성 완료!"); st.rerun()

        with st.sidebar.expander("🗑️ 현장 삭제", expanded=False):
            if not projects_df.empty:
                del_name = st.selectbox("삭제할 현장", projects_df['현장'].tolist())
                if st.button("🚨 삭제 실행"):
                    up_p = projects_df[projects_df['현장'] != del_name]
                    up_m = managers_df[managers_df['현장'] != del_name]
                    if save_data_to_sheet(sheet, up_m, up_p):
                        st.error(f"{del_name} 삭제됨"); st.rerun()

    # Risk Alert
    if not projects_df.empty:
        risks = projects_df[projects_df['안전 등급'].astype(str) == '위험']['현장'].tolist()
        if risks: st.error(f"⚠️ 위험 현장 발생: {', '.join(risks)}")

    # Tabs
    tab_dash, tab_finance, tab_gantt, tab_map, tab_man, tab_prog, tab_master, tab_res = st.tabs([
        "📊 대시보드", "💰 재무", "📅 일정", "🗺️ 지도/날씨", "👷 인력비교", "📈 공정율", "📋 마스터", "📥 내보내기"
    ])

    with tab_dash:
        if not projects_df.empty:
            st.subheader("📈 핵심 지표")
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("현장 수", f"{len(projects_df)} 개")
            k2.metric("총 용량", f"{projects_df['용량 (MW)'].sum():.1f} MW")
            k3.metric("총 인원", f"{managers_df['총 인원'].sum():.0f} 명")
            k4.metric("총 예산", f"{projects_df['예산 (KRW)'].sum():,.0f} 원")
            k5.metric("위험", f"{len(risks)} 개", delta_color="inverse")
            st.divider()
            fig_b = px.bar(projects_df, x="현장", y=["구조물 공정율", "전기 공정율"], barmode="group", title="현장별 공정 진행 현황 (%)")
            st.plotly_chart(fig_b, use_container_width=True)

    with tab_finance:
        st.subheader("💰 재무 분석")
        if not projects_df.empty:
            t_bud = projects_df['예산 (KRW)'].sum()
            t_act = projects_df['실제 집행 비용 (KRW)'].sum()
            f1, f2, f3 = st.columns(3)
            f1.metric("총 예산", f"{t_bud:,.0f} 원")
            f2.metric("총 집행액", f"{t_act:,.0f} 원", delta=f"{t_act-t_bud:,.0f} 원", delta_color="inverse")
            f3.metric("평균 집행률", f"{(t_act/t_bud*100 if t_bud>0 else 0):.1f} %")
            
            fig_f = go.Figure()
            fig_f.add_trace(go.Bar(x=projects_df['현장'], y=projects_df['예산 (KRW)'], name='예산', marker_color='#D3D3D3'))
            fig_f.add_trace(go.Bar(x=projects_df['현장'], y=projects_df['실제 집행 비용 (KRW)'], name='집행', marker_color='#636EFA'))
            fig_f.update_layout(barmode='group', height=400)
            st.plotly_chart(fig_f, use_container_width=True)
            
            fin_df = projects_df[['현장', '예산 (KRW)', '실제 집행 비용 (KRW)']].copy()
            fin_df['집행률 (%)'] = (fin_df['실제 집행 비용 (KRW)'] / fin_df['예산 (KRW)'] * 100).fillna(0)
            def color_rate(v): return 'color: red' if v > 100 else ('color: orange' if v > 80 else 'black')
            st.dataframe(fin_df.style.format({"예산 (KRW)": "{:,.0f}", "실제 집행 비용 (KRW)": "{:,.0f}", "집행률 (%)": "{:.1f}%"}).map(color_rate, subset=['집행률 (%)']), use_container_width=True)

    with tab_gantt:
        st.subheader("📅 프로젝트 일정")
        if not projects_df.empty:
            g_df = projects_df.dropna(subset=['공사 시작일', '종료일'])
            if not g_df.empty:
                fig_g = px.timeline(g_df, x_start="공사 시작일", x_end="종료일", y="현장", color="공정", title="공사 기간 및 상태")
                fig_g.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_g, use_container_width=True)

    with tab_map:
        col_m, col_w = st.columns([2, 1])
        with col_m:
            m_map = folium.Map(location=[36.5, 127.5], zoom_start=7)
            for _, r in projects_df.iterrows():
                if pd.notnull(r['위도']) and pd.notnull(r['경도']):
                    folium.Marker([float(r['위도']), float(r['경도'])], popup=str(r['현장'])).add_to(m_map)
            st_folium(m_map, width=700, height=450)
        with col_w:
            st.subheader("🌦️ 날씨 정보")
            w_opt = ["전체 요약 보기"] + projects_df['현장'].tolist()
            sel_w = st.selectbox("🔍 상세 날씨 조회", w_opt)
            if sel_w == "전체 요약 보기":
                st.info("현장을 선택하면 해당 지역의 현재 날씨가 표시됩니다.")
            else:
                site_info = projects_df[projects_df['현장'] == sel_w].iloc[0]
                target = site_info['위치'] if site_info['위치'] else "Seoul"
                weather = fetch_weather_info(target)
                if weather:
                    st.metric(f"📍 {sel_w}", f"{weather['temp']}°C")
                    st.write(f"**상태:** {weather['desc']} | **습도:** {weather['humidity']}%")
                else:
                    st.warning("날씨 정보를 가져올 수 없습니다.")
            
            st.divider()
            st.markdown("#### 📋 현장별 날씨 요약")
            if st.button("🔄 전체 현장 날씨 새로고침"):
                with st.spinner("날씨 데이터를 불러오는 중..."):
                    summary_list = []
                    for _, r in projects_df.iterrows():
                        target = r['위치'] if r['위치'] else "Seoul"
                        w = fetch_weather_info(target)
                        summary_list.append({
                            "현장": r['현장'],
                            "위치": target,
                            "온도": f"{w['temp']}°C" if w else "N/A",
                            "상태": w['desc'] if w else "N/A"
                        })
                    st.session_state.weather_summary = pd.DataFrame(summary_list)
            
            if 'weather_summary' in st.session_state and st.session_state.weather_summary is not None:
                st.dataframe(st.session_state.weather_summary, hide_index=True, use_container_width=True)
            else:
                st.info("버튼을 눌러 요약을 불러오세요.")

    with tab_man:
        st.subheader("📊 인력 투입 분석")
        if not managers_df.empty:
            melted = []
            for _, r in managers_df.iterrows():
                s = str(r['현장'])
                melted.append({'현장': s, '공종': '구조물', '구분': '계획', '인원': r['예정 구조물']})
                melted.append({'현장': s, '공종': '구조물', '구분': '누적', '인원': r['누적 구조물']})
                melted.append({'현장': s, '공종': '전기', '구분': '계획', '인원': r['예정 전기']})
                melted.append({'현장': s, '공종': '전기', '구분': '누적', '인원': r['누적 전기']})
            df_m = pd.DataFrame(melted)
            fig_m = go.Figure()
            for dtype, color, name in [('계획', '#D3D3D3', '계획'), ('누적', '#636EFA', '누적')]:
                subset = df_m[df_m['구분'] == dtype]
                fig_m.add_trace(go.Bar(x=subset['현장'], y=subset['인원'], name=name, marker_color=color))
            fig_m.update_layout(barmode='group', title="현장별 인력 계획 vs 누적")
            st.plotly_chart(fig_m, use_container_width=True)
            
            if user_role == "admin":
                st.write("📝 인력 데이터 수정")
                ed_m = st.data_editor(managers_df, use_container_width=True)
                if st.button("💾 인력 저장"):
                    if save_data_to_sheet(sheet, ed_m, projects_df): st.success("저장 완료!"); st.rerun()
            else:
                st.dataframe(managers_df, use_container_width=True)

    with tab_prog:
        st.subheader("📈 공정율 관리")
        if not projects_df.empty:
            if user_role == "admin":
                sel_p = st.selectbox("📍 현장 선택", projects_df['현장'].tolist())
                if sel_p:
                    idx = projects_df[projects_df['현장'] == sel_p].index[0]
                    row = projects_df.loc[idx]
                    c1, c2 = st.columns(2)
                    with c1:
                        new_s = st.slider("구조물 %", 0, 100, int(row['구조물 공정율']))
                        new_st = st.selectbox("상태", ["준비 중", "공사 중", "일시 중단", "완료"], index=["준비 중", "공사 중", "일시 중단", "완료"].index(row['공정']))
                    with c2:
                        new_e = st.slider("전기 %", 0, 100, int(row['전기 공정율']))
                    if st.button("✅ 업데이트"):
                        up_p = projects_df.copy()
                        up_p.at[idx, '구조물 공정율'] = float(new_s)
                        up_p.at[idx, '전기 공정율'] = float(new_e)
                        up_p.at[idx, '공정'] = new_st
                        if save_data_to_sheet(sheet, managers_df, up_p):
                            st.session_state.master_p_df = up_p
                            st.success("업데이트 완료!"); st.rerun()
            else:
                st.dataframe(projects_df, use_container_width=True)

    with tab_master:
        st.subheader("📋 마스터 데이터 편집")
        st.info("모든 데이터를 직접 수정할 수 있습니다. 수정 후 반드시 '저장' 버튼을 눌러주세요.")
        
        mode = st.radio("편집 대상", ["현장/공정 데이터 (Projects)", "인력 데이터 (Managers)"], horizontal=True)
        
        if mode == "현장/공정 데이터 (Projects)":
            ed_p = st.data_editor(projects_df, use_container_width=True, num_rows="dynamic")
            if st.button("💾 프로젝트 데이터 전체 저장"):
                if save_data_to_sheet(sheet, managers_df, ed_p):
                    st.session_state.master_p_df = ed_p
                    st.success("저장 완료!"); st.rerun()
        else:
            ed_m = st.data_editor(managers_df, use_container_width=True, num_rows="dynamic")
            if st.button("💾 인력 데이터 전체 저장"):
                if save_data_to_sheet(sheet, ed_m, projects_df):
                    st.success("저장 완료!"); st.rerun()

    with tab_res:
        st.subheader("📥 데이터 관리 및 내보내기")
        col_e1, col_e2 = st.columns(2)
        with col_e1:
            if st.button("📊 프로젝트 데이터 Excel 다운로드"):
                excel_data = export_to_excel(projects_df)
                st.download_button("⬇️ 다운로드", excel_data, "projects_export.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with col_e2:
            if st.button("👷 인력 데이터 Excel 다운로드"):
                excel_data = export_to_excel(managers_df)
                st.download_button("⬇️ 다운로드", excel_data, "managers_export.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        st.divider()
        if user_role == "admin":
            st.warning("⚠️ 데이터 초기화 주의: '전체 삭제'는 신중하게 결정하십시오.")
            if st.button("🗑️ 모든 현장 데이터 삭제 (초기화)"):
                if st.checkbox("위 내용을 확인했습니다. 삭제를 진행합니다."):
                    empty_p = pd.DataFrame(columns=PROJECTS_SCHEMA)
                    empty_m = pd.DataFrame(columns=MANAGERS_SCHEMA)
                    if save_data_to_sheet(sheet, empty_m, empty_p):
                        st.success("모든 데이터가 삭제되었습니다."); st.rerun()

else:
    st.error("❌ 구글 시트에 연결할 수 없습니다. `credentials.json` 또는 `st.secrets`를 확인하세요.")
