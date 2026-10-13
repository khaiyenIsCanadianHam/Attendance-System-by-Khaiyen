"""Read-only HTTP readiness checks for an already running local server."""

import argparse
from urllib.error import HTTPError
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:5000")
args = parser.parse_args()
with urlopen(args.base + "/", timeout=10) as response:
    assert response.status == 200
    assert b'name="csrf_token"' in response.read()
for path in (
    "/static/vendor/jsQR.js",
    "/static/js/qr-camera.js",
    "/static/js/scanner.js",
):
    with urlopen(args.base + path, timeout=10) as response:
        assert response.status == 200
        assert len(response.read()) > 100
for path in ("/get_attendance", "/export_csv"):
    try:
        urlopen(args.base + path, timeout=10)
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Attendance endpoint allowed unauthenticated access")
print(
    "HTTP smoke passed: login form, local scripts, and protected attendance endpoints."
)
