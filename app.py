import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

# ==========================================
# 1. 페이지 설정 및 기본 구성
# ==========================================
st.set_page_config(
    layout="wide", 
    page_title="AI 프로젝트 관리 인텔리전스",
    page_icon="📊"
)

# 커스텀 CSS (UI 개선)
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 5px 5px 0px 0px; padding: 10px 20px; }
    .stTabs [aria-selected="true"] { background-color: #0e1117; color: white; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. 데이터 로딩 및 Mock 데이터 생성 (Fallback)
# ==========================================
def generate_mock_data():
    """모든 지능형 기능을 테스트하기 위한 고도화된 한국어 샘플 데이터 생성"""
    np.random.seed(42)
    n_projects = 15
    
    projects = []
    locations = ['서울', '부산', '인천', '대구', '광주', '대전', '울산']
    
    for i in range(n_projects):
        start_date = datetime(2024, 1, 1) + timedelta(days=np.random.randint(0, 365))
        duration = np.random.randint(30, 200)
        end_date = start_date + timedelta(days=duration)
        progress = np.random.uniform(0.1, 0.95)
        contract_amount = np.random.randint(50000000, 500000000) # 계약 금액 (KRW)
        actual_cost = contract_amount * np.random.uniform(0.4, 0.9)
        
        projects.append({
            '프로젝트명': f'프로젝트 {chr(65+i)}',
            '시작일': start_date.strftime('%Y-%m-%d'),
            '종료일': end_date.strftime('%Y-%m-%d'),
            '진행률': progress,
            '계약 금액 (KRW)': contract_amount,
            '실행 비용 (KRW)': actual_cost,
            '위치': np.random.choice(locations),
            '투입 인력 (명)': np.random.randint(3, 15),
            '목표 인력 (명)': np.random.randint(5, 15),
            '리스크 요인': np.random.choice(['낮음', '보통', '높음'], p=[0.5, 0.3, 0.2]),
            '상태': np.random.choice(['진행중', '지연', '완료'], p=[0.6, 0.2, 0.2])
        })
    
    return pd.DataFrame(projects)

def load_data():
    """Google Sheets 연결 시도 및 실패 시 Mock 데이터 반환"""
    try:
        # 실제 운영 시에는 아래 주석을 해제하고 Google Sheets 연결 설정을 사용하세요.
        # conn = st.connection("gsheets", type="gsheets")
        # df = conn.read()
        # return df
        raise Exception("연결 설정되지 않음")
    except Exception:
        return generate_mock_data()

# ==========================================
# 3. 핵심 분석 엔진 (Intelligence Engines)
# ==========================================

def engine_predictive_risk(df):
    """Phase 2.1: 예측 엔진 - 리스크 점수 및 예상 종료일 계산"""
    df = df.copy()
    df['시작일'] = pd.to_datetime(df['시작일'])
    df['종료일'] = pd.to_datetime(df['종료일'])
    
    total_days = (df['종료일'] - df['시작일']).dt.days
    elapsed_days = (pd.Timestamp.now() - df['시작일']).dt.days
    time_progress = elapsed_days / total_days
    
    risk_score = (time_progress - df['진행률']) * 100
    risk_score = risk_score.clip(lower=0)
    
    risk_map = {'낮음': 0, '보통': 15, '높음': 30}
    df['리스크 점수'] = risk_score + df['리스크 요인'].map(risk_map)
    
    df['예상 종료일'] = df['종료일'] + pd.to_timedelta(df['리스크 점수'] * 2, unit='D')
    return df

def engine_labor_optimization(df):
    """Phase 2.2: 인력 최적화 - 불균형 및 재배치 제안"""
    df = df.copy()
    df['인력 불균형'] = df['투입 인력 (명)'] - df['목표 인력 (명)']
    
    def suggest_action(diff):
        if diff < -2: return "⚠️ 인력 충원 필요"
        elif diff > 2: return "💡 인력 재배치 가능"
        else: return "✅ 적정 인력"
    
    df['인력 최적화 제안'] = df['인력 불균형'].apply(suggest_action)
    return df

def engine_financial_intelligence(df):
    """Phase 2.3: 재무 지능 - 수익성 및 비용 예측"""
    df = df.copy()
    df['수익금'] = df['계약 금액 (KRW)'] - df['실행 비용 (KRW)']
    df['수익률 (%)'] = (df['수익금'] / df['계약 금액 (KRW)']) * 100
    df['예상 최종 비용 (KRW)'] = df['실행 비용 (KRW)'] * (1 + (1 - df['진행률']))
    return df

# ==========================================
# 4. 메인 앱 실행 및 UI 렌더링
# ==========================================

def main():
    st.title("🚀 AI 프로젝트 관리 인텔리전스")
    st.subheader("통합 예측 및 재무 분석 대시보드")

    # 데이터 로드 및 엔진 가동
    raw_df = load_data()
    df = engine_predictive_risk(raw_df)
    df = engine_labor_optimization(df)
    df = engine_financial_intelligence(df)

    # 사이드바 필터
    st.sidebar.header("🔍 필터 설정")
    selected_status = st.sidebar.multiselect("프로젝트 상태", options=df['상태'].unique(), default=df['상태'].unique())
    filtered_df = df[df['상태'].isin(selected_status)]

    # 탭 생성 (모두 한국어로 변경)
    tabs = st.tabs([
        "🏠 대시보드", "💰 재무 현황", "📅 간트 차트", "📍 지도", 
        "👥 인력 비교", "📈 진행률", "🔮 예측 분석", 
        "🛠️ 인력 최적화", "💎 재무 지능", "📋 마스터 데이터", "📥 데이터 내보내기"
    ])

    # --- [탭 0: 대시보드] ---
    with tabs[0]:
        st.header("전체 프로젝트 현황 요약")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("총 프로젝트 수", f"{len(filtered_df)} 개")
        col2.metric("평균 진행률", f"{filtered_df['진행률'].mean()*100:.1f}%")
        col3.metric("평균 리스크 점수", f"{filtered_df['리스크 점수'].mean():.1f}")
        col4.metric("총 계약 금액", f"{filtered_df['계약 금액 (KRW)'].sum():,.0f} 원")
        
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("### 프로젝트 상태 분포")
            fig_status = px.pie(filtered_df, names='상태', hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig_status, use_container_width=True)
        with col_b:
            st.write("### 진행률 vs 리스크 상관관계")
            fig_scatter = px.scatter(filtered_df, x='진행률', y='리스크 점수', size='계약 금액 (KRW)', color='상태', hover_name='프로젝트명')
            st.plotly_chart(fig_scatter, use_container_width=True)

    # --- [탭 1: 재무 현황] ---
    with tabs[1]:
        st.header("재무 현황 분석")
        fig_finance = px.bar(filtered_df, x='프로젝트명', y=['실행 비용 (KRW)', '계약 금액 (KRW)'], barmode='group')
        st.plotly_chart(fig_finance, use_container_width=True)

    # --- [탭 2: 간트 차트] ---
    with tabs[2]:
        st.header("프로젝트 타임라인 (Gantt)")
        fig_gantt = px.timeline(filtered_df, x_start='시작일', x_end='종료일', y='프로젝트명', color='상태')
        fig_gantt.update_yaxes(autorange="reversed")
        st.plotly_chart(fig_gantt, use_container_width=True)

    # --- [탭 3: 지도] ---
    with tabs[3]:
        st.header("지역별 프로젝트 분포")
        fig_map = px.scatter_geo(filtered_df, locations="위치", locationmode='country names', size='계약 금액 (KRW)', color='리스크 점수')
        st.plotly_chart(fig_map, use_container_width=True)

    # --- [탭 4: 인력 비교] ---
    with tabs[4]:
        st.header("인력 투입 현황 비교")
        fig_labor = go.Figure()
        fig_labor.add_trace(go.Bar(x=filtered_df['프로젝트명'], y=filtered_df['투입 인력 (명)'], name='현재 투입'))
        fig_labor.add_trace(go.Bar(x=filtered_df['프로젝트명'], y=filtered_df['목표 인력 (명)'], name='목표 인력'))
        st.plotly_chart(fig_labor, use_container_width=True)

    # --- [탭 5: 진행률] ---
    with tabs[5]:
        st.header("프로젝트 진행률 현황")
        fig_prog = px.bar(filtered_df, x='프로젝트명', y='진행률', color='진행률', color_continuous_scale='RdYlGn')
        st.plotly_chart(fig_prog, use_container_width=True)

    # --- [탭 6: 예측 분석] ---
    with tabs[6]:
        st.header("🔮 AI 리스크 예측 결과")
        st.write("진행 속도와 리스크 요인을 분석하여 예상 종료일을 산출했습니다.")
        pred_cols = st.columns(2)
        with pred_cols[0]:
            st.dataframe(filtered_df[['프로젝트명', '리스크 점수', '예상 종료일']].sort_values('리스크 점수', ascending=False))
        with pred_cols[1]:
            fig_risk = px.histogram(filtered_df, x='리스크 점수', nbins=10, title="리스크 점수 분포")
            st.plotly_chart(fig_risk, use_container_width=True)

    # --- [탭 7: 인력 최적화] ---
    with tabs[7]:
        st.header("🛠️ 인력 최적화 제안")
        st.write("인력 불균형을 감지하여 최적의 재배치 방안을 제시합니다.")
        st.dataframe(filtered_df[['프로젝트명', '투입 인력 (명)', '목표 인력 (명)', '인력 불균형', '인력 최적화 제안']])

    # --- [탭 8: 재무 지능] ---
    with tabs[8]:
        st.header("💎 재무 지능 (Financial Intelligence)")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.write("### 수익률 분석")
            fig_profit = px.bar(filtered_df, x='프로젝트명', y='수익률 (%)', color='수익률 (%)', color_continuous_scale='Viridis')
            st.plotly_chart(fig_profit, use_container_width=True)
        with col_f2:
            st.write("### 예상 최종 비용 vs 계약 금액")
            fig_cost = px.scatter(filtered_df, x='계약 금액 (KRW)', y='예상 최종 비용 (KRW)', size='수익금', hover_name='프로젝트명')
            st.plotly_chart(fig_cost, use_container_width=True)
        
        st.dataframe(filtered_df[['프로젝트명', '계약 금액 (KRW)', '실행 비용 (KRW)', '예상 최종 비용 (KRW)', '수익률 (%)']])

    # --- [탭 9: 마스터 데이터] ---
    with tabs[9]:
        st.header("📋 마스터 데이터 관리")
        st.write("데이터를 직접 수정하려면 아래 테이블을 편집하세요.")
        edited_df = st.data_editor(filtered_df, num_rows="dynamic")

    # --- [탭 10: 데이터 내보내기] ---
    with tabs[10]:
        st.header("📥 데이터 내보내기")
        csv = filtered_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="CSV 파일로 다운로드",
            data=csv,
            file_name='project_intelligence_data.csv',
            mime='text/csv',
        )

if __name__ == "__main__":
    main()
