import io
import os
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8881687625:AAHHN6kxCOFPhlIqTYpWBuK0HPAIyx-dlTY")
CHAT_ID = os.getenv("CHAT_ID", "6129768085")

TICKERS = ["QQQ", "QLD", "SPY", "BTC-USD", "SOXL"]


def get_data(ticker):
    try:
        df = yf.download(ticker, period="3y", progress=False, multi_level_index=False)
        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[['Close', 'High', 'Low', 'Volume']].copy()

        df["Close"] = pd.to_numeric(df["Close"], errors='coerce')
        df["High"] = pd.to_numeric(df["High"], errors='coerce')
        df["Low"] = pd.to_numeric(df["Low"], errors='coerce')
        df["Volume"] = pd.to_numeric(df["Volume"], errors='coerce')

        # 기본 이동평균선 및 지수이동평균선
        df["MA120"] = df["Close"].rolling(window=120).mean()
        df["MA240"] = df["Close"].rolling(window=240).mean()
        df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()

        # VWAP 계산
        cum_vol = df["Volume"].cumsum()
        df["VWAP"] = (df["Volume"] * df["Close"]).cumsum() / cum_vol.replace(0, 1)

        # RSI 계산
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        loss = loss.replace(0, 0.000001)
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # 일목균형표 구름대 (Senkou Span A, Senkou Span B) 계산
        # 여기서는 '계산일 기준' 원본값만 저장한다. 26일 뒤로 미는(shift) 작업은
        # 차트를 그릴 때(build_cloud_series)만 적용한다 - 원본값을 df에 그대로
        # 보관해야 최근 26개 값을 '미래 구름'으로 이어붙일 수 있기 때문.
        nine_high = df["High"].rolling(window=9).max()
        nine_low = df["Low"].rolling(window=9).min()
        df["Conversion_Line"] = (nine_high + nine_low) / 2

        twenty_six_high = df["High"].rolling(window=26).max()
        twenty_six_low = df["Low"].rolling(window=26).min()
        df["Base_Line"] = (twenty_six_high + twenty_six_low) / 2

        df["Senkou_Span_A"] = (df["Conversion_Line"] + df["Base_Line"]) / 2

        fifty_two_high = df["High"].rolling(window=52).max()
        fifty_two_low = df["Low"].rolling(window=52).min()
        df["Senkou_Span_B"] = (fifty_two_high + fifty_two_low) / 2

        return df.dropna()
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return None


def get_trend_msg(last):
    if last["Close"] > last["EMA9"] and last["EMA9"] > last["MA120"]:
        return "📈상승"
    if last["Close"] < last["MA120"]:
        return "📉하락"
    return "➡️횡보"


def get_action_msg(last):
    p, rsi, ma120 = last["Close"], last["RSI"], last["MA120"]
    if rsi >= 70:
        return "⚠️ 매도(과열): 분할 매도"
    if p < last["EMA9"] and p < last["VWAP"]:
        return "📉 매도(이탈): EMA 9 하향 이탈"
    if rsi < 70:
        if p < ma120 * 0.8: return "🔥 매수(4단계): 심한 웅덩이"
        if p < ma120 * 0.9: return "🔥 매수(3단계): 120일선 하향"
        if p <= ma120: return "💰 매수(2단계): 120일선 터치"
        if p <= ma120 * 1.1: return "💧 매수(1단계): 120일선 근접"
    return "🌿 관망"


def send_telegram(method, **kwargs):
    """텔레그램 API 호출 공용 함수. 실패 시 최대 3회 재시도."""
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
                timeout=30, **kwargs
            )
            if r.ok:
                return r
            print(f"[Telegram Error] {method} status={r.status_code} body={r.text}")
        except requests.RequestException as e:
            print(f"[Telegram Request Failed] {method} attempt={attempt + 1} err={e}")
    return None


def build_report_text(data_cache, vix_val, failed_tickers):
    """
    기존에는 마크다운 표(| --- | 형식)로 보냈지만 텔레그램은 마크다운 표 문법을
    지원하지 않아 파이프/구분선이 그대로 텍스트로 노출되고 줄바꿈이 깨지면서
    표가 중복/겹쳐 보이는 것처럼 표시되던 문제가 있었음.
    -> 표 대신 종목별 블록 형태로 정리해서 표 문법 없이 깔끔하게 표시.
    """
    lines = ["🚀 *Seulgi 투자 비서 최종 보고서*", f"📊 VIX 지수: {vix_val:.2f}", ""]

    for t in TICKERS:
        df = data_cache.get(t)
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        trend = get_trend_msg(last)
        action = get_action_msg(last)
        lines.append(f"*{t}*  {trend}  RSI {last['RSI']:.1f}")
        lines.append(f"    └ {action}")
        lines.append("")

    if failed_tickers:
        lines.append(f"⚠️ 데이터 조회 실패: {', '.join(failed_tickers)}")

    return "\n".join(lines).strip()


def build_cloud_series(df, window_df, periods=26):
    """
    일목균형표 구름대(선행스팬)는 계산일 기준 26기간 '뒤(미래)'에 표시하는 것이 정석.
    -> 과거 구간(window_df 범위)은 26기간 뒤로 shift해서 정렬하고,
       가장 최근 26개 계산값은 아직 실제 가격 데이터가 없는 '미래' 영업일에
       이어붙여서 구름이 캔들(가격)보다 오른쪽으로 튀어나오도록 만든다.

    반환되는 두 시리즈(cloud_a, cloud_b)는 과거 구간 + 미래 26영업일이
    합쳐진 인덱스를 가지므로, 그대로 ax.fill_between(index, a, b)에 사용하면 된다.
    """
    shifted_a = df["Senkou_Span_A"].shift(periods)
    shifted_b = df["Senkou_Span_B"].shift(periods)

    hist_a = shifted_a.loc[window_df.index]
    hist_b = shifted_b.loc[window_df.index]

    future_dates = pd.bdate_range(
        start=df.index[-1] + pd.Timedelta(days=1), periods=periods, freq="B"
    )
    future_a = pd.Series(df["Senkou_Span_A"].tail(periods).values, index=future_dates)
    future_b = pd.Series(df["Senkou_Span_B"].tail(periods).values, index=future_dates)

    cloud_a = pd.concat([hist_a, future_a])
    cloud_b = pd.concat([hist_b, future_b])
    return cloud_a, cloud_b


def build_ticker_chart(ticker, df):
    """
    기존에는 티커 1개당 차트 3장을 따로 만들어 각각 전송(5종목 x 3장 = 15장)했기 때문에
    사진이 연달아 너무 많이 오는 문제가 있었음.
    -> 3개 차트를 세로로 이어붙인 이미지 1장으로 합쳐서 종목당 딱 1장만 생성.
    """
    fig, axes = plt.subplots(3, 1, figsize=(10, 15))

    # 1) 최근 1개월 단기 줌인 + 구름대(26일 미래까지 확장)
    recent_1m = df.iloc[-30:]
    ax = axes[0]
    cloud_a, cloud_b = build_cloud_series(df, recent_1m)
    ax.plot(recent_1m.index, recent_1m['Close'], color='black', label='Price (1M)')
    ax.plot(recent_1m.index, recent_1m['MA120'], color='blue', linestyle='--', label='MA120')
    ax.plot(recent_1m.index, recent_1m['EMA9'], color='orange', label='EMA9')
    ax.fill_between(cloud_a.index, cloud_a, cloud_b,
                     color='lightgreen', alpha=0.3, label='Cloud (+26D)')
    ax.axvline(df.index[-1], color='gray', linestyle=':', linewidth=1)
    ax.set_title(f"{ticker} - Recent 1-Month (+26D Cloud)")
    ax.legend(fontsize=8)

    # 2) 180일(약 6개월) 확대 + 구름대(26일 미래까지 확장) (웅덩이/이격도 판정용)
    recent_180d = df.iloc[-180:]
    ax = axes[1]
    cloud_a, cloud_b = build_cloud_series(df, recent_180d)
    ax.plot(recent_180d.index, recent_180d['Close'], color='black', label='Price (180D)')
    ax.plot(recent_180d.index, recent_180d['MA120'], color='blue', label='MA120')
    ax.plot(recent_180d.index, recent_180d['MA240'], color='red', label='MA240')
    ax.fill_between(cloud_a.index, cloud_a, cloud_b,
                     color='lightgreen', alpha=0.3, label='Cloud (+26D)')
    ax.axvline(df.index[-1], color='gray', linestyle=':', linewidth=1)
    ax.set_title(f"{ticker} - 180-Day Zoom (+26D Cloud, Entry Point)")
    ax.legend(fontsize=8)

    # 3) 3년치 전체 통합 차트
    ax = axes[2]
    ax.plot(df.index, df['Close'], color='black', alpha=0.5, label='Price (3Y)')
    ax.plot(df.index, df['MA120'], color='blue', label='MA120')
    ax.plot(df.index, df['MA240'], color='red', label='MA240')
    ax.set_title(f"{ticker} - 3-Year Full Trend")
    ax.legend(fontsize=8)

    fig.suptitle(ticker, fontsize=14, fontweight='bold')
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=110)
    plt.close(fig)
    return buf.getvalue()  # bytes로 반환 (재시도 시 스트림 position 밀림 버그 방지)


def send_photos_as_album(photos):
    """
    photos: [(filename, image_bytes, caption), ...]
    텔레그램 sendMediaGroup으로 사진 여러 장을 앨범(메시지 1개)으로 묶어서 한 번에 전송.
    한 메시지에 최대 10장까지 가능하므로 10장 단위로 나눠서 호출.
    """
    if not photos:
        return

    for i in range(0, len(photos), 10):
        chunk = photos[i:i + 10]
        media = []
        files = {}
        for fname, img_bytes, caption in chunk:
            item = {"type": "photo", "media": f"attach://{fname}"}
            if caption:
                item["caption"] = caption
            media.append(item)
            files[fname] = (fname, img_bytes, "image/png")

        send_telegram(
            "sendMediaGroup",
            data={"chat_id": CHAT_ID, "media": json.dumps(media)},
            files=files,
        )


def send_report():
    data_cache = {t: get_data(t) for t in TICKERS}
    vix_df = get_data("^VIX")

    failed_tickers = [t for t, df in data_cache.items() if df is None or df.empty]
    vix_val = vix_df["Close"].iloc[-1] if vix_df is not None and not vix_df.empty else 0

    # 1) 텍스트 리포트(표 대신 블록 형태) 먼저 전송
    report_text = build_report_text(data_cache, vix_val, failed_tickers)
    send_telegram(
        "sendMessage",
        data={"chat_id": CHAT_ID, "text": report_text, "parse_mode": "Markdown"},
    )

    # 2) 티커별 3개 차트를 1장으로 합쳐서, 앨범(메시지 1개)으로 한 번에 전송
    photos = []
    for t in TICKERS:
        df = data_cache[t]
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        caption = f"{t}  {get_trend_msg(last)}  RSI {last['RSI']:.1f}\n{get_action_msg(last)}"
        img_bytes = build_ticker_chart(t, df)
        photos.append((f"{t}.png", img_bytes, caption))

    send_photos_as_album(photos)


if __name__ == "__main__":
    send_report()
