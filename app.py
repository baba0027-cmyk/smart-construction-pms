import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import folium
from streamlit_folium import st_folium
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- 1. 구글 시트 연결 설정 (에러 방지 로직 적용) ---
@st.cache_resource
def connect_to_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        # [안전 장치] st.secrets가 있는지, 그리고 그 안에 gcp_json이 있는지 아주 조심스럽게 확인합니다.
        use_secrets = False
        try:
            if "gcp_json" in st.secrets:
                use_secrets = True
        except Exception:
            # secrets 파일 자체가 없으면 바로 에러가 나므로 여기서 catch 합니다.
            pass

        if use_secrets:
            # 온라인 배포 환경 (Streamlit Cloud)
            json_string = st.secrets["gcp_json"]
            creds_dict = json.loads(json_string, strict=False)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            # 내 컴퓨터 환경 (로컬) - credentials.json 파일을 사용합니다.
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        
        client = gspread.authorize(creds)
        sheet = client.open_by_key("1p-m_7hhsKMRacNlejARKExVtTfQrkAaJpZ_zm7_EZco") 
        return sheet
    except Exception as e:
        st.error(f"구글 시트 연결 실패! 원인: {e}")
        return None

# --- 2. 데이터 로드 함수 ---
@st.cache_data(ttl=300)
def load_data(sheet_key):
    sheet = connect_to_gsheets()
    if sheet is None:
        return pd.DataFrame(), pd.DataFrame()
    
    managers_df = pd.DataFrame(sheet.worksheet("managers").get_all_records())
    projects_df = pd.DataFrame(sheet.worksheet("projects").get_all_records())
    
    if not projects_df.empty:
        projects_df['종료일'] = pd.to_datetime(projects_df['종료일'])
        for col in ["구조물 공정율", "전기 공정율"]:
            if col not in projects_df.columns:
                projects_df[col] = 0
                
    return managers_df, projects_df

# --- 3. 데이터 저장 함수 ---
def save_data(sheet, managers_df, projects_df):
    try:
        worksheet_m = sheet.worksheet("managers")
        worksheet_m.clear()
        worksheet_m.update([managers_df.columns.values.tolist()] + managers_df.values.tolist())
        
        worksheet_p = sheet.worksheet("projects")
        worksheet_p.clear()
        projects_copy = projects_df.copy()
        projects_copy['종료일'] = projects_copy['종료일'].dt.strftime('%Y-%m-%d')
        worksheet_p.update([projects_copy.columns.values.tolist()] + projects_copy.values.tolist())
        
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

# --- 4. [수정됨] 로그인 및 권한 관리 로직 (에러 방지 완벽 적용) ---
def handle_auth():
    st.sidebar.title("🔐 접속 권한 설정")
    auth_mode = st.sidebar.radio("접속 모드를 선택하세요", ["조회자 (현황 공유용)", "관리자 (수정/관리용)"])
    
    user_role = "viewer"
    
    if auth_mode == "관리자 (수정/관리용)":
        password = st.sidebar.text_input("관리자 비밀번호", type="password")
        
        # [핵심 수정] st.secrets에 접근할 때 발생하는 에러를 원천 차단합니다.
        admin_pw = "1234" # 기본값 설정
        try:
            if "ADMIN_PW" in st.secrets:
                admin_pw = st.secrets["ADMIN_PW"]
        except Exception:
            # 금고 파일이 없으면 에러를 내지 않고 기본값(1234)을 유지합니다.
            pass
        
        if password == admin_pw:
            user_role = "admin"
            st.sidebar.success("✅ 관리자 모드 활성화")
        elif password != "":
            st.sidebar.error("❌ 비밀번호가 틀렸습니다.")
            user_role = "viewer"
        else:
            st.sidebar.info("비밀번호를 입력해주세요.")
            user_role = "viewer"
    else:
        st.sidebar.info("👁️ 조회자 모드 (읽기 전용)")
        user_role = "viewer"
        
    return user_role

# --- 앱 시작 ---
st.set_page_config(page_title="스마트 건설 PMS (Cloud)", layout="wide")
st.title("🏗️ 스마트 건설 프로젝트 관리 시스템 (Cloud)")

# [권한 체크 실행]
user_role = handle_auth()

sheet = connect_to_gsheets()
SHEET_KEY = "1p-m_7hhsKMRacNlejARKExVtTfQrkAaJpZ_zm7_EZco"

if sheet:
    managers_df, projects_df = load_data(SHEET_KEY)
    
    if managers_df.empty:
        st.warning("데이터를 불러올 수 없습니다. 구글 시트 설정을 확인하세요.")
        st.stop()

    weather_data = {
        "서울": "☀️ 맑음", "부산": "☁️ 흐림", "대구": "🌧️ 비", "광주": "☀️ 맑음", 
        "인천": "💨 바람", "울산": "☀️ 맑음", "대전": "☁️ 흐림", "제주": "🌦️ 비", 
        "세종": "☀️ 맑음", "창원": "☀️ 맑음"
    }

    # --- [권한 제어] 사이드바: 관리자에게만 새 프로젝트 배정 메뉴 노출 ---
    if user_role == "admin":
        st.sidebar.markdown("---")
        st.sidebar.header("➕ 새 프로젝트 배정")
        new_p_name = st.sidebar.text_input("현장명")
        
        if '상태' in managers_df.columns:
            available_managers = managers_df[managers_df['상태'] == '휴식중']['이름'].tolist()
        else:
            available_managers = managers_df['이름'].tolist()

        if not available_managers:
            st.sidebar.warning("가용 인력이 없습니다.")
            selected_manager = st.sidebar.selectbox("직접 선택", managers_df['이름'].tolist())
        else:
            selected_manager = st.sidebar.selectbox("배정할 소장 선택", available_managers)

        new_p_location = st.sidebar.selectbox("지역", list(weather_data.keys()))
        new_p_end_date = st.sidebar.date_input("종료 예정일", datetime.now() + timedelta(days=30))

        if st.sidebar.button("프로젝트 생성 및 배정"):
            if new_p_name:
                loc_map = {
                    "서울": (37.5665, 126.9780), "부산": (35.1796, 129.0756), "대구": (35.8714, 128.6014), 
                    "광주": (35.1595, 126.8526), "인천": (37.4563, 126.7052), "울산": (35.5384, 129.3114),
                    "대전": (36.3504, 127.3845), "제주": (33.4890, 126.4983), "세종": (36.4800, 127.2890),
                    "창원": (35.2271, 128.6811)
                }
                lat, lon = loc_map.get(new_p_location, (36.5, 127.5))
                new_project = {
                    "현장명": new_p_name, "소장": selected_manager, "위치": new_p_location, 
                    "위도": lat, "경도": lon, "공정": "준비 중", "종료일": pd.to_datetime(new_p_end_date),
                    "구조물 공정율": 0, "전기 공정율": 0
                }
                projects_df = pd.concat([projects_df, pd.DataFrame([new_project])], ignore_index=True)
                managers_df.loc[managers_df['이름'] == selected_manager, '상태'] = '공사중'
                
                if save_data(sheet, managers_df, projects_df):
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
            st.subheader("🌦️ 지역별 날씨")
            for city, w in weather_data.items(): 
                st.write(f"**{city}**: {w}")

    with tab2:
        st.subheader("👷 소장님 실시간 상태 및 공정율")
        status_list = []
        for _, m_row in managers_df.iterrows():
            name, status = m_row['이름'], m_row['상태']
            struct_val, elec_val = 0, 0
            
            if status == '공사중':
                p_info = projects_df[projects_df['소장'] == name]
                if not p_info.empty:
                    p = p_info.iloc[0]
                    d_day = (p['종료일'].date() - datetime.now().date()).days
                    txt = f"{p['현장명']} (D-{d_day})"
                    struct_val = p.get('구조물 공정율', 0)
                    elec_val = p.get('전기 공정율', 0)
                else: txt = "현장 정보 없음"
            else: txt = "🟢 휴식 중"
            
            status_list.append({
                "소장명": name, 
                "유형": m_row['유형'], 
                "상태": txt, 
                "🏗️ 구조물(%)": struct_val, 
                "⚡ 전기(%)": elec_val
            })
        st.table(pd.DataFrame(status_list))

    with tab3:
        st.subheader("📋 프로젝트 전체 정보 관리")
        if user_role == "admin":
            st.info("💡 관리자 모드: 표를 직접 수정하고 아래 버튼을 눌러 저장하세요.")
            edited_projects = st.data_editor(
                projects_df, 
                use_container_width=True,
                num_rows="fixed"
            )
            if st.button("💾 프로젝트 변경사항 구글 시트에 저장"):
                if save_data(sheet, managers_df, edited_projects):
                    st.success("✅ 모든 프로젝트 정보가 저장되었습니다!")
                    st.rerun()
        else:
            st.warning("⚠️ 조회자 모드: 데이터는 읽기 전용입니다.")
            st.dataframe(projects_df, use_container_width=True)

    with tab4:
        st.subheader("👥 인력 정보 관리")
        if user_role == "admin":
            edited_managers = st.data_editor(
                managers_df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={"상태": st.column_config.SelectboxColumn("상태", options=["공사중", "휴식중"], required=True)}
            )
            if st.button("변경사항 구글 시트에 저장"):
                if save_data(sheet, edited_managers, projects_df):
                    st.success("✅ 인력 정보가 저장되었습니다!")
                    st.rerun()
        else:
            st.warning("⚠️ 조회자 모드: 데이터는 읽기 전용입니다.")
            st.dataframe(managers_df, use_container_width=True)

else:
    st.error("구글 시트 연결에 실패했습니다.")
