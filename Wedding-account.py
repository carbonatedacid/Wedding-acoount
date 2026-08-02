import io
import os
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
        # 전환선 (9일 고가 + 9일 저가) / 2
        nine_high = df["High"].rolling(window=9).max()
        nine_low = df["Low"].rolling(window=9).min()
        df["Conversion_Line"] = (nine_high + nine_low) / 2
        
        # 기준선 (26일 고가 + 26일 저가) / 2
        twenty_six_high = df["High"].rolling(window=26).max()
        twenty_six_low = df["Low"].rolling(window=26).min()
        df["Base_Line"] = (twenty_six_high + twenty_six_low) / 2
        
        # 선행스팬 A (전환선 + 기준선) / 2 (현재 시점에 맞추기 위해 shift 없이 정렬)
        df["Senkou_Span_A"] = (df["Conversion_Line"] + df["Base_Line"]) / 2
        
        # 선행스팬 B (52일 고가 + 52일 저가) / 2
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

    for t in TICKERS:
        df = get_data(t)
        if df is None or df.empty: 
            continue
        
        # [차트 1] 최근 1개월(30일) 단기 줌인 차트 + 구름대 포함
        recent_1m = df.iloc[-30:]
        fig1 = plt.figure(figsize=(10, 6))
        plt.plot(recent_1m.index, recent_1m['Close'], color='black', label='Price (1M)')
        plt.plot(recent_1m.index, recent_1m['MA120'], color='blue', linestyle='--', label='MA120')
        plt.plot(recent_1m.index, recent_1m['EMA9'], color='orange', label='EMA9')
        plt.fill_between(recent_1m.index, recent_1m['Senkou_Span_A'], recent_1m['Senkou_Span_B'], 
                         color='lightgreen', alpha=0.3, label='Cloud (Senkou A/B)')
        plt.title(f"{t} - Recent 1-Month Trend with Cloud")
        plt.legend()
        plt.tight_layout()
        
        buf1 = io.BytesIO()
        plt.savefig(buf1, format='png')
        buf1.seek(0)
        plt.close(fig1)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", 
                      data={"chat_id": CHAT_ID}, files={"photo": ("c1.png", buf1)})
        buf1.close()

        # [차트 2] 기존 3년치 전체 통합 차트
        fig2 = plt.figure(figsize=(10, 6))
        plt.plot(df.index, df['Close'], color='black', alpha=0.5, label='Price (3Y)')
        plt.plot(df.index, df['MA120'], color='blue', label='MA120')
        plt.plot(df.index, df['MA240'], color='red', label='MA240')
        plt.title(f"{t} - 3-Year Full Trend")
        plt.legend()
        plt.tight_layout()
        
        buf2 = io.BytesIO()
        plt.savefig(buf2, format='png')
        buf2.seek(0)
        plt.close(fig2)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", 
                      data={"chat_id": CHAT_ID}, files={"photo": ("c2.png", buf2)})
        buf2.close()

        # [차트 3] 180일(약 6개월) 확대 차트 (웅덩이 이격 판정용) + 구름대 포함
        recent_180d = df.iloc[-180:]
        fig3 = plt.figure(figsize=(10, 6))
        plt.plot(recent_180d.index, recent_180d['Close'], color='black', label='Price (180D)')
        plt.plot(recent_180d.index, recent_180d['MA120'], color='blue', label='MA120')
        plt.plot(recent_180d.index, recent_180d['MA240'], color='red', label='MA240')
        plt.fill_between(recent_180d.index, recent_180d['Senkou_Span_A'], recent_180d['Senkou_Span_B'], 
                         color='lightgreen', alpha=0.3, label='Cloud (Senkou A/B)')
        plt.title(f"{t} - 180-Day Zoom with Cloud (Entry Point)")
        plt.legend()
        plt.tight_layout()
        
        buf3 = io.BytesIO()
        plt.savefig(buf3, format='png')
        buf3.seek(0)
        plt.close(fig3)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", 
                      data={"chat_id": CHAT_ID}, files={"photo": ("c3.png", buf3)})
        buf3.close()

if __name__ == "__main__":
    send_report()
