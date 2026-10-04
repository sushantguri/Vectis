"""Minimal reference backend service. vectis starts this as a
subprocess for the backend_a / backend_b / backends roles; BACKEND_ID
and PORT come from the environment so the same file serves both
Backend A and Backend B."""
import os
import time

from flask import Flask, jsonify, make_response

app = Flask(__name__)
BACKEND_ID = os.environ.get("BACKEND_ID", "A")
START = time.time()


@app.after_request
def add_backend_header(resp):
    resp.headers["X-Backend"] = BACKEND_ID
    return resp


@app.get("/")
def root():
    return jsonify(service="vectis-backend", backend=BACKEND_ID, up_since=START)


@app.get("/api/status")
def status():
    resp = make_response(jsonify(backend=BACKEND_ID, status="ok"))
    resp.headers["Cache-Control"] = "max-age=60"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3001"))
    # Bind 0.0.0.0, not 127.0.0.1 -- other machines on the LAN need to
    # be able to reach this directly.
    app.run(host="0.0.0.0", port=port)
