from flask import Flask

app = Flask(__name__)

@app.get("/_debug/ping")
def ping():
    return "pong", 200

@app.get("/health")
def health():
    return {"status": "ok"}, 200

@app.get("/_debug/try-import")
def try_import():
    import traceback
    try:
        import app as real_app  # Try importing the main app module
        return "import ok", 200
    except Exception as e:
        tb = traceback.format_exc()
        # Return error details to quickly pinpoint failing import lines
        return f"import error: {type(e).__name__}: {e}\n{tb}", 500
