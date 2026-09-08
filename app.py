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

# --- 0. [Master Schema] 우리 앱의 절대적인 데이터 표준 정의 ---
PROJECTS_SCHEMA = ["현장", "소장", "용량 (MW)", "위치", "안전 등급", "공정", "구조물 공정율", "전기 공정율", "공사 시작일", "종료일", "위도", "경도"]
MANAGERS_SCHEMA = ["현장", "소장", "이름", "예정 구조물", "예정 전기", "누적 구조물", "누적 전기", "총 인원"]

# --- 1. [Engine] 컬럼 매핑 및 데이터 표준화 엔진 ---
def fix_column_names(df):
    if df.empty: return df
    mapping = {
        "현장": ["현장", "현장명", "현장 이름", "현장명(명)", "대상현장"],
        "소장": ["소장", "현장소장", "소장명", "담당자"],
        "위치": ["위치", "현장위치", "지역"],
        "위도": ["위도", "lat", "latitude"],
        "경도": ["경도", "lon", "longitude"],
        "공사 시작일": ["공사 시작일", "시작일", "공사시작일", "시작 예정일"],
        "종료일": ["종료일", "종료(예정)일", "종료예정일", "종료일(예정)"],
        "안전 등급": ["안전 등급", "안전등급", "안전", "안전상태"],
        "공정": ["공정", "진행상태", "공정상태", "프로젝트상태"],
        "구조물 공정율": ["구조물 공정율", "구조물공정율", "구조물%", "구조물 공정"],
        "전기 공정율": ["전기 공정율", "전기공정율", "전기%", "전기 공정"],
        "이름": ["이름", "성함", "성명"],
        "예정 구조물": ["예정 구조물", "예정 구조물 인원", "계획 구조물", "예정 구조"],
        "예정 전기": ["예정 전기", "예정 전기 인원", "계획 전기", "예정 전기"],
        "누적 구조물": ["누적 구조물", "누적 구조물 인원", "실적 구조물", "누적 구조"],
        "누적 전기": ["누적 전기", "누적 전기 인원", "실적 전기", "누적 전기"],
        "총 인원": ["총 인원", "합계 인원", "전체 인원", "투입인원", "투입인원수"],
        "용량 (MW)": ["용량 (MW)", "용량(MW)", "MW", "용량", "규모"] 
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
    """기존 데이터를 마스터 스키마에 맞춰 강제로 정렬하고 부족한 값은 채웁니다."""
    df = fix_column_names(df)
    new_df = pd.DataFrame(index=df.index, columns=schema)
    
    for col in schema:
        if col in df.columns:
            if col in ["용량 (MW)", "구조물 공정율", "전기 공정율", "예정 구조물", "예정 전기", "누적 구조물", "누적 전기", "총 인원"]:
                new_df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            elif col in ["공사 시작일", "종료일"]:
                new_df[col] = pd.to_datetime(df[col], errors='coerce')
            elif col in ["위도", "경도"]:
                new_df[col] = pd.to_numeric(df[col], errors='coerce').fillna(36.5)
            else:
                new_df[col] = df[col].fillna("").astype(object)
        else:
            if col in ["용량 (MW)", "구조물 공정율", "전기 공정율", "예정 구조물", "예정 전기", "누적 구조물", "누적 전기", "총 인원"]:
                new_df[col] = 0.0
            elif col in ["공사 시작일", "종료일"]:
                new_df[col] = datetime.now()
            elif col in ["위도", "경도"]:
                new_df[col] = 36.5
            else:
                new_df[col] = ""
                
    if "공사 시작일" in new_df.columns: new_df["공사 시작일"] = new_df["공사 시작일"].fillna(datetime.now())
    if "종료일" in new_df.columns: new_df["종료일"] = new_df["종료일"].fillna(datetime.now())
    
    return new_df

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

# --- 3. 데이터 로드 (표준화 적용) ---
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

# --- 4. 데이터 저장 (안전한 저장) ---
def save_data_to_sheet(sheet, managers_df, projects_df):
    try:
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
st.set_page_config(page_title="스마트 건설 PMS Pro", layout="wide")

sheet = connect_to_gsheets()
if sheet:
    managers_df, projects_df = load_data_from_sheet()
    user_role = handle_auth()
    st.title("🏗️ 스마트 건설 프로젝트 관리 시스템 Pro")

    # --- [Sidebar] 관리자 전용 기능 ---
    if user_role == "admin":
        # [A] 신규 현장 등록
        with st.sidebar.expander("🚀 신규 현장 즉시 등록", expanded=False):
            with st.form("quick_add_site_form", clear_on_submit=True): # clear_on_submit 추가로 중복 생성 방지
                new_site_name = st.text_input("📍 신규 현장명 *")
                new_site_manager = st.text_input("👤 현장 소장")
                new_site_mw = st.number_input("⚡ 용량 (MW)", min_value=0.0, step=0.1)
                new_site_loc = st.text_input("🗺️ 위치 (예: 전남 고흥)")
                submit_new_site = st.form_submit_button("🆕 현장 생성 및 시트 반영")
                
                if submit_new_site:
                    if not new_site_name:
                        st.error("현장명은 반드시 입력해야 합니다!")
                    elif new_site_name in projects_df['현장'].values:
                        st.error("이미 존재하는 현장명입니다!")
                    else:
                        # 마스터 스키마에 맞춰 데이터 생성
                        new_p_data = {col: "" for col in PROJECTS_SCHEMA}
                        new_p_data.update({
                            "현장": new_site_name, "소장": new_site_manager, "용량 (MW)": new_site_mw,
                            "위치": new_site_loc, "안전 등급": "정상", "공정": "준비 중",
                            "구조물 공정율": 0.0, "전기 공정율": 0.0, "공사 시작일": datetime.now(), "종료일": datetime.now(),
                            "위도": 36.5, "경도": 127.5
                        })
                        new_m_data = {col: 0 for col in MANAGERS_SCHEMA}
                        new_m_data.update({
                            "현장": new_site_name, "소장": new_site_manager,
                            "예정 구조물": 0, "예정 전기": 0, "누적 구조물": 0, "누적 전기": 0, "총 인원": 0
                        })
                        
                        updated_p = pd.concat([projects_df, pd.DataFrame([new_p_data])], ignore_index=True)
                        updated_m = pd.concat([managers_df, pd.DataFrame([new_m_data])], ignore_index=True)
                        
                        if save_data_to_sheet(sheet, updated_m, updated_p):
                            st.success(f"✅ '{new_site_name}' 생성 완료!")
                            st.rerun()

        # [B] 현장 삭제 기능 (신규 추가됨)
        with st.sidebar.expander("🗑️ 현장 삭제 (관리자용)", expanded=False):
            if not projects_df.empty:
                site_to_delete = st.selectbox("삭제할 현장 선택", projects_df['현장'].tolist(), key="delete_site_sel")
                st.warning(f"⚠️ '{site_to_delete}' 현장을 삭제하면 모든 인력 데이터도 함께 삭제됩니다.")
                confirm_delete = st.checkbox("❌ 삭제를 확정합니다", key="confirm_delete_check")
                
                if st.button("🚨 현장 삭제 실행", key="btn_delete_exec"):
                    if confirm_delete:
                        # 데이터 필터링 (해당 현장 제외)
                        updated_p = projects_df[projects_df['현장'] != site_to_delete]
                        updated_m = managers_df[managers_df['현장'] != site_to_delete]
                        
                        if save_data_to_sheet(sheet, updated_m, updated_p):
                            st.error(f"✅ '{site_to_delete}' 현장이 삭제되었습니다!")
                            st.rerun()
                    else:
                        st.info("⚠️ 삭제를 확정하려면 체크박스를 선택해주세요.")
            else:
                st.info("삭제할 현장이 없습니다.")

    # --- 상단 알림 ---
    if not projects_df.empty:
        high_risk = projects_df[projects_df['안전 등급'].astype(str) == '위험']['현장'].tolist()
        if high_risk: st.error(f"⚠️ **긴급 알림**: 위험 현장 [{', '.join(high_risk)}] 관리가 필요합니다!")

    tab_dash, tab1, tab2, tab3, tab4 = st.tabs(["📊 종합 대시보드", "🗺️ 지도/날씨", "👷 인력 투입 비교 (Plan vs Act)", "📋 프로젝트", "👥 인력/자원 관리"])

    # --- [Tab 0] 종합 대시보드 ---
    with tab_dash:
        if not projects_df.empty:
            st.subheader("📈 핵심 현황 지표 (Summary)")
            kpi1, kpi2, kpi3, kpi4 = st.columns(4)
            kpi1.metric("총 현장 수", f"{len(projects_df)} 개")
            kpi2.metric("총 용량 (MW)", f"{projects_df['용량 (MW)'].sum():.1f} MW")
            kpi3.metric("총 투입 인원", f"{managers_df['총 인원'].sum():.0f} 명")
            kpi4.metric("위험 현장", f"{len(projects_df[projects_df['안전 등급'].astype(str) == '위험'])} 개", delta_color="inverse")
            
            st.divider()
            st.subheader("📊 현장별 공정 진행 현황 (%)")
            fig_bar = px.bar(projects_df, x="현장", y=["구조물 공정율", "전기 공정율"], barmode="group", title="현장별 구조물 vs 전기 공정율 비교", color_discrete_sequence=["#1f77b4", "#ff7f0e"])
            st.plotly_chart(fig_bar, use_container_width=True)

    # --- [Tab 1] 지도 ---
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
            st.subheader("🌦️ 지역별 날씨")
            st.write("☀️ 서울: 맑음")

    # --- [Tab 2] 인력 비교 차트 ---
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
                    "총 인원": st.column_config.NumberColumn("📊 총 인원", format="%d")
                }
                edited_m = st.data_editor(managers_df, column_config=col_config, use_container_width=True, key="editor_tab2")
                if st.button("💾 변경사항 저장", key="btn_save_tab2"):
                    if save_data_to_sheet(sheet, edited_m, projects_df): st.success("✅ 저장 완료!"); st.rerun()
            else:
                st.dataframe(managers_df, use_container_width=True)

    # --- [Tab 3] 프로젝트 상세 ---
    with tab3:
        st.subheader("📋 프로젝트 상세 정보")
        if user_role == "admin":
            col_config = {
                "구조물 공정율": st.column_config.ProgressColumn("구조물 %", min_value=0, max_value=100, format="%d%%"),
                "전기 공정율": st.column_config.ProgressColumn("전기 %", min_value=0, max_value=100, format="%d%%"),
                "용량 (MW)": st.column_config.NumberColumn("용량 (MW)", format="%.2f"),
                "안전 등급": st.column_config.SelectboxColumn("안전", options=["정상", "주의", "위험"]),
                "공정": st.column_config.SelectboxColumn("공정", options=["준비 중", "공사 중", "일시 중단", "완료"])
            }
            edited_p = st.data_editor(projects_df, column_config=col_config, use_container_width=True, key="editor_tab3")
            if st.button("💾 프로젝트 변경사항 저장", key="btn_save_tab3"):
                if save_data_to_sheet(sheet, managers_df, edited_p): st.success("✅ 저장 완료!"); st.rerun()
            st.download_button("📥 엑셀 다운로드", export_to_excel(projects_df), "projects.xlsx", key="btn_dl_excel")
        else:
            st.dataframe(projects_df.drop(columns=['위도', '경도'], errors='ignore'), use_container_width=True)

    # --- [Tab 4] 인력 관리 ---
    with tab4:
        st.subheader("👥 전체 인력/자원 관리")
        if user_role == "admin":
            if '이름' in managers_df.columns and managers_df['이름'].nunique() > len(managers_df):
                st.info("💡 [소장님 개인별 관리 모드]")
                st.data_editor(managers_df, use_container_width=True, key="editor_tab4_mgr")
            else:
                st.info("💡 [현장별 인력 투입 관리 모드]")
                col_config = {
                    "예정 구조물": st.column_config.NumberColumn("🏗️ 예정(구조)", format="%d"),
                    "예정 전기": st.column_config.NumberColumn("⚡ 예정(전기)", format="%d"),
                    "누적 구조물": st.column_config.NumberColumn("🏗️ 누적(구조)", format="%d"),
                    "누적 전기": st.column_config.NumberColumn("⚡ 누적(전기)", format="%d"),
                    "총 인원": st.column_config.NumberColumn("📊 총 인원", format="%d")
                }
                edited_m = st.data_editor(managers_df, column_config=col_config, use_container_width=True, key="editor_tab4_site")
                if st.button("💾 저장", key="btn_save_tab4"):
                    if save_data_to_sheet(sheet, edited_m, projects_df): st.success("✅ 저장 완료!"); st.rerun()
        else:
            st.dataframe(managers_df, use_container_width=True)

else:
    st.error("구글 시트 연결 실패")
