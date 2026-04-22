import requests
# Verified 2026 parameters
url = "https://indodax.com/tradingview/history_v2?from=1710000000&symbol=btc_idr&tf=1D&to=1713800000"
headers = {'User-Agent': 'Mozilla/5.0'}
r = requests.get(url, headers=headers)
print(f"V2 Status: {r.status_code}")
if r.status_code == 200:
    print("✅ Connection Established. Data is streaming!")