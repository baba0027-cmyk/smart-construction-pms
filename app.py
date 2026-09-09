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

# --- 0. [Master Schema] ---
PROJECTS_SCHEMA = [
    "현장", "소장", "용량 (MW)", "위치", "안전 등급", "공정", 
    "구조물 공정율", "전기 공정율", "공사 시작일", "종료일", 
    "위도", "경도", "예산 (KRW)", "실제 집행 비용 (KRW)"
]
MANAGERS_SCHEMA = ["현장", "소장", "예정 구조물", "예정 전기", "누적 구조물", "누적 전기", "총 인원"]

# --- 1. [Engine] 강력한 계산 및 표준화 엔진 ---

def calculate_managers_totals(df):
    if df.empty: return df
    df["누적 구조물"] = pd.to_numeric(df["누적 구조물"], errors='coerce').fillna(0.0)
    df["누적 전기"] = pd.to_numeric(df["누적 전기"], errors='coerce').fillna(0.0)
    df["총 인원"] = df["누적 구조물"] + df["누적 전기"]
    return df

def fix_column_names(df):
    if df.empty: return df
    mapping = {
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
    new_columns = {}
    for col in df.columns:
        clean_col = str(col).strip()
        found = False
        for standard_name, aliases in mapping.items():
            if clean_col in aliases:
                new_columns[col] = standard_name
                found = True
                break
        if not found:
            new_columns[col] = col
    return df.rename(columns=new_columns)

def standardize_dataframe(df, schema):
    df = fix_column_names(df)
    new_df = pd.DataFrame(index=df.index, columns=schema)
    for col in schema:
        if col in df.columns:
            numeric_cols = ["용량 (MW)", "구조물 공정율", "전기 공정율", "예정 구조물", "예정 전기", 
                            "누적 구조물", "누적 전기", "총 인원", "예산 (KRW)", "실제 집행 비용 (KRW)"]
            if col in numeric_cols:
                new_df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            elif col in ["공사 시작일", "종료일"]:
                new_df[col] = pd.to_datetime(df[col], errors='coerce')
            elif col in ["위도", "경도"]:
                new_df[col] = pd.to_numeric(df[col], errors='coerce').fillna(36.5)
            else:
                new_df[col] = df[col].fillna("").astype(object)
        else:
            numeric_cols = ["용량 (MW)", "구조물 공정율", "전기 공정율", "예정 구조물", "예정 전기", 
                            "누적 구조물", "누적 전기", "총 인원", "예산 (KRW)", "실제 집행 비용 (KRW)"]
            if col in numeric_cols:
                new_df[col] = 0.0
            elif col in ["공사 시작일", "종료일"]:
                new_df[col] = datetime.now()
            elif col in ["위도", "경도"]:
                new_df[col] = 36.5
            else:
                new_df[col] = ""
    
    if "공사 시작일" in new_df.columns: new_df["공사 시작일"] = new_df["공사 시작일"].fillna(datetime.now())
    if "종료일" in new_df.columns: new_df["종료일"] = new_df["종료일"].fillna(datetime.now())
    if "총 인원" in new_df.columns: new_df = calculate_managers_totals(new_df)
    return new_df

# --- [Weather Engine] ---
@st.cache_data(ttl=600)
def fetch_weather_info(location="Seoul"):
    try:
        url = f"https://wttr.in/{location}?format=j1"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            curr = data['current_condition'][0]
            temp = curr['temp_C']
            desc = curr['weatherDesc'][0]['value']
            humidity = curr['humidity']
            return {"temp": temp, "desc": desc, "humidity": humidity}
        else:
            return None
    except:
        return None

# --- [Geocoding Engine] ---
def geocode_all_addresses(df):
    if not HAS_GEOPY:
        return df, "⚠️ geopy 라이브러리가 설치되지 않았습니다. (pip install geopy)"
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
            except:
                continue
    msg = f"✅ {updated_count}개 현장의 위치를 업데이트했습니다!" if updated_count > 0 else "❌ 변경된 주소를 찾을 수 없습니다."
    return new_df, msg

# --- 2. 구글 시트 연결 ---
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

# --- 3. 데이터 로드 ---
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
        st.error(f"로드 오류: {e}")
        return pd.DataFrame(columns=MANAGERS_SCHEMA), pd.DataFrame(columns=PROJECTS_SCHEMA)

# --- 4. 데이터 저장 ---
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

# --- 5. 권한 관리 ---
def handle_auth():
    st.sidebar.title("🔐 접속 권한")
    auth_mode = st.sidebar.radio("접속 모드를 선택하세요", ["조회자 (읽기 전용)", "관리자 (수정/관리용)"], key="auth_radio")
    user_role = "viewer"
    if auth_mode == "관리자 (수정/관리용)":
        password = st.sidebar.text_input("관리자 비밀번호", type="password", key="admin_pw_input")
        admin_pw = st.secrets.get("ADMIN_PW", "1931")
        if password == admin_pw:
            user_role = "admin"
            st.sidebar.success("✅ 관리자 모드 활성화")
        elif password != "":
            st.sidebar.error("❌ 비밀번호가 틀렸습니다.")
    return user_role

# --- 6. 유틸리티 ---
def export_to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    return output.getvalue()

# --- 7. 메인 앱 ---
st.set_page_config(page_title="스타쏠라 프로젝트 관리", layout="wide")

sheet = connect_to_gsheets()
if sheet:
    if 'master_p_df' not in st.session_state:
        _, projects_df = load_data_from_sheet()
        st.session_state.master_p_df = projects_df

    managers_df, projects_df = load_data_from_sheet()
    if 'master_p_df' in st.session_state and st.session_state.master_p_df.empty:
        st.session_state.master_p_df = projects_df

    user_role = handle_auth()
    st.title("🏗️ 스타쏠라 프로젝트 관리")

    if user_role == "admin":
        with st.sidebar.expander("🚀 신규 현장 즉시 등록", expanded=False):
            with st.form("quick_add_site_form", clear_on_submit=True):
                new_site_name = st.text_input("📍 신규 현장명 *")
                new_site_manager = st.text_input("👤 현장 소장")
                new_site_mw = st.number_input("⚡ 용량 (MW)", min_value=0.0, step=0.1)
                new_site_loc = st.text_input("🗺️ 위치 (예: 전남 고흥)")
                submit_new_site = st.form_submit_button("🆕 현장 생성 및 시트 반영")
                if submit_new_site:
                    if not new_site_name: st.error("현장명은 반드시 입력해야 합니다!")
                    elif new_site_name in projects_df['현장'].values: st.error("이미 존재하는 현장명입니다!")
                    else:
                        new_p_data = {col: "" for col in PROJECTS_SCHEMA}
                        new_p_data.update({"현장": new_site_name, "소장": new_site_manager, "용량 (MW)": new_site_mw, "위치": new_site_loc, "안전 등급": "정상", "공정": "준비 중", "구조물 공정율": 0.0, "전기 공정율": 0.0, "공사 시작일": datetime.now(), "종료일": datetime.now(), "위도": 36.5, "경도": 127.5, "예산 (KRW)": 0, "실제 집행 비용 (KRW)": 0})
                        new_m_data = {col: 0 for col in MANAGERS_SCHEMA}
                        new_m_data.update({"현장": new_site_name, "소장": new_site_manager, "예정 구조물": 0, "예정 전기": 0, "누적 구조물": 0, "누적 전기": 0, "총 인원": 0})
                        updated_p = pd.concat([projects_df, pd.DataFrame([new_p_data])], ignore_index=True)
                        updated_m = pd.concat([managers_df, pd.DataFrame([new_m_data])], ignore_index=True)
                        updated_m = calculate_managers_totals(updated_m)
                        if save_data_to_sheet(sheet, updated_m, updated_p): 
                            st.success(f"✅ '{new_site_name}' 생성 완료!"); 
                            st.session_state.master_p_df = updated_p
                            st.rerun()

        with st.sidebar.expander("🗑️ 현장 삭제 (관리자용)", expanded=False):
            if not projects_df.empty:
                site_to_delete = st.selectbox("삭제할 현장 선택", projects_df['현장'].tolist(), key="delete_site_sel")
                st.warning(f"⚠️ '{site_to_delete}' 현장을 삭제하면 모든 인력 데이터도 함께 삭제됩니다.")
                confirm_delete = st.checkbox("❌ 삭제를 확정합니다", key="confirm_delete_check")
                if st.button("🚨 현장 삭제 실행", key="btn_delete_exec"):
                    if confirm_delete:
                        updated_p = projects_df[projects_df['현장'] != site_to_delete]
                        updated_m = managers_df[managers_df['현장'] != site_to_delete]
                        if save_data_to_sheet(sheet, updated_m, updated_p): 
                            st.error(f"✅ '{site_to_delete}' 현장이 삭제되었습니다!"); 
                            st.session_state.master_p_df = updated_p
                            st.rerun()
                    else: st.info("⚠️ 삭제를 확정하려면 체크박스를 선택해주세요.")
            else: st.info("삭제할 현장이 없습니다.")

    if not projects_df.empty:
        high_risk = projects_df[projects_df['안전 등급'].astype(str) == '위험']['현장'].tolist()
        if high_risk: st.error(f"⚠️ **긴급 알림**: 위험 현장 [{', '.join(high_risk)}] 관리가 필요합니다!")

    tab_dash, tab_finance, tab_gantt, tab1, tab2, tab_progress, tab3, tab4 = st.tabs([
        "📊 종합 대시보드", "💰 재무 현황", "📅 전체 일정 (Gantt)", "🗺️ 지도/날씨", "👷 인력 투입 비교", "📈 공정율 관리", "📋 프로젝트 마스터", "👥 인력/자원 관리"
    ])

    with tab_dash:
        if not projects_df.empty:
            st.subheader("📈 핵심 현황 지표 (Summary)")
            kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
            kpi1.metric("총 현장 수", f"{len(projects_df)} 개")
            kpi2.metric("총 용량 (MW)", f"{projects_df['용량 (MW)'].sum():.1f} MW")
            kpi3.metric("총 투입 인원", f"{managers_df['총 인원'].sum():.0f} 명")
            kpi4.metric("총 예산", f"{projects_df['예산 (KRW)'].sum():,.0f} 원")
            kpi5.metric("위험 현장", f"{len(projects_df[projects_df['안전 등급'].astype(str) == '위험'])} 개", delta_color="inverse")
            st.divider()
            st.subheader("📊 현장별 공정 진행 현황 (%)")
            fig_bar = px.bar(projects_df, x="현장", y=["구조물 공정율", "전기 공정율"], barmode="group", title="현장별 구조물 vs 전기 공정율 비교", color_discrete_sequence=["#1f77b4", "#ff7f0e"])
            st.plotly_chart(fig_bar, use_container_width=True)

    with tab_finance:
        st.subheader("💰 프로젝트 재무 분석 (Budget vs Actual)")
        if not projects_df.empty:
            total_budget = projects_df['예산 (KRW)'].sum()
            total_actual = projects_df['실제 집행 비용 (KRW)'].sum()
            avg_execution_rate = (total_actual / total_budget * 100) if total_budget > 0 else 0
            f_kpi1, f_kpi2, f_kpi3 = st.columns(3)
            f_kpi1.metric("총 예산", f"{total_budget:,.0f} 원")
            f_kpi2.metric("총 집행액", f"{total_actual:,.0f} 원", delta=f"{total_actual - total_budget:,.0f} 원", delta_color="inverse")
            f_kpi3.metric("평균 집행률", f"{avg_execution_rate:.1f} %")
            st.divider()
            st.markdown("#### 📊 현장별 예산 대비 집행 현황")
            fig_finance = go.Figure()
            fig_finance.add_trace(go.Bar(x=projects_df['현장'], y=projects_df['예산 (KRW)'], name='💰 예산 (Budget)', marker_color='#D3D3D3'))
            fig_finance.add_trace(go.Bar(x=projects_df['현장'], y=projects_df['실제 집행 비용 (KRW)'], name='💸 실제 비용 (Actual)', marker_color='#636EFA'))
            fig_finance.update_layout(barmode='group', xaxis_title="현장명", yaxis_title="금액 (KRW)", height=450)
            st.plotly_chart(fig_finance, use_container_width=True)
            st.markdown("#### 📋 현장별 집행 상세 리스트")
            finance_df = projects_df[['현장', '예산 (KRW)', '실제 집행 비용 (KRW)']].copy()
            finance_df['집행률 (%)'] = (finance_df['실제 집행 비용 (KRW)'] / finance_df['예산 (KRW)'] * 100).fillna(0)
            
            def color_execution_rate(val):
                if val > 100: color = 'red'
                elif val > 80: color = 'orange'
                else: color = 'black'
                return f'color: {color}'

            # [FIXED] .applymap() -> .map()
            st.dataframe(finance_df.style.format({
                "예산 (KRW)": "{:,.0f}",
                "실제 집행 비용 (KRW)": "{:,.0f}",
                "집행률 (%)": "{:.1f}%"
            }).map(color_execution_rate, subset=['집행률 (%)']), use_container_width=True)
            st.info("💡 **집행률 색상 가이드**: 🔴 100% 초과(예산 초과), 🟠 80% 초과(주의)")
        else:
            st.write("데이터가 없습니다.")

    with tab_gantt:
        st.subheader("📅 프로젝트 공사 일정 (Gantt Chart)")
        if not projects_df.empty:
            gantt_df = projects_df[['현장', '공사 시작일', '종료일', '공정']].copy()
            gantt_df = gantt_df.dropna(subset=['공사 시작일', '종료일'])
            if not gantt_df.empty:
                fig_gantt = px.timeline(
                    gantt_df, x_start="공사 시작일", x_end="종료일", y="현장", color="공정",
                    title="현장별 공사 기간 및 공정 상태",
                    color_discrete_map={"준비 중": "#D3D3D3", "공사 중": "#636EFA", "일시 중단": "#EF553B", "완료": "#00CC96"}
                )
                fig_gantt.update_yaxes(autorange="reversed")
                fig_gantt.update_layout(height=max(400, len(gantt_df) * 40), xaxis_title="일정", yaxis_title="현장명")
                today = datetime.now()
                fig_gantt.add_vline(x=today.strftime("%Y-%m-%d"), line_width=2, line_dash="dash", line_color="red")
                fig_gantt.add_annotation(x=today.strftime("%Y-%m-%d"), text="오늘", showarrow=False, yref="paper", y=1.05, font_color="red")
                st.plotly_chart(fig_gantt, use_container_width=True)
            else:
                st.warning("📅 표시할 일정 데이터(시작일/종료일)가 부족합니다.")
        else:
            st.write("데이터가 없습니다.")

    with tab1:
        col1, col2 = st.columns([2, 1])
        with col1:
            m = folium.Map(location=[36.5, 127.5], zoom_start=7)
            for _, row in projects_df.iterrows():
                try:
                    if pd.notnull(row['위도']) and pd.notnull(row['경도']):
                        folium.Marker([float(row['위도']), float(row['경도'])], popup=str(row['현장'])).add_to(m)
                except: pass
            st_folium(m, width=700, height=450)
        with col2:
            st.subheader("🌦️ 실시간 날씨 정보")
            st.markdown("#### 🔍 현장별 상세 날씨")
            weather_options = ["전체 요약 보기"] + projects_df['현장'].tolist()
            selected_weather_site = st.selectbox("날씨를 확인할 현장을 선택하세요", weather_options)
            if selected_weather_site == "전체 요약 보기":
                st.info("아래 [현장별 날씨 요약] 섹션에서 모든 현장의 날씨를 확인할 수 있습니다.")
            else:
                site_data = projects_df[projects_df['현장'] == selected_weather_site].iloc[0]
                target_loc = site_data['위치'] if site_data['위치'] else "Seoul"
                weather = fetch_weather_info(target_loc)
                if weather:
                    st.metric(label=f"📍 {selected_weather_site}", value=f"{weather['temp']}°C")
                    st.write(f"**상태:** {weather['desc']}")
                    st.write(f"**습도:** {weather['humidity']}%")
                else:
                    st.warning(f"'{target_loc}'의 날씨 정보를 가져올 수 없습니다.")
            st.divider()
            st.markdown("#### 📋 현장별 날씨 요약")
            if not projects_df.empty:
                summary_list = []
                for _, row in projects_df.iterrows():
                    loc = row['위치'] if row['위치'] else "Seoul"
                    w = fetch_weather_info(loc)
                    if w: summary_list.append({"현장명": row['현장'], "온도": f"{w['temp']}°C", "상태": w['desc']})
                    else: summary_list.append({"현장명": row['현장'], "온도": "-", "상태": "정보 없음"})
                if summary_list: st.dataframe(pd.DataFrame(summary_list), hide_index=True, use_container_width=True)
            else: st.write("데이터가 없습니다.")

    with tab2:
        st.subheader("📊 현장별 인력 투입 분석 (계획 vs 누적)")
        if not managers_df.empty:
            melted_data = []
            for _, row in managers_df.iterrows():
                site = str(row['현장'])
                melted_data.append({'현장': site, '공종': '구조물', '구분': '계획(Plan)', '인원': row['예정 구조물']})
                melted_data.append({'현장': site, '공종': '구조물', '구분': '누적(Actual)', '인원': row['누적 구조물']})
                melted_data.append({'현장': site, '공종': '전기', '구분': '계획(Plan)', '인원': row['예정 전기']})
                melted_data.append({'현장': site, '공종': '전기', '구분': '누적(Actual)', '인원': row['누적 전기']})
            df_plot = pd.DataFrame(melted_data)
            fig_man = go.Figure()
            df_sp = df_plot[(df_plot['공종']=='구조물') & (df_plot['구분']=='계획(Plan)')]
            fig_man.add_trace(go.Bar(x=df_sp['현장'], y=df_sp['인원'], name='🏗️ 구조물(계획)', marker_color='#D3D3D3'))
            df_sa = df_plot[(df_plot['공종']=='구조물') & (df_plot['구분']=='누적(Actual)')]
            sa_colors = []
            for _, r in df_sa.iterrows():
                orig = managers_df[managers_df['현장'] == r['현장']]
                sa_colors.append('#EF553B' if not orig.empty and r['인원'] > orig['예정 구조물'].values[0] else '#636EFA')
            fig_man.add_trace(go.Bar(x=df_sa['현장'], y=df_sa['인원'], name='🏗️ 구조물(누적)', marker_color=sa_colors))
            df_ep = df_plot[(df_plot['공종']=='전기') & (df_plot['구분']=='계획(Plan)')]
            fig_man.add_trace(go.Bar(x=df_ep['현장'], y=df_ep['인원'], name='⚡ 전기(계획)', marker_color='#D3D3D3'))
            df_ea = df_plot[(df_plot['공종']=='전기') & (df_plot['구분']=='누적(Actual)')]
            ea_colors = []
            for _, r in df_ea.iterrows():
                orig = managers_df[managers_df['현장'] == r['현장']]
                ea_colors.append('#EF553B' if not orig.empty and r['인원'] > orig['예정 전기'].values[0] else '#636EFA')
            fig_man.add_trace(go.Bar(x=df_ea['현장'], y=df_ea['인원'], name='⚡ 전기(누적)', marker_color=ea_colors))
            fig_man.update_layout(barmode='group', title="현장별 인력 투입 현황 (🔴 빨간색: 계획 초과!)", xaxis={'type': 'category'})
            st.plotly_chart(fig_man, use_container_width=True)
            st.divider()
            st.subheader("📝 인력 데이터 수정")
            if user_role == "admin":
                col_config = {
                    "예정 구조물": st.column_config.NumberColumn("🏗️ 예정(구조)", format="%d"),
                    "예정 전기": st.column_config.NumberColumn("⚡ 예정(전기)", format="%d"),
                    "누적 구조물": st.column_config.NumberColumn("🏗️ 누적(구조)", format="%d"),
                    "누적 전기": st.column_config.NumberColumn("⚡ 누적(전기)", format="%d"),
                    "총 인원": st.column_config.NumberColumn("📊 총 인원", format="%d", disabled=True)
                }
                edited_m = st.data_editor(managers_df, column_config=col_config, use_container_width=True, key="editor_tab2")
                if st.button("💾 변경사항 저장", key="btn_save_tab2"):
                    if save_data_to_sheet(sheet, edited_m, projects_df): st.success("✅ 저장 완료!"); st.rerun()
            else: st.dataframe(managers_df, use_container_width=True)

    with tab_progress:
        st.subheader("📈 공정율 관리")
        if user_role == "admin":
            st.markdown("### ⚡ 1. 초고속 슬라이더 업데이트 (추천)")
            selected_site = st.selectbox("📍 업데이트할 현장을 선택하세요", projects_df['현장'].tolist(), key="slider_site_sel")
            if selected_site:
                site_idx = projects_df[projects_df['현장'] == selected_site].index[0]
                current_row = projects_df.loc[site_idx]
                col1, col2 = st.columns(2)
                with col1:
                    new_struct_prog = st.slider("🏗️ 구조물 공정율 (%)", 0, 100, int(current_row['구조물 공정율']))
                    new_status = st.selectbox("🔄 현재 공정 상태", ["준비 중", "공사 중", "일시 중단", "완료"], index=["준비 중", "공사 중", "일시 중단", "완료"].index(current_row['공정']))
                with col2:
                    new_elec_prog = st.slider("⚡ 전기 공정율 (%)", 0, 100, int(current_row['전기 공정율']))
                if st.button(f"✅ '{selected_site}' 정보 업데이트", key="btn_update_slider"):
                    updated_p = projects_df.copy()
                    updated_p.at[site_idx, '구조물 공정율'] = float(new_struct_prog)
                    updated_p.at[site_idx, '전기 공정율'] = float(new_elec_prog)
                    updated_p.at[site_idx, '공정'] = new_status
                    if save_data_to_sheet(sheet, managers_df, updated_p):
                        st.session_state.master_p_df = updated_p
                        st.success(f"✅ '{selected_site}' 업데이트 완료!"); st.rerun()
            st.divider()
            st.markdown("### 📋 2. 일괄 편집 모드 (Batch Edit)")
            progress_cols = ["현장", "공정", "구조물 공정율", "전기 공정율"]
            col_config_prog = {
                "구조물 공정율": st.column_config.ProgressColumn("🏗️ 구조물 %", min_value=0, max_value=100, format="%d%%"),
                "전기 공정율": st.column_config.ProgressColumn("⚡ 전기 %", min_value=0, max_value=100, format="%d%%"),
                "공정": st.column_config.SelectboxColumn("진행상태", options=["준비 중", "공사 중", "일시 중단", "완료"])
            }
            edited_prog = st.data_editor(projects_df, column_config=col_config_prog, column_order=progress_cols, use_container_width=True, key="editor_tab_progress")
            if st.button("💾 일괄 변경사항 저장", key="btn_save_prog"):
                if save_data_to_sheet(sheet, managers_df, edited_prog):
                    st.session_state.master_p_df = edited_prog
                    st.success("✅ 일괄 업데이트 완료!"); st.rerun()
        else: st.dataframe(projects_df[["현장", "공정", "구조물 공정율", "전기 공정율"]], use_container_width=True)

    with tab3:
        st.subheader("📋 프로젝트 마스터 정보")
        st.info("💡 현장 위치, 용량, 안전 등급 및 **예산/집행 비용**을 수정합니다.")
        if user_role == "admin":
            col_config = {
                "구조물 공정율": st.column_config.ProgressColumn("구조물 %", min_value=0, max_value=100, format="%d%%"),
                "전기 공정율": st.column_config.ProgressColumn("전기 %", min_value=0, max_value=100, format="%d%%"),
                "용량 (MW)": st.column_config.NumberColumn("용량 (MW)", format="%.2f"),
                "안전 등급": st.column_config.SelectboxColumn("안전", options=["정상", "주의", "위험"]),
                "공정": st.column_config.SelectboxColumn("공정", options=["준비 중", "공사 중", "일시 중단", "완료"]),
                "예산 (KRW)": st.column_config.NumberColumn("💰 예산 (KRW)", format="%d"),
                "실제 집행 비용 (KRW)": st.column_config.NumberColumn("💸 집행 비용 (KRW)", format="%d")
            }
            edited_p = st.data_editor(st.session_state.master_p_df, column_config=col_config, use_container_width=True, key="editor_tab3")
            col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 2])
            with col_btn1:
                if st.button("📍 위경도 자동 변환", key="btn_geocode"):
                    with st.spinner("주소 검색 중..."):
                        updated_p, msg = geocode_all_addresses(edited_p)
                        st.session_state.master_p_df = updated_p
                        st.success(msg); st.rerun()
            with col_btn2:
                if st.button("💾 마스터 저장", key="btn_save_tab3"):
                    if save_data_to_sheet(sheet, managers_df, edited_p): 
                        st.session_state.master_p_df = edited_p
                        st.success("✅ 저장 완료!"); st.rerun()
            with col_btn3: st.download_button("📥 엑셀 다운로드", export_to_excel(edited_p), "projects.xlsx", key="btn_dl_excel")
        else: st.dataframe(projects_df.drop(columns=['위도', '경도'], errors='ignore'), use_container_width=True)

    with tab4:
        st.subheader("👥 전체 인력/자원 관리")
        if user_role == "admin":
            st.info("💡 [현장별 인력 투입 관리 모드] *입력 후 반드시 [저장] 버튼을 눌러주세요.*")
            col_config = {
                "예정 구조물": st.column_config.NumberColumn("🏗️ 예정(구조)", format="%d"),
                "예정 전기": st.column_config.NumberColumn("⚡ 예정(전기)", format="%d"),
                "누적 구조물": st.column_config.NumberColumn("🏗️ 누적(구조)", format="%d"),
                "누적 전기": st.column_config.NumberColumn("⚡ 누적(전기)", format="%d"),
                "총 인원": st.column_config.NumberColumn("📊 총 인원", format="%d", disabled=True)
            }
            edited_m = st.data_editor(managers_df, column_config=col_config, use_container_width=True, key="editor_tab4_site")
            if st.button("💾 저장", key="btn_save_tab4"):
                if save_data_to_sheet(sheet, edited_m, projects_df): 
                    st.success("✅ 저장 완료!"); st.rerun()
        else: st.dataframe(managers_df, use_container_width=True)

else:
    st.error("구글 시트 연결 실패")
