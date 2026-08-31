from pathlib import Path


APP_JS = Path(__file__).parents[2] / "demo" / "frontend" / "app.js"


def test_deployed_frontend_keeps_cloud_api_same_origin():
    source = APP_JS.read_text(encoding="utf-8")

    assert 'if(location.protocol==="file:")apiBase="http://127.0.0.1:8765"' in source
    assert 'location.port!=="8765"' not in source
