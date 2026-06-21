import datetime
import io
import re
import pandas as pd
import pdfplumber
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# 페이지 설정
st.set_page_config(page_title="Copper Arbitrage Dashboard", layout="wide")

st.title("📈 LME vs CME Copper Arbitrage Calculator")
st.markdown("LME Valuation PDF를 드래그 & 드롭하면 종가기준 CME 가격과 비교합니다.")


# --- 1. LME 데이터 추출 함수 (PDF) ---
@st.cache_data
def extract_lme_from_pdf(uploaded_file_bytes):
    # 캐싱을 위해 파일 객체 자체가 아닌 바이트 데이터를 받습니다.
    with pdfplumber.open(io.BytesIO(uploaded_file_bytes)) as pdf:
        text = pdf.pages[0].extract_text()

    # 패턴: 영문3글자+숫자2자리 (공백/줄바꿈) 숫자연속+소수점2자리
    pattern = r"([A-Z][a-z]{2}\d{2})\s*([\d,]{4,6}\.\d{2})"
    matches = re.findall(pattern, text)

    data = []
    seen = set()
    for month, price in matches:
        if month not in seen:  # 중복 추출 방지
            data.append(
                {
                    "Month": month,
                    "LME_Price_MT": float(price.replace(",", "")),
                }
            )
            seen.add(month)

    return pd.DataFrame(data)


# --- 2. CME 티커 자동 생성 함수 ---
def get_cme_ticker(month_str):
    month_codes = {
        "Jan": "F",
        "Feb": "G",
        "Mar": "H",
        "Apr": "J",
        "May": "K",
        "Jun": "M",
        "Jul": "N",
        "Aug": "Q",
        "Sep": "U",
        "Oct": "V",
        "Nov": "X",
        "Dec": "Z",
    }
    m_name = month_str[:3]
    year_str = month_str[-2:]
    code = month_codes.get(m_name, "")
    return f"HG{code}{year_str}.CMX"


# --- 3. CME 데이터 수집 함수 (캐싱 추가) ---
@st.cache_data
def load_cme_data(months, target_date):
    cme_data = []
    end_date = target_date + datetime.timedelta(days=1)

    start_str = target_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    progress_text = f"{start_str} 기준 CME 데이터를 가져오는 중입니다..."
    my_bar = st.progress(0, text=progress_text)

    for i, month in enumerate(months):
        ticker = get_cme_ticker(month)
        try:
            hist = yf.Ticker(ticker).history(start=start_str, end=end_str)
            if not hist.empty:
                close_price = hist["Close"].iloc[0]
                cme_data.append({"Month": month, "CME_Price_lb": close_price})
            else:
                cme_data.append({"Month": month, "CME_Price_lb": None})
        except Exception:
            cme_data.append({"Month": month, "CME_Price_lb": None})

        my_bar.progress(
            (i + 1) / len(months),
            text=f"{ticker} 데이터 수집 완료 ({i+1}/{len(months)})",
        )

    my_bar.empty()
    return pd.DataFrame(cme_data)


# ==========================================
# 🚀 메인 대시보드 UI
# ==========================================

col1, col2 = st.columns(2)
with col1:
    uploaded_file = st.file_uploader(
        "📥 LME Valuation PDF 파일 업로드", type="pdf"
    )
with col2:
    target_date = st.date_input(
        "📅 비교할 CME종가 기준 날짜를 선택하세요",
        datetime.date(2026, 6, 17),
        help="**기준 날짜 선택 가이드**\n\n업로드하시는 **LME Valuation PDF 파일의 기준일**과 동일한 날짜를 선택해 주세요.",
    )

if uploaded_file is not None:
    # 캐싱 효율을 위해 파일 바이트를 읽어서 넘김
    file_bytes = uploaded_file.read()

    with st.spinner("PDF에서 LME 데이터를 추출하는 중입니다..."):
        df_lme = extract_lme_from_pdf(file_bytes)

    if df_lme.empty:
        st.error("PDF에서 데이터를 찾을 수 없습니다.")
    else:
        st.success(
            f"PDF 추출 성공! 총 {len(df_lme)}개의 월물 데이터를 찾았습니다."
        )

        # CME 데이터 수집
        df_cme = load_cme_data(df_lme["Month"].tolist(), target_date)

        # --- 데이터 병합 및 단위 환산 ---
        df_arb = pd.merge(df_lme, df_cme, on="Month")

        # 시계열 정렬 및 가공
        df_arb["Date"] = pd.to_datetime(
            df_arb["Month"], format="%b%y", errors="coerce"
        )
        df_arb = df_arb.sort_values("Date")
        df_arb["Chart_Month"] = df_arb["Date"].dt.strftime("%Y-%m")

        # 단위 환산 (lb -> MT)
        conversion_factor = 2204.62
        df_arb["CME_Price_MT"] = df_arb["CME_Price_lb"] * conversion_factor
        df_arb["Spread(CME-LME)"] = (
            df_arb["CME_Price_MT"] - df_arb["LME_Price_MT"]
        )

        df_arb_clean = df_arb.dropna().copy()

        if df_arb_clean.empty:
            st.warning("CME 가격 매칭에 실패하여 표시할 데이터가 없습니다.")
        else:
            st.divider()

            # ==========================================
            # 🚀 마켓 컨텍스트 & KPI 패널 (변수명 오류 수정)
            # ==========================================
            st.subheader("💡 Market Context & Key Indicators")

            def draw_sparkline(series, color):
                fig = go.Figure(
                    go.Scatter(y=series, mode="lines", line=dict(color=color, width=3))
                )

            # 정의되지 않았던 kpi1, kpi2, kpi3 변수 매핑 해결
            kpi1, kpi2, kpi3 = st.columns(3)

            with kpi1:
                max_spread_row = df_arb_clean.loc[
                    df_arb_clean["Spread(CME-LME)"].idxmax()
                ]
                st.metric(
                    label=f"🔥 최대 스프레드 월물 ({max_spread_row['Chart_Month']})",
                    value=f"${max_spread_row['Spread(CME-LME)']:,.1f}",
                    help="현재 LME 대비 CME 가격이 가장 높게 형성된 월물과 그 차익 금액입니다.",
                )
             
            with kpi2:
                avg_spread = df_arb_clean["Spread(CME-LME)"].mean()
                st.metric(
                    label="📊 평균 교차 월물 스프레드",
                    value=f"${avg_spread:,.1f}",
                    delta="Positive Arb" if avg_spread > 0 else "Negative Arb",
                    delta_color="normal",
                )
             
            with kpi3:
                try:
                    start_dx = (
                        target_date - datetime.timedelta(days=7)
                    ).strftime("%Y-%m-%d")
                    end_dx = (
                        target_date + datetime.timedelta(days=1)
                    ).strftime("%Y-%m-%d")
                    dx_data = yf.Ticker("DX-Y.NYB").history(
                        start=start_dx, end=end_dx
                    )

                    if not dx_data.empty:
                        current_dx = dx_data["Close"].iloc[-1]
                        prev_dx = (
                            dx_data["Close"].iloc[-2]
                            if len(dx_data) > 1
                            else current_dx
                        )
                        dx_change = current_dx - prev_dx

                        st.metric(
                            label="💵 US Dollar Index (DXY)",
                            value=f"{current_dx:.2f}",
                            delta=f"{dx_change:.2f} (vs 전일)",
                            delta_color="inverse",
                            help="달러 강세는 통상적으로 구리 가격에 하방 압력으로 작용합니다.",
                        )
             
                    else:
                        st.metric(
                            label="💵 US Dollar Index (DXY)", value="Data N/A"
                        )
                except Exception:
                    st.metric(
                        label="💵 US Dollar Index (DXY)", value="Load Error"
                    )

            st.divider()

            # --- 4. 결과 시각화 (포맷팅 구조 개선) ---
            st.subheader("📋 Price & Spread Table (전일 종가 기준)")

            # 변형 데이터 유지를 위해 뷰용 df 따로 정의
            df_display = df_arb_clean[
                ["Month", "LME_Price_MT", "CME_Price_lb", "CME_Price_MT", "Spread(CME-LME)"]
            ]

            # st.dataframe의 내장 기능을 활용하여 안전하게 표 포맷팅 처리
            st.dataframe(
                df_display,
                use_container_width=True,
                column_config={
                    "Month": st.column_config.TextColumn("Month"),
                    "LME_Price_MT": st.column_config.NumberColumn(
                        "LME Price ($/MT)", format="$%,.2f"
                    ),
                    "CME_Price_lb": st.column_config.NumberColumn(
                        "CME Price ($/lb)", format="$%,.4f"
                    ),
                    "CME_Price_MT": st.column_config.NumberColumn(
                        "CME Price ($/MT)", format="$%,.2f"
                    ),
                    "Spread(CME-LME)": st.column_config.NumberColumn(
                        "Spread(CME-LME) ℹ️",
                        format="$%,.2f",
                        help="**Spread(CME-LME)란?**\n\n이 값이 양수이면 아비트라지 거래가 유효함을 의미합니다.",
                    ),
                },
            )

            st.divider()

            # Forward Curve 그래프 생성
            st.subheader("📊 LME vs CME Forward Curve ($/MT)")

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=df_arb_clean["Chart_Month"],
                    y=df_arb_clean["CME_Price_MT"],
                    mode="lines+markers+text",
                    name="CME Price",
                    line=dict(color="#0000FF", width=2),
                    text=df_arb_clean["CME_Price_MT"].apply(
                        lambda x: f"${x:,.0f}"
                    ),
                    textposition="top center",
                    textfont=dict(color="#0000FF"),
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=df_arb_clean["Chart_Month"],
                    y=df_arb_clean["LME_Price_MT"],
                    mode="lines+markers+text",
                    name="LME Price",
                    line=dict(color="#FF0000", width=2),
                    text=df_arb_clean["LME_Price_MT"].apply(
                        lambda x: f"${x:,.0f}"
                    ),
                    textposition="bottom center",
                    textfont=dict(color="#FF0000"),
                )
            )

            # 중간 지점에 스프레드 갭 표시
            mid_y = (
                df_arb_clean["CME_Price_MT"] + df_arb_clean["LME_Price_MT"]
            ) / 2
            fig.add_trace(
                go.Scatter(
                    x=df_arb_clean["Chart_Month"],
                    y=mid_y,
                    mode="text",
                    name="Spread Gap",
                    text=df_arb_clean["Spread(CME-LME)"].apply(
                        lambda x: f"Gap: ${x:,.0f}"
                    ),
                    textposition="middle center",
                    textfont=dict(color="#2ca02c", size=11, weight="bold"),
                    showlegend=False,
                )
            )

            y_min = df_arb_clean[["LME_Price_MT", "CME_Price_MT"]].min().min()
            y_max = df_arb_clean[["LME_Price_MT", "CME_Price_MT"]].max().max()
            margin = (y_max - y_min) * 0.15 if y_max != y_min else 100

            fig.update_layout(
                hovermode="x unified",
                yaxis=dict(
                    title="Price ($/MT)",
                    range=[y_min - margin, y_max + margin],
                    tickformat="$,.0f",
                ),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.05,
                    xanchor="right",
                    x=1,
                ),
                margin=dict(t=60),
            )

            st.plotly_chart(fig, use_container_width=True)

            st.divider()

            # --- 5. 교차 월물 아비트라지 매트릭스 ---
            st.subheader("🧮 Cross-Month Arbitrage Matrix (CME - LME, $/MT)")
            st.markdown(
                "세로축(LME)과 가로축(CME)의 조합별 가격 차이입니다. "
                "**노란색 구간**은 CME 가격이 LME보다 높은 차익 거래 기회 구간입니다."
            )

            lme_labels = df_arb_clean.apply(
                lambda x: f"{x['Chart_Month']} (LME: ${x['LME_Price_MT']:,.0f})",
                axis=1,
            ).tolist()
            cme_labels = df_arb_clean.apply(
                lambda x: f"{x['Chart_Month']} (CME: ${x['CME_Price_MT']:,.0f})",
                axis=1,
            ).tolist()

            matrix_data = []
            for i, lme_row in df_arb_clean.iterrows():
                row_data = []
                for j, cme_row in df_arb_clean.iterrows():
                    spread = cme_row["CME_Price_MT"] - lme_row["LME_Price_MT"]
                    row_data.append(spread)
                matrix_data.append(row_data)

            df_matrix = pd.DataFrame(
                matrix_data, index=lme_labels, columns=cme_labels
            )

            def highlight_positive(val):
                return (
                    "background-color: rgba(255, 235, 59, 0.35); color: #111111;"
                    if val > 0
                    else ""
                )

            try:
                styled_matrix = df_matrix.style.format("${:,.2f}").map(
                    highlight_positive
                )
            except AttributeError:
                styled_matrix = df_matrix.style.format("${:,.2f}").applymap(
                    highlight_positive
                )

            st.dataframe(styled_matrix, use_container_width=True)
