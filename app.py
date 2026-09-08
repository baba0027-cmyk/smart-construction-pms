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

# --- 0. [엔진 교정] 중복 방지 및 정확한 매핑 엔진 ---
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
        "상태": ["상태", "인력상태", "근무상태", "현장상태"],
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

# --- 2. 데이터 로드 (방어적 프로그래밍) ---
@st.cache_data(ttl=300)
def load_data_from_sheet():
    sheet = connect_to_gsheets()
    if sheet is None: return pd.DataFrame(), pd.DataFrame()
    try:
        managers_df = pd.DataFrame(sheet.worksheet("managers").get_all_records())
        managers_df = fix_column_names(managers_df)
        manpower_cols = ["예정 구조물", "예정 전기", "누적 구조물", "누적 전기", "총 인원"]
        for col in manpower_cols:
            if col not in managers_df.columns: managers_df[col] = 0
            else: managers_df[col] = pd.to_numeric(managers_df[col], errors='coerce').fillna(0)
        
        projects_df = pd.DataFrame(sheet.worksheet("projects").get_all_records())
        projects_df = fix_column_names(projects_df)
        
        if not projects_df.empty:
            essential_proj_cols = {
                "현장": "미지정 현장", "소장": "미지정 소장", "용량 (MW)": 0.0,
                "안전 등급": "정상", "공정": "준비 중", "구조물 공정율": 0.0,
                "전기 공정율": 0.0, "공사 시작일": datetime.now(), "종료일": datetime.now(),
                "위도": 36.5, "경도": 127.5
            }
            for col, default_val in essential_proj_cols.items():
                if col not in projects_df.columns:
                    projects_df[col] = default_val
                else:
                    if col in ['공사 시작일', '종료일']:
                        projects_df[col] = pd.to_datetime(projects_df[col], errors='coerce').fillna(datetime.now())
                    elif isinstance(default_val, (int, float)):
                        projects_df[col] = pd.to_numeric(projects_df[col], errors='coerce').fillna(default_val)
                    else:
                        projects_df[col] = projects_df[col].fillna(default_val)

            desired_order = ["현장", "소장", "용량 (MW)", "위치", "안전 등급", "공정", "구조물 공정율", "전기 공정율", "공사 시작일", "종료일", "위도", "경도"]
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
        high_risk_mask = projects_df['안전 등급'].astype(str) == '위험'
        high_risk = projects_df[high_risk_mask]['현장'].tolist()
        if high_risk: 
            st.error(f"⚠️ **긴급 알림**: 위험 현장 [{', '.join(high_risk)}] 관리가 필요합니다!")

    tab_dash, tab1, tab2, tab3, tab4 = st.tabs(["📊 종합 대시보드", "🗺️ 지도/날씨", "👷 인력 투입 비교 (Plan vs Act)", "📋 프로젝트", "👥 인력/자원 관리"])

    # --- [Tab 0] 종합 대시보드 ---
    with tab_dash:
        if not projects_df.empty:
            st.subheader("📈 핵심 현황 지표 (Summary)")
            total_sites = len(projects_df)
            total_mw = projects_df['용량 (MW)'].sum()
            total_manpower = managers_df['총 인원'].sum() if '총 인원' in managers_df.columns else 0
            high_risk_count = len(projects_df[projects_df['안전 등급'].astype(str) == '위험'])

            kpi1, kpi2, kpi3, kpi4 = st.columns(4)
            kpi1.metric("총 현장 수", f"{total_sites} 개")
            kpi2.metric("총 용량 (MW)", f"{total_mw:.1f} MW")
            kpi3.metric("총 투입 인원", f"{total_manpower:.0f} 명")
            kpi4.metric("위험 현장", f"{high_risk_count} 개", delta_color="inverse")

            st.divider()
            st.subheader("📊 현장별 공정 진행 현황 (%)")
            fig_bar = px.bar(projects_df, x="현장", y=["구조물 공정율", "전기 공정율"], 
                             barmode="group", title="현장별 구조물 vs 전기 공정율 비교",
                             color_discrete_sequence=["#1f77b4", "#ff7f0e"])
            st.plotly_chart(fig_bar, use_container_width=True)

    # --- [Tab 1] 지도/날씨 ---
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

    # --- [Tab 2] 인력 투입 비교 (개선됨: 현장명 중심 & 그룹화) ---
    with tab2:
        st.subheader("📊 현장별 인력 투입 분석 (계획 vs 누적)")
        
        required_cols = ["현장", "예정 구조물", "예정 전기", "누적 구조물", "누적 전기"]
        missing_cols = [c for c in required_cols if c not in managers_df.columns]
        
        if not missing_cols:
            # 1. 데이터 가공: 각 현장별로 4개의 데이터 포인트를 만듭니다.
            melted_data = []
            for _, row in managers_df.iterrows():
                site = str(row['현장'])
                # (구조물 - 계획), (구조물 - 누적), (전기 - 계획), (전기 - 누적)
                melted_data.append({'현장': site, '공종': '구조물', '구분': '계획(Plan)', '인원': row['예정 구조물']})
                melted_data.append({'현장': site, '공종': '구조물', '구분': '누적(Actual)', '인원': row['누적 구조물']})
                melted_data.append({'현장': site, '공종': '전기', '구분': '계획(Plan)', '인원': row['예정 전기']})
                melted_data.append({'현장': site, '공종': '전기', '구분': '누적(Actual)', '인원': row['누적 전기']})
            
            df_plot = pd.DataFrame(melted_data)

            # 2. 차트 생성 (go.Figure 사용)
            # X축을 '현장'으로 고정하고, 각 항목별로 Trace를 만들어 그룹화합니다.
            fig_man = go.Figure()

            # 항목 정의: (공종, 구분, 색상_Base, 이름)
            # '누적(Actual)'의 경우 계획보다 많으면 빨간색으로 만들기 위해 별도 로직 적용
            
            # --- Trace 1: 구조물 계획 (회색) ---
            df_struct_plan = df_plot[(df_plot['공종']=='구조물') & (df_plot['구분']=='계획(Plan)')]
            fig_man.add_trace(go.Bar(x=df_struct_plan['현장'], y=df_struct_plan['인원'], name='🏗️ 구조물(계획)', marker_color='#D3D3D3'))

            # --- Trace 2: 구조물 누적 (파랑 or 빨강) ---
            df_struct_act = df_plot[(df_plot['공종']=='구조물') & (df_plot['구분']=='누적(Actual)')]
            # 계획 대비 초과 여부 확인을 위해 원래 managers_df와 조인
            # (여기서는 단순화를 위해 df_plot 내에서 비교 로직을 쓸 수 있음)
            struct_act_colors = []
            for _, row in df_struct_act.iterrows():
                plan_val = df_plot[(df_plot['현장']==row['현장']) & (df_plot['공종']=='구조물') & (df_plot['구분']=='계획(Plan) ')]['인원'].values
                # 위 코드는 인덱싱 이슈가 있을 수 있으니 안전하게 managers_df에서 가져옴
                orig_row = managers_df[managers_df['현장'] == row['현장']]
                if not orig_row.empty and row['인원'] > orig_row['예정 구조물'].values[0]:
                    struct_act_colors.append('#EF553B') # 🔴 Red
                else:
                    struct_act_colors.append('#636EFA') # 🔵 Blue
            fig_man.add_trace(go.Bar(x=df_struct_act['현장'], y=df_struct_act['인원'], name='🏗️ 구조물(누적)', marker_color=struct_act_colors))

            # --- Trace 3: 전기 계획 (회색) ---
            df_elec_plan = df_plot[(df_plot['공종']=='전기') & (df_plot['구분']=='계획(Plan)')]
            fig_man.add_trace(go.Bar(x=df_elec_plan['현장'], y=df_elec_plan['인원'], name='⚡ 전기(계획)', marker_color='#D3D3D3'))

            # --- Trace 4: 전기 누적 (파랑 or 빨강) ---
            df_elec_act = df_plot[(df_plot['공종']=='전기') & (df_plot['구분']=='누적(Actual)')]
            elec_act_colors = []
            for _, row in df_elec_act.iterrows():
                orig_row = managers_df[managers_df['현장'] == row['현장']]
                if not orig_row.empty and row['인원'] > orig_row['예정 전기'].values[0]:
                    elec_act_colors.append('#EF553B') # 🔴 Red
                else:
                    elec_act_colors.append('#636EFA') # 🔵 Blue
            fig_man.add_trace(go.Bar(x=df_elec_act['현장'], y=df_elec_act['인원'], name='⚡ 전기(누적)', marker_color=elec_act_colors))

            fig_man.update_layout(
                barmode='group',
                title="현장별 인력 투입 현황 (🔴 빨간색: 계획 인원 초과!)",
                xaxis_title="현장명",
                yaxis_title="인원 (명)",
                legend_title="항목",
                xaxis={'type': 'category'} # 현장명이 숫자로 인식되지 않도록 강제
            )
            st.plotly_chart(fig_man, use_container_width=True)
            
            st.divider()
            
            # 3. [핵심 기능] 관리자 모드일 때 즉시 수정 가능하도록 Data Editor 배치
            st.subheader("📝 인력 투입 현황 데이터 수정")
            if user_role == "admin":
                st.info("💡 차트의 데이터를 직접 수정하고 아래 [💾 저장] 버튼을 누르면 차트와 구글 시트에 즉시 반영됩니다.")
                col_config = {
                    "현장": None,
                    "예정 구조물": st.column_config.NumberColumn("🏗️ 예정(구조)", format="%d 명"),
                    "예정 전기": st.column_config.NumberColumn("⚡ 예정(전기)", format="%d 명"),
                    "누적 구조물": st.column_config.NumberColumn("🏗️ 누적(구조)", format="%d 명"),
                    "누적 전기": st.column_config.NumberColumn("⚡ 누적(전기)", format="%d 명"),
                    "총 인원": st.column_config.NumberColumn("📊 총 인원", format="%d 명")
                }
                edited_m = st.data_editor(managers_df, column_config=col_config, use_container_width=True, key="editor_tab2")
                
                if st.button("💾 변경사항 구글 시트에 저장"):
                    if save_data_to_sheet(sheet, edited_m, projects_df):
                        st.success("✅ 저장 완료! 차트가 업데이트됩니다."); st.rerun()
            else:
                st.warning("⚠️ 조회자 모드입니다. 수정하려면 관리자 모드로 접속하세요.")
                st.dataframe(managers_df, use_container_width=True)
        else:
            st.warning(f"⚠️ 필수 데이터가 부족합니다: **{', '.join(missing_cols)}**")

    # --- [Tab 3] 프로젝트 ---
    with tab3:
        st.subheader("📋 프로젝트 상세 정보")
        if user_role == "admin":
            column_config = {
                "구조물 공정율": st.column_config.ProgressColumn("구조물 %", min_value=0, max_value=100, format="%d%%"),
                "전기 공정율": st.column_config.ProgressColumn("전기 %", min_value=0, max_value=100, format="%d%%"),
                "용량 (MW)": st.column_config.NumberColumn("용량 (MW)", format="%.2f MW"),
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

    # --- [Tab 4] 인력/자원 관리 ---
    with tab4:
        st.subheader("👥 전체 인력/자원 관리")
        if user_role == "admin":
            if '이름' in managers_df.columns:
                st.info("💡 [소장님 개인별 관리 모드]")
                st.data_editor(managers_df, use_container_width=True, key="editor_tab4")
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
                edited_m = st.data_editor(managers_df, column_config=col_config, use_container_width=True, key="editor_tab4")
                col_btn1, col_btn2 = st.columns([1, 5])
                with col_btn1:
                    if st.button("💾 저장"):
                        if save_data_to_sheet(sheet, edited_m, projects_df):
                            st.success("✅ 저장 완료!"); st.rerun()
                with col_btn2:
                    st.download_button("📥 엑셀 다운로드", export_to_excel(managers_df), "manpower.xlsx")
        else:
            st.warning("⚠️ 조회자 모드: 데이터는 읽기 전용입니다.")
            st.dataframe(managers_df, use_container_width=True)
            st.download_button("📥 엑셀 다운로드", export_to_excel(managers_df), "manpower.xlsx")

else:
    st.error("구글 시트 연결 실패")
