from flask import Flask, jsonify, render_template
import requests
import http.client
import threading
import os
from threading import Lock
import http.client
import os
import json


app = Flask(__name__)

# =========================
# GLOBAL STATE
# =========================
SYSTEM_CACHE = []
LOADING = True
LOAD_ERROR = None
CACHE_LOCK = Lock()

URL = "https://gql.aiq.netapp.com/"

# ==================================================
# Refresh Token
# ==================================================
def refresh_token():

    # ==========================================
    # Priority 1:
    # Railway Environment Variable
    # ==========================================
    refresh_token_value = os.environ.get(
        "REFRESH_TOKEN"
    )

    # ==========================================
    # Priority 2:
    # Local refresh_token.txt
    # ==========================================
    if not refresh_token_value:
        raise Exception("REFRESH_TOKEN not set in Railway Variables")

    # ==========================================
    # Request Access Token
    # ==========================================
    conn = http.client.HTTPSConnection(
        "api.activeiq.netapp.com"
    )

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

    data = res.read()

    response_json = json.loads(
        data.decode("utf-8")
    )

    access_token = response_json.get(
        "access_token"
    )

    if not access_token:

        raise Exception(
            f"Cannot get access token: {response_json}"
        )

    return access_token


def get_headers():
    return {
        "Authorization": f"Bearer {refresh_token()}",
        "Content-Type": "application/json"
    }


# =========================
# GRAPHQL QUERY
# =========================
QUERY = """
query($clusterName: String, $after: String) {
  systems(clusterName: $clusterName, after: $after, pageSize: 500) {
    cursor
    totalCount
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


# =========================
# LOAD DATA
# =========================
def load_all_systems():

    global SYSTEM_CACHE, LOADING, LOAD_ERROR

    after = None
    seen_serials = set()
    seen_cursors = set()
    all_data = []

    try:
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
                LOAD_ERROR = f"HTTP {r.status_code}"
                break

            data = r.json()

            block = data.get("data", {}).get("systems", {})
            systems = block.get("systems", [])
            cursor = block.get("cursor")

            if cursor in seen_cursors:
                break

            if cursor:
                seen_cursors.add(cursor)

            new_added = 0

            for s in systems:
                sn = s.get("serialNumber")
                if not sn or sn in seen_serials:
                    continue

                seen_serials.add(sn)
                all_data.append(s)
                new_added += 1

            if new_added == 0 or not cursor:
                break

            after = cursor

    except Exception as e:
        LOAD_ERROR = str(e)

    with CACHE_LOCK:
        SYSTEM_CACHE = all_data
        LOADING = False

    print(f"✅ Loaded: {len(SYSTEM_CACHE)} systems")


# =========================
# START LOADER
# =========================
def start_loader():
    t = threading.Thread(target=load_all_systems)
    t.daemon = True
    t.start()


# =========================
# API
# =========================
@app.route("/api/systems")
def api_systems():

    try:
        with CACHE_LOCK:
            return jsonify({
                "loading": LOADING,
                "count": len(SYSTEM_CACHE),
                "error": LOAD_ERROR,
                "data": SYSTEM_CACHE
            })

    except Exception as e:
        import traceback
        print("🔥 API ERROR:", traceback.format_exc())

        return jsonify({
            "error": str(e),
            "trace": traceback.format_exc(),
            "loading": False,
            "data": []
        }), 500


# =========================
# FRONTEND
# =========================
@app.route("/")
def index():
    return render_template("index.html")


# =========================
# MAIN
# =========================
if __name__ == "__main__":

    start_loader()

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
