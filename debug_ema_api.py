"""
EMA Scanner API 400 에러 디버깅
"""

import requests
import json

BASE_URL = "http://localhost:8001"

print("=" * 60)
print("EMA Scanner API 디버깅")
print("=" * 60)

# 1. 상태 확인 (GET)
print("\n1. 상태 확인 (GET /api/ema-scanner/status)")
try:
    response = requests.get(f"{BASE_URL}/api/ema-scanner/status")
    print(f"   Status Code: {response.status_code}")
    print(f"   Response: {response.json()}")
except Exception as e:
    print(f"   Error: {e}")

# 2. 시작 (POST) - 옵션 1: JSON Body
print("\n2. 시작 - JSON Body")
try:
    response = requests.post(
        f"{BASE_URL}/api/ema-scanner/start",
        headers={"Content-Type": "application/json"},
        json={
            "timeframe": "15m",
            "top": 50,
            "llm_alert": False
        }
    )
    print(f"   Status Code: {response.status_code}")
    print(f"   Response: {response.text}")
    if response.status_code != 200:
        print(f"   Error Detail: {response.json()}")
except Exception as e:
    print(f"   Error: {e}")

# 3. 시작 (POST) - 옵션 2: Query Parameters
print("\n3. 시작 - Query Parameters")
try:
    response = requests.post(
        f"{BASE_URL}/api/ema-scanner/start",
        params={
            "timeframe": "15m",
            "top": 50,
            "llm_alert": False
        }
    )
    print(f"   Status Code: {response.status_code}")
    print(f"   Response: {response.text}")
except Exception as e:
    print(f"   Error: {e}")

# 4. 시작 (POST) - 옵션 3: Form Data
print("\n4. 시작 - Form Data")
try:
    response = requests.post(
        f"{BASE_URL}/api/ema-scanner/start",
        data={
            "timeframe": "15m",
            "top": 50,
            "llm_alert": False
        }
    )
    print(f"   Status Code: {response.status_code}")
    print(f"   Response: {response.text}")
except Exception as e:
    print(f"   Error: {e}")

# 5. 빈 Body
print("\n5. 시작 - 빈 Body (기본값 사용)")
try:
    response = requests.post(
        f"{BASE_URL}/api/ema-scanner/start",
        headers={"Content-Type": "application/json"},
        json={}
    )
    print(f"   Status Code: {response.status_code}")
    print(f"   Response: {response.text}")
except Exception as e:
    print(f"   Error: {e}")

print("\n" + "=" * 60)
print("디버깅 완료")
print("=" * 60)
