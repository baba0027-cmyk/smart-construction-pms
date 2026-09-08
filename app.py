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

# --- 0. [엔진 고도화] 자동 컬럼 교정 엔진 ---
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
        "예정 구조물": ["예정 구조물", "예정 구조물 인원", "계획 구조물", "예정 구조"],
        "예정 전기": ["예정 전기", "예정 전기 인원", "계획 전기", "예정 전기"],
        "누적 구조물": ["누적 구조물", "누적 구조물 인원", "실적 구조물", "누적 구조"],
        "누적 전기": ["누적 전기", "누적 전기 인원", "실적 전기", "누적 전기"],
        "총 인원": ["총 인원", "합계 인원", "전체 인원", "투입인원", "투입인원수"]
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

# --- 2. 데이터 로드 (에러 방지 로직 강화) ---
@st.cache_data(ttl=300)
def load_data_from_sheet():
    sheet = connect_to_gsheets()
    if sheet is None: return pd.DataFrame(), pd.DataFrame()
    try:
        # 1. Managers 데이터 로드 및 정화
        managers_df = pd.DataFrame(sheet.worksheet("managers").get_all_records())
        managers_df = fix_column_names(managers_df)
        
        # [핵심 수정] 숫자형 컬럼 강제 변환 (에러 방지)
        manpower_cols = ["예정 구조물", "예정 전기", "누적 구조물", "누적 전기", "총 인원"]
        for col in manpower_cols:
            if col in managers_df.columns:
                # 숫자가 아닌 값(글자, 공백 등)은 NaN으로 만들고, NaN은 0으로 채움
                managers_df[col] = pd.to_numeric(managers_df[col], errors='coerce').fillna(0)
        
        # 2. Projects 데이터 로드 및 정화
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

# --- 4. 권한 관리 ---
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

# --- 5. 유틸리티 ---
def export_to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    return output.getvalue()

# --- 6. 메인 앱 ---
st.set_page_config(page_title="스마트 건설 PMS Pro", layout="wide")

sheet = connect_to_gsheets()
if sheet:
    managers_df, projects_df = load_data_from_sheet()
    user_role = handle_auth()
    st.title("🏗️ 스마트 건설 프로젝트 관리 시스템 Pro")

    if not projects_df.empty:
        high_risk = projects_df[projects_df['안전 등급'] == '위험']['현장명'].tolist()
        if high_risk: st.error(f"⚠️ **긴급 알림**: 위험 현장 [{', '.join(high_risk)}] 관리가 필요합니다!")

    tab_dash, tab1, tab2, tab3, tab4 = st.tabs(["📊 종합 대시보드", "🗺️ 지도/날씨", "👷 인력 투입 비교 (Plan vs Act)", "📋 프로젝트", "👥 인력/자원 관리"])

    # --- [Tab 0] 종합 대시보드 ---
    with tab_dash:
        if not projects_df.empty:
            st.subheader("📈 실시간 프로젝트 요약")
            kpi1, kpi2, kpi3, kpi4 = st.columns(4)
            kpi1.metric("총 현장 수", f"{len(projects_df)} 개")
            kpi2.metric("평균 공정율", f"{projects_df[['구조물 공정율', '전기 공정율']].mean().mean():.1f}%")
            
            # [수정] 에러 방지를 위해 다시 한번 안전하게 계산
            total_manpower = managers_df['총 인원'].sum() if '총 인원' in managers_df.columns else 0
            kpi3.metric("총 투입 인원", f"{int(total_manpower)} 명")
            
            kpi4.metric("위험 현장", f"{len(projects_df[projects_df['안전 등급'] == '위험'])} 개", delta_color="inverse")

            st.divider()
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                fig_bar = px.bar(projects_df, x="현장명", y=["구조물 공정율", "전기 공정율"], barmode="group", title="현장별 공정 현황 (%)")
                st.plotly_chart(fig_bar, use_container_width=True)
            with col_c2:
                fig_pie = px.pie(projects_df, names="안전 등급", title="안전 등급 분포", color="안전 등급", color_discrete_map={"정상": "green", "주의": "orange", "위험": "red"})
                st.plotly_chart(fig_pie, use_container_width=True)

    # --- [Tab 1] 지도/날씨 ---
    with tab1:
        col1, col2 = st.columns([2, 1])
        with col1:
            m = folium.Map(location=[36.5, 127.5], zoom_start=7)
            for _, row in projects_df.iterrows():
                if '위도' in row and '경도' in row and pd.notnull(row['위도']):
                    folium.Marker([float(row['위도']), float(row['경도'])], popup=row['현장명']).add_to(m)
            st_folium(m, width=700, height=450)
        with col2:
            st.subheader("🌦️ 지역별 날씨")
            st.write("☀️ 서울: 맑음")
            st.write("☁️ 부산: 흐림")

    # --- [Tab 2] 작업현황 (업그레이드: Plan vs Actual 비교 차트) ---
    with tab2:
        st.subheader("📊 현장별 인력 투입 분석 (계획 vs 누적)")
        
        required_cols = ["현장", "예정 구조물", "예정 전기", "누적 구조물", "누적 전기"]
        if all(col in managers_df.columns for col in required_cols):
            melted_data = []
            for _, row in managers_df.iterrows():
                site = row['현장']
                melted_data.append({'현장': site, '구분': '계획(Plan)', '공종': '구조물', '인원': row['예정 구조물']})
                melted_data.append({'현장': site, '구분': '계획(Plan)', '공종': '전기', '인원': row['예정 전기']})
                melted_data.append({'현장': site, '구분': '누적(Actual)', '공종': '구조물', '인원': row['누적 구조물']})
                melted_data.append({'현장': site, '구분': '누적(Actual)', '공종': '전기', '인원': row['누적 전기']})
            
            df_plot = pd.DataFrame(melted_data)
            df_plot['현장_구분'] = df_plot['현장'] + " (" + df_plot['구분'] + ")"
            
            fig_man = px.bar(df_plot, x="현장_구분", y="인원", color="공종",
                             title="현장별 인력 투입 계획 vs 실적 비교 (Stacked)",
                             barmode="stack",
                             color_discrete_map={"구조물": "#1f77b4", "전기": "#ff7f0e"})
            
            st.plotly_chart(fig_man, use_container_width=True)
            st.write("**📋 상세 인력 투입 현황 데이터**")
            st.dataframe(managers_df[required_cols + (['총 인원'] if '총 인원' in managers_df.columns else [])], use_container_width=True)
        else:
            st.warning("⚠️ '예정 구조물', '예정 전기', '누적 구조물', '누적 전기' 컬럼이 필요합니다. '인력/자원 관리' 탭에서 데이터를 먼저 입력해 주세요.")
            st.info("현재 데이터 컬럼: " + ", ".join(managers_df.columns))

    # --- [Tab 3] 프로젝트 ---
    with tab3:
        st.subheader("📋 프로젝트 상세 정보")
        if user_role == "admin":
            column_config = {
                "구조물 공정율": st.column_config.ProgressColumn("구조물 %", min_value=0, max_value=100, format="%d%%"),
                "전기 공정율": st.column_config.ProgressColumn("전기 %", min_value=0, max_value=100, format="%d%%"),
                "안전 등급": st.column_config.SelectboxColumn("안전", options=["정상", "주의", "위험"]),
                "공정": st.column_config.SelectboxColumn("공정", options=["준비 중", "공사 중", "일시 중단", "완료"])
            }
            edited_p = st.data_editor(projects_df, column_config=column_config, use_container_width=True)
            if st.button("💾 프로젝트 변경사항 저장"):
                if save_data_to_sheet(sheet, managers_df, edited_p):
                    st.success("✅ 저장 완료!"); st.rerun()
            st.download_button("📥 엑셀 다운로드", export_to_excel(projects_df), "projects.xlsx")
        else:
            st.dataframe(projects_df.drop(columns=['위도', '경도'], errors='ignore'), use_container_width=True)

    # --- [Tab 4] 인력/자원 관리 (업그레이드: 4대 핵심 지표 입력) ---
    with tab4:
        st.subheader("👥 인력/자원 관리 (Plan & Actual)")
        if user_role == "admin":
            if '이름' in managers_df.columns:
                st.info("💡 [소장님 개인별 관리 모드]")
                st.data_editor(managers_df, use_container_width=True)
            else:
                st.info("💡 [현장별 인력 투입 계획/실적 관리 모드]")
                col_config = {
                    "현장": None,
                    "예정 구조물": st.column_config.NumberColumn("🏗️ 예정(구조)", format="%d 명"),
                    "예정 전기": st.column_config.NumberColumn("⚡ 예정(전기)", format="%d 명"),
                    "누적 구조물": st.column_config.NumberColumn("🏗️ 누적(구조)", format="%d 명"),
                    "누적 전기": st.column_config.NumberColumn("⚡ 누적(전기)", format="%d 명"),
                    "총 인원": st.column_config.NumberColumn("📊 총 인원", format="%d 명")
                }
                edited_m = st.data_editor(managers_df, column_config=col_config, use_container_width=True)
                
                col_btn1, col_btn2 = st.columns([1, 5])
                with col_btn1:
                    if st.button("💾 저장"):
                        if save_data_to_sheet(sheet, edited_m, projects_df):
                            st.success("✅ 인력 데이터가 저장되었습니다!"); st.rerun()
                with col_btn2:
                    st.download_button("📥 엑셀 다운로드", export_to_excel(managers_df), "manpower.xlsx")
        else:
            st.warning("⚠️ 조회자 모드: 데이터는 읽기 전용입니다.")
            st.dataframe(managers_df, use_container_width=True)
            st.download_button("📥 엑셀 다운로드", export_to_excel(managers_df), "manpower.xlsx")

else:
    st.error("구글 시트 연결 실패")
