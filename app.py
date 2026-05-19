from flask import Flask, jsonify, render_template
import requests
import threading
import os
import time
import http.client
import json

app = Flask(__name__)

# =========================
# CACHE STATE
# =========================
SYSTEM_CACHE = []
CACHE_LOCK = threading.Lock()

TOKEN_CACHE = None
TOKEN_TIME = 0
TOKEN_TTL = 50 * 60  # 50 min

URL = "https://gql.aiq.netapp.com/"


# =========================
# TOKEN (cached)
# =========================
def refresh_token():

    refresh_token_value = os.environ.get("REFRESH_TOKEN")

    if not refresh_token_value:
        raise Exception("REFRESH_TOKEN not set")

    conn = http.client.HTTPSConnection("api.activeiq.netapp.com")

    payload = json.dumps({
        "refresh_token": refresh_token_value
    })

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    conn.request(
        "POST",
        "/v1/tokens/accessToken",
        payload,
        headers
    )

    res = conn.getresponse()
    data = json.loads(res.read().decode())

    token = data.get("access_token")

    if not token:
        raise Exception(f"Token error: {data}")

    return token


def get_token():

    global TOKEN_CACHE, TOKEN_TIME

    now = time.time()

    if TOKEN_CACHE is None or now - TOKEN_TIME > TOKEN_TTL:
        print("🔄 Refreshing token...")
        TOKEN_CACHE = refresh_token()
        TOKEN_TIME = now

    return TOKEN_CACHE


# =========================
# GRAPHQL
# =========================
QUERY = """
query($clusterName: String, $after: String) {
  systems(clusterName: $clusterName, after: $after, pageSize: 500) {
    cursor
    systems {
      hostName
      serialNumber
      platformType
      hardwareModel { name }
      contract { expiryDate }
    }
  }
}
"""


def get_headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json"
    }


# =========================
# DATA LOADER (background job)
# =========================
def load_all_systems():

    global SYSTEM_CACHE

    while True:

        try:
            print("🚀 Refreshing system cache...")

            after = None
            all_data = []
            seen = set()

            for _ in range(100):

                r = requests.post(
                    URL,
                    headers=get_headers(),
                    json={
                        "query": QUERY,
                        "variables": {
                            "clusterName": "",
                            "after": after
                        }
                    },
                    timeout=60
                )

                if r.status_code != 200:
                    break

                data = r.json()

                block = data.get("data", {}).get("systems", {})
                systems = block.get("systems", [])
                cursor = block.get("cursor")

                new_added = 0

                for s in systems:
                    sn = s.get("serialNumber")
                    if not sn or sn in seen:
                        continue

                    seen.add(sn)
                    all_data.append(s)
                    new_added += 1

                if not cursor or new_added == 0:
                    break

                after = cursor

            with CACHE_LOCK:
                SYSTEM_CACHE = all_data

            print(f"✅ Cache updated: {len(all_data)} systems")

        except Exception as e:
            print("❌ Loader error:", e)

        # 每 10 分鐘更新
        time.sleep(600)


# =========================
# START BACKGROUND SERVICE
# =========================
def start_background():
    t = threading.Thread(target=load_all_systems, daemon=True)
    t.start()


# =========================
# API (FAST - no loading state)
# =========================
@app.route("/api/systems")
def api_systems():

    with CACHE_LOCK:
        return jsonify({
            "count": len(SYSTEM_CACHE),
            "data": SYSTEM_CACHE,
            "status": "ready"
        })


# =========================
# FRONTEND
# =========================
@app.route("/")
def index():
    return render_template("index.html")


# =========================
# STARTUP (IMPORTANT FOR RAILWAY)
# =========================
@app.before_request
def init():
    if not hasattr(app, "started"):
        app.started = True
        print("🚀 Starting background loader")
        start_background()


# =========================
# MAIN
# =========================
if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    start_background()

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
