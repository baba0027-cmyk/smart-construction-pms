import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import folium
from streamlit_folium import st_folium
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- 0. [핵심] 자동 컬럼 교정 엔진 ---
def fix_column_names(df):
    """구글 시트의 컬럼 이름이 제각각이어도 표준 이름으로 교정합니다."""
    if df.empty:
        return df
    
    # 매핑 규칙: { '표준이름': ['사용자가 쓸만한 이름들'] }
    mapping = {
        "현장명": ["현장명", "현장 이름", "현장명(명)"],
        "소장": ["소장", "현장소장", "소장명", "담당자"],
        "위치": ["위치", "현장위치", "지역"],
        "위도": ["위도", "lat", "latitude"],
        "경도": ["경도", "lon", "longitude"],
        "공사 시작일": ["공사 시작일", "시작일", "공사시작일", "시작 예정일"],
        "종료일": ["종료일", "종료(예정)일", "종료예정일", "종료일(예정)"],
        "안전 등급": ["안전 등급", "안전등급", "안전", "안전상태"],
        "공정": ["공정", "진행상태", "공정상태", "상태"],
        "구조물 공정율": ["구조물 공정율", "구조물공정율", "구조물%", "구조물 공정"],
        "전기 공정율": ["전기 공정율", "전기공정율", "전기%", "전기 공정"]
    }
    
    new_columns = {}
    for col in df.columns:
        found = False
        for standard_name, aliases in mapping.items():
            if col.strip() in aliases:
                new_columns[col] = standard_name
                found = True
                break
        if not found:
            new_columns[col] = col # 매핑 안되면 그대로 유지
            
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
        st.error(f"구글 시트 연결 실패! 원인: {e}")
        return None

# --- 2. 데이터 로드 ---
@st.cache_data(ttl=300)
def load_data_from_sheet():
    sheet = connect_to_gsheets()
    if sheet is None: return pd.DataFrame(), pd.DataFrame()
    
    try:
        # 데이터 로드 후 바로 컬럼 교정 적용
        managers_df = pd.DataFrame(sheet.worksheet("managers").get_all_records())
        managers_df = fix_column_names(managers_df)
        
        projects_df = pd.DataFrame(sheet.worksheet("projects").get_all_records())
        projects_df = fix_column_names(projects_df)
        
        if not projects_df.empty:
            # 날짜 형식 변환 (에러 방지를 위해 errors='coerce' 사용)
            for col in ['공사 시작일', '종료일']:
                if col in projects_df.columns:
                    projects_df[col] = pd.to_datetime(projects_df[col], errors='coerce')
            
            # 공정율 숫자 변환
            for col in ["구조물 공정율", "전기 공정율"]:
                if col in projects_df.columns:
                    projects_df[col] = pd.to_numeric(projects_df[col], errors='coerce').fillna(0)
        return managers_df, projects_df
    except Exception as e:
        st.error(f"데이터 로드 중 오류 발생: {e}")
        return pd.DataFrame(), pd.DataFrame()

# --- 3. 데이터 저장 ---
def save_data_to_sheet(sheet, managers_df, projects_df):
    try:
        # 1. Managers 저장
        ws_m = sheet.worksheet("managers")
        ws_m.clear()
        ws_m.update([managers_df.columns.values.tolist()] + managers_df.values.tolist())
        
        # 2. Projects 저장
        ws_p = sheet.worksheet("projects")
        ws_p.clear()
        projects_copy = projects_df.copy()
        # 날짜를 다시 문자열로 변환 (저장용)
        for col in ['공사 시작일', '종료일']:
            if col in projects_copy.columns:
                projects_copy[col] = projects_copy[col].dt.strftime('%Y-%m-%d')
        
        ws_p.update([projects_copy.columns.values.tolist()] + projects_copy.values.tolist())
        
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

# --- 4. 로그인 및 권한 관리 ---
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

# --- 5. 메인 앱 실행 ---
st.set_page_config(page_title="스마트 건설 PMS Pro", layout="wide")
st.title("🏗️ 스마트 건설 프로젝트 관리 시스템 Pro")

user_role = handle_auth()
sheet = connect_to_gsheets()

if sheet:
    managers_df, projects_df = load_data_from_sheet()
    
    if managers_df.empty and projects_df.empty:
        st.warning("데이터를 불러올 수 없습니다. 구글 시트 내용을 확인하세요.")
        st.stop()

    weather_data = {"서울": "☀️ 맑음", "부산": "☁️ 흐림", "대구": "🌧️ 비", "광주": "☀️ 맑음", "인천": "💨 바람", "울산": "☀️ 맑음", "대전": "☁️ 흐림", "제주": "🌦️ 비", "세종": "☀️ 맑음", "창원": "☀️ 맑음"}

    # --- 관리자용 기능 (사이드바) ---
    if user_role == "admin":
        st.sidebar.markdown("---")
        st.sidebar.header("➕ 새 프로젝트 배정")
        new_p_name = st.sidebar.text_input("현장명")
        
        available_managers = managers_df[managers_df['상태'] == '휴식중']['이름'].tolist() if '상태' in managers_df.columns else managers_df['이름'].tolist()
        selected_manager = st.sidebar.selectbox("배정할 소장 선택", available_managers if available_managers else managers_df['이름'].tolist())
        
        new_p_location = st.sidebar.selectbox("위치(지역)", list(weather_data.keys()))
        new_p_start_date = st.sidebar.date_input("공사 시작일", datetime.now())
        new_p_end_date = st.sidebar.date_input("종료 예정일", datetime.now() + timedelta(days=30))
        new_p_safety = st.sidebar.selectbox("초기 안전 등급", ["정상", "주의", "위험"])

        if st.sidebar.button("프로젝트 생성 및 배정"):
            if new_p_name:
                loc_map = {"서울": (37.5665, 126.9780), "부산": (35.1796, 129.0756), "대구": (35.8714, 128.6014), "광주": (35.1595, 126.8526), "인천": (37.4563, 126.7052), "울산": (35.5384, 129.3114), "대전": (36.3504, 127.3845), "제주": (33.4890, 126.4983), "세종": (36.4800, 127.2890), "창원": (35.2271, 128.6811)}
                lat, lon = loc_map.get(new_p_location, (36.5, 127.5))
                
                new_project = {
                    "현장명": new_p_name, "소장": selected_manager, "위치": new_p_location, 
                    "위도": lat, "경도": lon, "공사 시작일": pd.to_datetime(new_p_start_date),
                    "안전 등급": new_p_safety, "공정": "준비 중", "종료일": pd.to_datetime(new_p_end_date),
                    "구조물 공정율": 0, "전기 공정율": 0
                }
                projects_df = pd.concat([projects_df, pd.DataFrame([new_project])], ignore_index=True)
                if '상태' in managers_df.columns:
                    managers_df.loc[managers_df['이름'] == selected_manager, '상태'] = '공사중'
                
                if save_data_to_sheet(sheet, managers_df, projects_df):
                    st.sidebar.success(f"✅ {new_p_name} 배정 완료!")
                    st.rerun()
            else:
                st.sidebar.error("현장명을 입력해주세요.")

    # --- 메인 화면 탭 구성 ---
    tab1, tab2, tab3, tab4 = st.tabs(["🗺️ 지도/날씨", "👷 작업현황", "📋 프로젝트", "👥 인력관리"])

    with tab1:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader("📍 현장 위치")
            m = folium.Map(location=[36.5, 127.5], zoom_start=7)
            for _, row in projects_df.iterrows():
                if '위도' in row and '경도' in row:
                    folium.Marker([row['위도'], row['경도']], popup=row['현장명'], tooltip=row['현장명']).add_to(m)
            st_folium(m, width=700, height=400)
        with col2:
            st.subheader("🌦️ 지역별 날씨")
            for city, w in weather_data.items(): st.write(f"**{city}**: {w}")

    with tab2:
        st.subheader("👷 소장님 실시간 상태 및 공정율")
        status_list = []
        for _, m_row in managers_df.iterrows():
            name, status = m_row['이름'], m_row['상태']
            struct_val, elec_val = 0, 0
            txt = "🟢 휴식 중"
            if status == '공사중':
                p_info = projects_df[projects_df['소장'] == name]
                if not p_info.empty:
                    p = p_info.iloc[0]
                    # 종료일이 날짜형인지 확인 후 D-day 계산
                    if pd.notnull(p.get('종료일')):
                        d_day = (pd.to_datetime(p['종료일']).date() - datetime.now().date()).days
                        txt = f"{p['현장명']} (D-{d_day})"
                    else:
                        txt = f"{p['현장명']} (날짜미지정)"
                    struct_val = p.get('구조물 공정율', 0)
                    elec_val = p.get('전기 공정율', 0)
                else: txt = "현장 정보 없음"
            status_list.append({"소장명": name, "유형": m_row.get('유형', '-'), "상태": txt, "🏗️ 구조물(%)": struct_val, "⚡ 전기(%)": elec_val})
        st.table(pd.DataFrame(status_list))

    with tab3:
        st.subheader("📋 프로젝트 전체 정보 관리")
        if user_role == "admin":
            st.info("💡 관리자 모드: 표를 수정하고 아래 버튼을 눌러 저장하세요. (위도/경도는 숨겨져 있습니다)")
            
            # [핵심] 컬럼 설정 (데이터가 있을 때만 적용되도록 안전하게 설계)
            column_configuration = {}
            if "위도" in projects_df.columns: column_configuration["위도"] = None
            if "경도" in projects_df.columns: column_configuration["경도"] = None
            if "구조물 공정율" in projects_df.columns:
                column_configuration["구조물 공정율"] = st.column_config.ProgressColumn("구조물 공정율", min_value=0, max_value=100, format="%d%%")
            if "전기 공정율" in projects_df.columns:
                column_configuration["전기 공정율"] = st.column_config.ProgressColumn("전기 공정율", min_value=0, max_value=100, format="%d%%")
            if "안전 등급" in projects_df.columns:
                column_configuration["안전 등급"] = st.column_config.SelectboxColumn("안전 등급", options=["정상", "주의", "위험"])
            if "공정" in projects_df.columns:
                column_configuration["공정"] = st.column_config.SelectboxColumn("공정", options=["준비 중", "공사 중", "일시 중단", "완료"])
            
            edited_projects = st.data_editor(projects_df, column_config=column_configuration, use_container_width=True)
            
            if st.button("💾 프로젝트 변경사항 구글 시트에 저장"):
                if save_data_to_sheet(sheet, managers_df, edited_projects):
                    st.success("✅ 모든 프로젝트 정보가 저장되었습니다!")
                    st.rerun()
        else:
            st.warning("⚠️ 조회자 모드: 데이터는 읽기 전용입니다.")
            # 조회자에게도 위도/경도는 숨겨서 보여줌
            display_df = projects_df.drop(columns=['위도', '경도'], errors='ignore')
            st.dataframe(display_df, use_container_width=True)

    with tab4:
        st.subheader("👥 인력 정보 관리")
        if user_role == "admin":
            edited_managers = st.data_editor(managers_df, num_rows="dynamic", use_container_width=True, column_config={"상태": st.column_config.SelectboxColumn("상태", options=["공사중", "휴식중"])})
            if st.button("변경사항 구글 시트에 저장"):
                if save_data_to_sheet(sheet, edited_managers, projects_df):
                    st.success("✅ 인력 정보가 저장되었습니다!")
                    st.rerun()
        else:
            st.warning("⚠️ 조회자 모드: 데이터는 읽기 전용입니다.")
            st.dataframe(managers_df, use_container_width=True)

else:
    st.error("구글 시트 연결에 실패했습니다. Secrets 설정을 확인하세요.")
