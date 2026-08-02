import io
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf

# 텔레그램 토큰 및 채널 ID (GitHub Secrets를 통해 안전하게 불러올 수도 있습니다)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8881687625:AAHHN6kxCOFPhlIqTYpWBuK0HPAIyx-dlTY")
CHAT_ID = os.getenv("CHAT_ID", "6129768085")

# 분석할 종목 리스트 (필요에 따라 SOXL 등 추가 가능)
TICKERS = ["QQQ", "QLD", "SPY", "BTC-USD", "SOXL"]

def get_data(ticker):
    try:
        # 단일 종목 다운로드 시 멀티인덱스 충돌 방지
        df = yf.download(ticker, period="3y", progress=False, multi_level_index=False)
        if df is None or df.empty:
            return None
            
        if isinstance(df.columns, pd.MultiIndex): 
            df.columns = df.columns.get_level_values(0)
            
        df = df[['Close', 'Volume']].copy()
        
        df["Close"] = pd.to_numeric(df["Close"], errors='coerce')
        df["Volume"] = pd.to_numeric(df["Volume"], errors='coerce')
        
        df["MA120"] = df["Close"].rolling(window=120).mean()
        df["MA240"] = df["Close"].rolling(window=240).mean()
        df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
        
        cum_vol = df["Volume"].cumsum()
        df["VWAP"] = (df["Volume"] * df["Close"]).cumsum() / cum_vol.replace(0, 1)
        
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        
        loss = loss.replace(0, 0.000001)
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))
        
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

def send_report():
    vix_df = get_data("^VIX")
    vix_val = vix_df["Close"].iloc[-1] if vix_df is not None and not vix_df.empty else 0
    
    report = f"🚀 **[Seulgi 투자 비서 최종 보고서]**\n📊 VIX 지수: {vix_val:.2f}\n\n종목 | 추세 | RSI | 상세 판단\n---|---|---|---\n"
    
    for t in TICKERS:
        df = get_data(t)
        if df is None or df.empty: 
            continue
        last = df.iloc[-1]
        report += f"{t} | {get_trend_msg(last)} | {last['RSI']:.1f} | {get_action_msg(last)}\n"
    
    # 텔레그램 텍스트 리포트 전송
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                  data={"chat_id": CHAT_ID, "text": report, "parse_mode": "Markdown"})

    # 종목별 차트 생성 및 전송
    for t in TICKERS:
        df = get_data(t)
        if df is None or df.empty: 
            continue
        
        # 1. 3년치 통합 차트
        fig = plt.figure(figsize=(10, 6))
        plt.plot(df.index, df['Close'], color='black', alpha=0.5, label='Price')
        plt.plot(df.index, df['MA120'], color='blue', label='MA120')
        plt.plot(df.index, df['MA240'], color='red', label='MA240')
        plt.title(f"{t} 3-Year Trend")
        plt.legend()
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close(fig)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", 
                      data={"chat_id": CHAT_ID}, files={"photo": ("c1.png", buf)})
        buf.close()

        # 2. 180일 확대 차트 (120일선 이격 확인용)
        recent_df = df.iloc[-180:]
        fig2 = plt.figure(figsize=(10, 6))
        plt.plot(recent_df.index, recent_df['Close'], color='black', label='Price')
        plt.plot(recent_df.index, recent_df['MA120'], color='blue', label='MA120')
        plt.plot(recent_df.index, recent_df['MA240'], color='red', label='MA240')
        plt.title(f"{t} 180-Day Zoom (Entry Point)")
        plt.legend()
        plt.tight_layout()
        
        buf2 = io.BytesIO()
        plt.savefig(buf2, format='png')
        buf2.seek(0)
        plt.close(fig2)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", 
                      data={"chat_id": CHAT_ID}, files={"photo": ("c2.png", buf2)})
        buf2.close()

if __name__ == "__main__":
    send_report()
