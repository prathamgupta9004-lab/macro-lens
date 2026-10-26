# Macro Lens

India and global macro dashboard, refreshed every weekday evening by GitHub Actions.

Built on the framework Ritesh Jain set out on the SOIC podcast (21 Oct 2026): track nominal growth, read bond markets first, follow government incentives. The page states observations from the data, not buy or sell calls.

Live site: https://prathamgupta9004-lab.github.io/macro-lens/

## Sources
- FRED (St. Louis Fed): US growth, inflation, liquidity, rates, credit, oil, USD/INR
- IMF (IRFCL and International Liquidity): central bank reserves and gold holdings by country
- MOSPI (Government of India): CPI, IIP, WPI, quarterly GDP
- EIA: US crude stocks and Strategic Petroleum Reserve
- NSE: FII / DII cash flows, Nifty 500 constituents
- Yahoo Finance: index, currency, commodity and stock prices
- RBI: policy repo rate

## Run locally
```
pip install -r requirements.txt
python fetch.py
python build.py
```
Then open `dashboard/index.html`. Edit `data/events.json` to change the events and themes list.
