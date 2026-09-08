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

# --- 0. [핵심] 자동 컬럼 교정 엔진 ---
def fix_column_names(df):
    if df.empty: return df
    mapping = {
        "현장명": ["현장명", "현장 이름", "현장명(명)"],
        "소장": ["소장", "현장소장", "소장명", "담당자"],
        "위치": ["위치", "현장위치", "지역"],
        "위도": ["위도", "lat", "latitude"],
        "경도": ["경도", "lon", "longitude"],
        "공사 시작일": ["공사 시작일", "시작일", "공사시작일", "시작 예정일"],
        "종료일": ["종료일", "종료(예정)일", "종료예정일", "종료일(예정)"],
        "안전 등급": ["안전 등급", "안전등급", "안전", "안전상태"],
        "공정": ["공정", "진행상태", "공정상태", "프로젝트상태"],
        "상태": ["상태", "인력상태", "근무상태", "현장상태"],
        "구조물 공정율": ["구조물 공정율", "구조물공정율", "구조물%", "구조물 공정"],
        "전기 공정율": ["전기 공정율", "전기공정율", "전기%", "전기 공정"],
        "이름": ["이름", "성함", "성명"],
        "현장": ["현장", "현장명", "대상현장"],
        "용량": ["용량", "필요인원", "규모", "총인원"],
        "근무일수": ["근무일수", "작업일수", "투입기간"],
        "투입인원": ["투입인원", "현재인원", "투입인원수"]
    }
    new_columns = {}
    for col in df.columns:
        found = False
        for standard_name, aliases in mapping.items():
            if col.strip() in aliases:
                new_columns[col] = standard_name
                found = True
                break
        if not found: new_columns[col] = col
    return df.rename(columns=new_columns)

# --- 1. 구글 시트 연결 ---
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

# --- 2. 데이터 로드 ---
@st.cache_data(ttl=300)
def load_data_from_sheet():
    sheet = connect_to_gsheets()
    if sheet is None: return pd.DataFrame(), pd.DataFrame()
    try:
        managers_df = pd.DataFrame(sheet.worksheet("managers").get_all_records())
        managers_df = fix_column_names(managers_df)
        projects_df = pd.DataFrame(sheet.worksheet("projects").get_all_records())
        projects_df = fix_column_names(projects_df)
        
        if not projects_df.empty:
            for col in ['공사 시작일', '종료일']:
                if col in projects_df.columns: projects_df[col] = pd.to_datetime(projects_df[col], errors='coerce')
            for col in ["구조물 공정율", "전기 공정율"]:
                if col in projects_df.columns: projects_df[col] = pd.to_numeric(projects_df[col], errors='coerce').fillna(0)
            
            desired_order = ["현장명", "소장", "위치", "안전 등급", "공정", "구조물 공정율", "전기 공정율", "공사 시작일", "종료일", "위도", "경도"]
            existing_cols = [col for col in desired_order if col in projects_df.columns]
            extra_cols = [col for col in projects_df.columns if col not in existing_cols]
            projects_df = projects_df[existing_cols + extra_cols]

        return managers_df, projects_df
    except Exception as e:
        st.error(f"로드 오류: {e}")
        return pd.DataFrame(), pd.DataFrame()

# --- 3. 데이터 저장 ---
def save_data_to_sheet(sheet, managers_df, projects_df):
    try:
        ws_m = sheet.worksheet("managers")
        ws_m.clear()
        ws_m.update([managers_df.columns.values.tolist()] + managers_df.values.tolist())
        ws_p = sheet.worksheet("projects")
        ws_p.clear()
        projects_copy = projects_df.copy()
        for col in ['공사 시작일', '종료일']:
            if col in projects_copy.columns: projects_copy[col] = projects_copy[col].dt.strftime('%Y-%m-%d')
        ws_p.update([projects_copy.columns.values.tolist()] + projects_copy.values.tolist())
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

# --- 4. 권한 관리 로직 ---
def handle_auth():
    st.sidebar.title("🔐 접속 권한")
    auth_mode = st.sidebar.radio("접속 모드를 선택하세요", ["조회자 (읽기 전용)", "관리자 (수정/관리용)"])
    user_role = "viewer"
    if auth_mode == "관리자 (수정/관리용)":
        password = st.sidebar.text_input("관리자 비밀번호", type="password")
        admin_pw = st.secrets.get("ADMIN_PW", "1931")
        if password == admin_pw:
            user_role = "admin"
            st.sidebar.success("✅ 관리자 모드 활성화")
        elif password != "":
            st.sidebar.error("❌ 비밀번호가 틀렸습니다.")
    return user_role

# --- 5. 유틸리티 기능 (날씨/엑셀/알림) ---
def get_simulated_weather():
    # 실제 운영 시에는 requests를 이용해 API 호출로 교체 가능
    return {"서울": "☀️ 맑음", "부산": "☁️ 흐림", "대구": "🌧️ 비", "광주": "☀️ 맑음", "인천": "💨 바람", "울산": "☀️ 맑음", "대전": "☁️ 흐림", "제주": "🌦️ 비", "세종": "☀️ 맑음", "창원": "☀️ 맑음"}

def export_to_excel(df, filename):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    processed_data = output.getvalue()
    return processed_data

# --- 6. 메인 앱 실행 ---
st.set_page_config(page_title="스마트 건설 PMS Pro", layout="wide", initial_sidebar_state="expanded")

# [알림 시스템] 위험 현장 체크
sheet = connect_to_gsheets()
if sheet:
    managers_df, projects_df = load_data_from_sheet()
    
    # 위험 알림 배너
    if not projects_df.empty:
        high_risk_sites = projects_df[projects_df['안전 등급'] == '위험']['현장명'].tolist()
        stopped_sites = projects_df[projects_df['공정'] == '일시 중단']['현장명'].tolist()
        if high_risk_sites or stopped_sites:
            st.error(f"⚠️ **긴급 알림**: 위험 현장 [{', '.join(high_risk_sites)}] 및 중단 현장 [{', '.join(stopped_sites)}]이 감지되었습니다!")

    # 권한 관리
    user_role = handle_auth()
    st.title("🏗️ 스마트 건설 프로젝트 관리 시스템 Pro")

    # --- 탭 구성 ---
    tab_dash, tab1, tab2, tab3, tab4 = st.tabs(["📊 종합 대시보드", "🗺️ 지도/날씨", "👷 작업현황", "📋 프로젝트", "👥 인력/자원 관리"])

    # --- [Tab 0] 종합 대시보드 ---
    with tab_dash:
        if not projects_df.empty:
            st.subheader("📈 실시간 프로젝트 요약")
            kpi1, kpi2, kpi3, kpi4 = st.columns(4)
            
            total_projects = len(projects_df)
            avg_progress = projects_df[['구조물 공정율', '전기 공정율']].mean().mean()
            total_manpower = managers_df['투입인원'].sum() if '투입인원' in managers_df.columns else 0
            risk_count = len(projects_df[projects_df['안전 등급'] == '위험'])

            kpi1.metric("총 현장 수", f"{total_projects} 개")
            kpi2.metric("평균 공정율", f"{avg_progress:.1f}%")
            kpi3.metric("총 투입 인원", f"{total_manpower} 명")
            kpi4.metric("위험 현장", f"{risk_count} 개", delta_color="inverse")

            st.divider()
            
            col_chart1, col_chart2 = st.columns(2)
            with col_chart1:
                st.write("**🏗️ 현장별 공정 현황 (%)**")
                fig_bar = px.bar(projects_df, x="현장명", y=["구조물 공정율", "전기 공정율"], barmode="group", color_discrete_sequence=["#1f77b4", "#ff7f0e"])
                st.plotly_chart(fig_bar, use_container_width=True)
            
            with col_chart2:
                st.write("**🛡️ 안전 등급 분포**")
                fig_pie = px.pie(projects_df, names="안전 등급", color="안전 등급", color_discrete_map={"정상": "green", "주의": "orange", "위험": "red"})
                st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("데이터가 없습니다.")

    # --- [Tab 1] 지도/날씨 ---
    with tab1:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader("📍 현장 위치 정보")
            m = folium.Map(location=[36.5, 127.5], zoom_start=7)
            for _, row in projects_df.iterrows():
                if '위도' in row and '경도' in row and pd.notnull(row['위도']):
                    folium.Marker([float(row['위도']), float(row['경도'])], popup=row['현장명']).add_to(m)
            st_folium(m, width=700, height=450)
        with col2:
            st.subheader("🌦️ 지역별 날씨 정보")
            weather_data = get_simulated_weather()
            for city, w in weather_data.items():
                st.write(f"**{city}**: {w}")

    # --- [Tab 2] 작업현황 ---
    with tab2:
        if '이름' in managers_df.columns and '상태' in managers_df.columns:
            st.subheader("👷 소장님 실시간 상태 및 공정율")
            status_list = []
            for _, m_row in managers_df.iterrows():
                name, status = m_row.get('이름', '이름없음'), m_row.get('상태', '정보없음')
                struct_val, elec_val, txt = 0, 0, "🟢 휴식 중"
                if status == '공사중':
                    p_info = projects_df[projects_df['소장'] == name]
                    if not p_info.empty:
                        p = p_info.iloc[0]
                        txt = f"{p['현장명']}"
                        struct_val, elec_val = p.get('구조물 공정율', 0), p.get('전기 공정율', 0)
                    else: txt = "현장 정보 없음"
                status_list.append({"소장명": name, "상태": txt, "🏗️ 구조물(%)": struct_val, "⚡ 전기(%)": elec_val})
            st.table(pd.DataFrame(status_list))
        else:
            st.subheader("📊 현장별 인력 투입 현황")
            display_cols = [c for c in ["현장", "용량", "근무일수", "투입인원"] if c in managers_df.columns]
            st.dataframe(managers_df[display_cols], use_container_width=True)

    # --- [Tab 3] 프로젝트 ---
    with tab3:
        st.subheader("📋 프로젝트 상세 정보")
        if user_role == "admin":
            st.info("💡 관리자 모드: 데이터 수정 후 아래 버튼을 눌러 저장하세요.")
            column_configuration = {
                "위도": None, "경도": None,
                "구조물 공정율": st.column_config.ProgressColumn("구조물 공정율", min_value=0, max_value=100, format="%d%%"),
                "전기 공정율": st.column_config.ProgressColumn("전기 공정율", min_value=0, max_value=100, format="%d%%"),
                "안전 등급": st.column_config.SelectboxColumn("안전 등급", options=["정상", "주의", "위험"]),
                "공정": st.column_config.SelectboxColumn("공정", options=["준비 중", "공사 중", "일시 중단", "완료"])
            }
            edited_projects = st.data_editor(projects_df, column_config=column_configuration, use_container_width=True)
            
            col_btn1, col_btn2 = st.columns([1, 5])
            with col_btn1:
                if st.button("💾 저장"):
                    if save_data_to_sheet(sheet, managers_df, edited_projects):
                        st.success("✅ 저장 완료!")
                        st.rerun()
            with col_btn2:
                excel_data = export_to_excel(edited_projects, "projects_export.xlsx")
                st.download_button(label="📥 엑셀 다운로드", data=excel_data, file_name="projects_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            display_df = projects_df.drop(columns=['위도', '경도'], errors='ignore')
            st.dataframe(display_df, use_container_width=True)
            st.download_button(label="📥 엑셀 다운로드", data=export_to_excel(projects_df, "projects.xlsx"), file_name="projects_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # --- [Tab 4] 인력/자원 관리 ---
    with tab4:
        st.subheader("👥 인력/자원 관리")
        if user_role == "admin":
            if '이름' in managers_df.columns:
                st.info("💡 [소장님 개인별 관리 모드]")
                st.data_editor(managers_df, use_container_width=True)
            else:
                st.info("💡 [현장별 인력 투입 계획 모드]")
                col_config = {}
                if '용량' in managers_df.columns: col_config['용량'] = st.column_config.NumberColumn("용량", format="%d 명")
                if '투입인원' in managers_df.columns: col_config['투입인원'] = st.column_config.NumberColumn("투입인원", format="%d 명")
                if '근무일수' in managers_df.columns: col_config['근무일수'] = st.column_config.NumberColumn("근무일수", format="%d 일")
                
                edited_managers = st.data_editor(managers_df, use_container_width=True, column_config=col_config)
                col_btn1, col_btn2 = st.columns([1, 5])
                with col_btn1:
                    if st.button("💾 저장"):
                        if save_data_to_sheet(sheet, edited_managers, projects_df):
                            st.success("✅ 저장 완료!")
                            st.rerun()
                with col_btn2:
                    st.download_button(label="📥 엑셀 다운로드", data=export_to_excel(edited_managers, "managers.xlsx"), file_name="manpower_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.warning("⚠️ 조회자 모드: 데이터는 읽기 전용입니다.")
            st.dataframe(managers_df, use_container_width=True)
            st.download_button(label="📥 엑셀 다운로드", data=export_to_excel(managers_df, "managers.xlsx"), file_name="manpower_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

else:
    st.error("구글 시트 연결 실패")

