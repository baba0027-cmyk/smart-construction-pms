import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import folium
from streamlit_folium import st_folium
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- 1. 구글 시트 연결 설정 (로컬 & 클라우드 하이브리드 방식) ---
def connect_to_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        # [최종 수정] 클라우드 환경(Secrets)에서 JSON 문자열을 읽어옵니다.
        if "gcp_json" in st.secrets:
            # st.secrets["gcp_json"]은 TOML의 """ 로 감싸진 문자열입니다.
            json_string = st.secrets["gcp_json"]
            creds_dict = json.loads(json_string)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            # 로컬 테스트용
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        
        client = gspread.authorize(creds)
        sheet = client.open_by_key("1p-m_7hhsKMRacNlejARKExVtTfQrkAaJpZ_zm7_EZco") 
        return sheet
    except Exception as e:
        st.error(f"구글 시트 연결 실패! 원인: {e}")
        st.info("💡 해결 팁: Secrets에 gcp_json = \"\"\" (JSON내용) \"\"\" 형식을 확인하세요.")
        return None

# --- 2. 데이터 로드 함수 ---
def load_data(sheet):
    managers_df = pd.DataFrame(sheet.worksheet("managers").get_all_records())
    projects_df = pd.DataFrame(sheet.worksheet("projects").get_all_records())
    if not projects_df.empty:
        projects_df['종료일'] = pd.to_datetime(projects_df['종료일'])
    return managers_df, projects_df

# --- 3. 데이터 저장 함수 ---
def save_data(sheet, managers_df, projects_df):
    worksheet_m = sheet.worksheet("managers")
    worksheet_m.clear()
    worksheet_m.update([managers_df.columns.values.tolist()] + managers_df.values.tolist())
    
    worksheet_p = sheet.worksheet("projects")
    worksheet_p.clear()
    projects_copy = projects_df.copy()
    projects_copy['종료일'] = projects_copy['종료일'].dt.strftime('%Y-%m-%d')
    worksheet_p.update([projects_copy.columns.values.tolist()] + projects_copy.values.tolist())

# --- 앱 시작 ---
st.set_page_config(page_title="스마트 건설 PMS (Cloud)", layout="wide")
st.title("🏗️ 스마트 건설 프로젝트 관리 시스템 (Cloud)")

sheet = connect_to_gsheets()

if sheet:
    managers_df, projects_df = load_data(sheet)
    weather_data = {"서울": "☀️ 맑음", "부산": "☁️ 흐림", "대구": "🌧️ 비", "광주": "☀️ 맑음", "인천": "💨 바람"}

    # --- 사이드바: 새 프로젝트 배정 ---
    st.sidebar.header("➕ 새 프로젝트 배정")
    new_p_name = st.sidebar.text_input("현장명")
    
    if '상태' in managers_df.columns:
        available_managers = managers_df[managers_df['상태'] == '휴식중']['이름'].tolist()
    else:
        available_managers = managers_df['이름'].tolist()

    if not available_managers:
        st.sidebar.warning("가용 인력이 없습니다. [인력 관리] 탭에서 상태를 변경하세요.")
        selected_manager = st.sidebar.selectbox("직접 선택", managers_df['이름'].tolist())
    else:
        selected_manager = st.sidebar.selectbox("배정할 소장 선택", available_managers)

    new_p_location = st.sidebar.selectbox("지역", ["서울", "부산", "대구", "광주", "인천"])
    new_p_end_date = st.sidebar.date_input("종료 예정일", datetime.now() + timedelta(days=30))

    if st.sidebar.button("프로젝트 생성 및 배정"):
        if new_p_name:
            loc_map = {"서울": (37.5665, 126.9780), "부산": (35.1796, 129.0756), "대구": (35.8714, 128.6014), "광주": (35.1595, 126.8526), "인천": (37.4563, 126.7052)}
            lat, lon = loc_map[new_p_location]
            new_project = {
                "현장명": new_p_name, "소장": selected_manager, "위치": new_p_location, 
                "위도": lat, "경도": lon, "공정": "준비 중", "종료일": pd.to_datetime(new_p_end_date)
            }
            projects_df = pd.concat([projects_df, pd.DataFrame([new_project])], ignore_index=True)
            managers_df.loc[managers_df['이름'] == selected_manager, '상태'] = '공사중'
            save_data(sheet, managers_df, projects_df)
            st.sidebar.success(f"✅ {new_p_name} 배정 완료!")
            st.rerun()
        else:
            st.sidebar.error("현장명을 입력해주세요.")

    # --- 메인 탭 구성 ---
    tab1, tab2, tab3, tab4 = st.tabs(["🗺️ 지도 & 날씨", "👷 작업 현황", "📋 프로젝트 리스트", "👥 인력 관리"])

    with tab1:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader("📍 현장 위치")
            m = folium.Map(location=[36.5, 127.5], zoom_start=7)
            for _, row in projects_df.iterrows():
                folium.Marker([row['위도'], row['경도']], popup=row['현장명'], tooltip=row['현장명']).add_to(m)
            st_folium(m, width=700, height=400)
        with col2:
            st.subheader("🌦️ 날씨")
            for city, w in weather_data.items(): st.write(f"**{city}**: {w}")

    with tab2:
        st.subheader("👷 소장님 실시간 상태")
        status_list = []
        for _, m_row in managers_df.iterrows():
            name, status = m_row['이름'], m_row['상태']
            if status == '공사중':
                p_info = projects_df[projects_df['소장'] == name]
                if not p_info.empty:
                    p = p_info.iloc[0]
                    d_day = (p['종료일'].date() - datetime.now().date()).days
                    txt = f"{p['현장명']} ({p['공정']}) | D-{d_day}"
                else: txt = "현장 정보 없음"
            else: txt = "🟢 휴식 중"
            status_list.append({"소장명": name, "유형": m_row['유형'], "상태": txt})
        st.table(pd.DataFrame(status_list))

    with tab3:
        st.subheader("📋 전체 프로젝트")
        if not projects_df.empty:
            df_display = projects_df.copy()
            df_display['잔여일수'] = (df_display['종료일'] - datetime.now()).dt.days
            st.dataframe(df_display, use_container_width=True)

    with tab4:
        st.subheader("👥 인력 정보 관리")
        edited_managers = st.data_editor(
            managers_df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={"상태": st.column_config.SelectboxColumn("상태", options=["공사중", "휴식중"], required=True)}
        )
        if st.button("변경사항 구글 시트에 저장"):
            save_data(sheet, edited_managers, projects_df)
            st.success("✅ 저장 완료!")
            st.rerun()

else:
    st.error("구글 시트 연결에 실패했습니다.")
