import streamlit as st
import pandas as pd
import yfinance as yf
import pdfplumber
import re
import io
import plotly.express as px
import datetime


st.set_page_config(page_title="Copper Arbitrage Dashboard", layout="wide")

st.title("📈 LME vs CME Copper Arbitrage Calculator")
st.markdown("매일 갱신되는 LME Valuation PDF를 업로드하면 자동으로 CME 가격과 비교합니다.")

# --- 1. LME 데이터 추출 함수 (PDF) ---
def extract_lme_from_pdf(uploaded_file):
    with pdfplumber.open(uploaded_file) as pdf:
        # 구리(Copper) 데이터는 보통 첫 페이지에 존재함
        text = pdf.pages[0].extract_text()
        
    # 정규표현식을 이용해 '월물(Jul26)'과 '가격(13801.45)' 패턴 추출
    # 패턴: 영문3글자+숫자2자리 (공백/줄바꿈) 숫자연속+소수점2자리
    pattern = r"([A-Z][a-z]{2}\d{2})\s*([\d,]{4,6}\.\d{2})"
    matches = re.findall(pattern, text)
    
    data = []
    seen = set()
    for month, price in matches:
        if month not in seen: # 중복 추출 방지
            data.append({
                "Month": month,
                "LME_Price_MT": float(price.replace(',', ''))
            })
            seen.add(month)
            
    return pd.DataFrame(data)

# --- 2. CME 티커 자동 생성 함수 ---
def get_cme_ticker(month_str):
    # CME 구리(COMEX) 월물 기호 매핑
    month_codes = {
        "Jan": "F", "Feb": "G", "Mar": "H", "Apr": "J", "May": "K", "Jun": "M",
        "Jul": "N", "Aug": "Q", "Sep": "U", "Oct": "V", "Nov": "X", "Dec": "Z"
    }
    m_name = month_str[:3]
    year_str = month_str[-2:]
    code = month_codes.get(m_name, "")
    
    # 예: Jul26 -> HGN26.CMX (야후 파이낸스 COMEX 구리 티커 형식)
    return f"HG{code}{year_str}.CMX"

# --- 3. CME 데이터 수집 함수 (특정 날짜 지정 기능 추가) ---
def load_cme_data(months, target_date):
    cme_data = []
    
    # 야후 파이낸스는 start/end로 날짜를 지정할 때 end 날짜는 포함하지 않으므로 하루를 더해줍니다.
    end_date = target_date + datetime.timedelta(days=1)
    
    start_str = target_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    progress_text = f"{start_str} 기준 CME 데이터를 가져오는 중입니다..."
    my_bar = st.progress(0, text=progress_text)
    
    for i, month in enumerate(months):
        ticker = get_cme_ticker(month)
        try:
            # period="1d" 대신 정확한 start, end 날짜를 지정하여 호출합니다.
            hist = yf.Ticker(ticker).history(start=start_str, end=end_str)
            if not hist.empty:
                # 해당 날짜의 종가 추출
                close_price = hist['Close'].iloc[0]
                cme_data.append({"Month": month, "CME_Price_lb": close_price})
            else:
                cme_data.append({"Month": month, "CME_Price_lb": None})
        except Exception:
            cme_data.append({"Month": month, "CME_Price_lb": None})
            
        my_bar.progress((i + 1) / len(months), text=f"{ticker} 데이터 수집 완료 ({i+1}/{len(months)})")
        
    my_bar.empty()
    return pd.DataFrame(cme_data)

# ==========================================
# 🚀 메인 대시보드 UI
# ==========================================

col1, col2 = st.columns(2)
with col1:
    uploaded_file = st.file_uploader("📥 LME Valuation PDF 파일 업로드", type="pdf")
with col2:
    # 사용자가 PDF에 해당하는 날짜를 직접 달력에서 선택할 수 있도록 합니다.
    target_date = st.date_input("📅 비교할 CME종가 기준 날짜를 선택하세요", datetime.date(2026, 6, 17))

if uploaded_file is not None:
    with st.spinner("PDF에서 LME 데이터를 추출하는 중입니다..."):
        df_lme = extract_lme_from_pdf(uploaded_file)
        
    if df_lme.empty:
        st.error("PDF에서 데이터를 찾을 수 없습니다.")
    else:
        st.success(f"PDF 추출 성공! 총 {len(df_lme)}개의 월물 데이터를 찾았습니다.")
        
        # 2. CME 데이터 가져올 때 사용자가 선택한 target_date를 같이 넘겨줍니다.
        df_cme = load_cme_data(df_lme["Month"].tolist(), target_date)
        
        # --- (이하 3번 데이터 병합 및 시각화 코드는 기존과 동일하게 유지) ---
        
# --- 3. 데이터 병합 및 단위 환산 ---
        df_arb = pd.merge(df_lme, df_cme, on="Month")
        
        # 월물 문자열(Jul26)을 실제 날짜(datetime) 객체로 변환하여 시간순 정렬
        df_arb["Date"] = pd.to_datetime(df_arb["Month"], format="%b%y", errors='coerce')
        df_arb = df_arb.sort_values("Date")
        
        # [핵심 수정] 차트 X축 레이블용으로 보기 좋은 문자열 컬럼 생성 (예: "2026-07")
        df_arb["Chart_Month"] = df_arb["Date"].dt.strftime("%Y-%m")
        
        # CME 단위를 $/lb 에서 $/MT 로 환산
        conversion_factor = 2204.62
        df_arb["CME_Price_MT"] = df_arb["CME_Price_lb"] * conversion_factor
        
        # Spread (CME-LME) 계산
        df_arb["Spread(CME-LME)"] = df_arb["CME_Price_MT"] - df_arb["LME_Price_MT"]
        
        df_arb_clean = df_arb.dropna().copy()

        st.divider()
        
# --- 4. 결과 시각화 ---
        st.subheader("📋 Price & Spread Table, 전일종가기준")

        
        st.dataframe(df_arb_clean.drop(columns=["Date", "Chart_Month"]).style.format({
            "LME_Price_MT": "${:,.2f}",
            "CME_Price_MT": "${:,.2f}",
            "Spread(CME-LME)": "${:,.2f}"
        }), use_container_width=True)
        
        st.divider()

        st.subheader("📊 LME vs CME Forward Curve ($/MT)")
        
        # [핵심 수정] Plotly를 사용하여 Y축 자동 최적화 그래프 그리기
        fig = px.line(
            df_arb_clean, 
            x="Chart_Month", 
            y=["LME_Price_MT", "CME_Price_MT"],
            color_discrete_sequence=["#FF0000", "#0000FF"], # LME 빨강, CME 파랑
            labels={"value": "Price ($/MT)", "Chart_Month": "Month", "variable": "Market"}
        )
        
        # 마우스를 올렸을 때 두 가격이 같은 선상에서 동시에 보이도록 설정
        fig.update_layout(hovermode="x unified")
        
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
# --- 5. LME vs CME 교차 월물 아비트라지 매트릭스 ---
        st.subheader("🧮 Cross-Month Arbitrage Matrix (CME - LME, $/MT)")
        st.markdown(
            "세로축(LME)과 가로축(CME)의 모든 월물 조합에 대한 가격 차이입니다. "
            "**노란색 하이라이트**는 CME 가격이 LME보다 높아 스프레드 수익(Positive)이 발생하는 구간입니다."
        )
        
        # 매트릭스 축에서 가격을 바로 확인할 수 있도록 직관적인 레이블 생성
        lme_labels = df_arb_clean.apply(
            lambda x: f"{x['Chart_Month']}\n(LME: ${x['LME_Price_MT']:,.0f})", axis=1
        ).tolist()
        
        cme_labels = df_arb_clean.apply(
            lambda x: f"{x['Chart_Month']}\n(CME: ${x['CME_Price_MT']:,.0f})", axis=1
        ).tolist()
        
        # N x N 매트릭스 데이터 계산 (CME 가로축 가격 - LME 세로축 가격)
        matrix_data = []
        for i, lme_row in df_arb_clean.iterrows():
            row_data = []
            for j, cme_row in df_arb_clean.iterrows():
                spread = cme_row["CME_Price_MT"] - lme_row["LME_Price_MT"]
                row_data.append(spread)
            matrix_data.append(row_data)
            
        # 데이터프레임으로 변환 (인덱스는 LME, 컬럼은 CME)
        df_matrix = pd.DataFrame(matrix_data, index=lme_labels, columns=cme_labels)
        
        # 조건부 서식 적용 함수: 스프레드가 0보다 크면 노란색 배경
        def highlight_positive(val):
            # Streamlit 다크/라이트 모드에서 모두 글자가 잘 보이는 안정적인 노란색(rgba)
            return 'background-color: rgba(255, 235, 59, 0.4)' if val > 0 else ''
            
        # 판다스 버전에 호환되도록 스타일링 객체 생성 (최신: map, 구버전: applymap)
        try:
            styled_matrix = df_matrix.style.format("${:,.2f}").map(highlight_positive)
        except AttributeError:
            styled_matrix = df_matrix.style.format("${:,.2f}").applymap(highlight_positive)
            
        # 화면에 출력
        st.dataframe(styled_matrix, use_container_width=True)