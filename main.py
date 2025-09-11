import os
import time

def get_price():
    # Тут має бути логіка запиту ціни з API WhiteBIT
    return 100.0  # умовна ціна

def trade_decision(price, sma):
    if price > sma:
        return "SELL"
    elif price < sma:
        return "BUY"
    return "HOLD"

def main():
    prices = []
    while True:
        price = get_price()
        prices.append(price)
        if len(prices) > 5:
            prices.pop(0)
        sma = sum(prices) / len(prices)
        decision = trade_decision(price, sma)
        print(f"Price: {price}, SMA: {sma:.2f}, Action: {decision}")
        time.sleep(60)

if __name__ == "__main__":
    main()
