# Macro Lens

India and global macro dashboard, refreshed every weekday evening by GitHub Actions.

Framework: track nominal growth, read bond markets first, follow government incentives. The page states observations from the data, not buy or sell calls.

Live site: https://prathamgupta9004-lab.github.io/macro-lens/

## Sources
- FRED (St. Louis Fed): US growth, inflation, liquidity, rates, credit, oil, USD/INR
- IMF (IRFCL and International Liquidity): central bank reserves and gold holdings by country
- MOSPI (Government of India): CPI, IIP, WPI, quarterly GDP
- EIA: US crude stocks and Strategic Petroleum Reserve
- NSE: FII / DII cash flows, daily category-wise turnover (FPI, mutual funds, insurers, banks, retail), index history, Nifty 500 constituents, total market cap
- NSDL: monthly FPI net investment since 2002
- RBI data via the IMF monetary survey: bank credit, deposits, broad money; BIS via FRED: credit to GDP
- World Bank: market cap to GDP, real household consumption per head
- Kenneth R. French data library: US market cap by industry since 1926
- Yahoo Finance: index, currency, commodity and stock prices
- RBI: policy repo rate

## Run locally
```
pip install -r requirements.txt
python fetch.py
python build.py
```
Then open `dashboard/index.html`. Edit `data/events.json` to change the events and themes list.
