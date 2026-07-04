#!/data/data/com.termux/files/usr/bin/python3
"""
Simple Python HTTP server that serves a custom HTML page.
Run: python web_server.py
Or make executable: chmod +x web_server.py && ./web_server.py
"""

import http.server
import socketserver
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Termux Web Server</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: #fff;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .container {
            background: rgba(255,255,255,0.08);
            backdrop-filter: blur(12px);
            border-radius: 24px;
            padding: 48px 64px;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
            border: 1px solid rgba(255,255,255,0.1);
        }
        h1 {
            font-size: 3rem;
            background: linear-gradient(90deg, #00d2ff, #3a7bd5);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 12px;
        }
        .subtitle {
            font-size: 1.2rem;
            color: rgba(255,255,255,0.7);
            margin-bottom: 32px;
        }
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 24px;
            margin: 16px 0;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .info {
            font-size: 0.95rem;
            line-height: 1.8;
            color: rgba(255,255,255,0.6);
        }
        .info span {
            color: #00d2ff;
            font-weight: 600;
        }
        .badge {
            display: inline-block;
            background: rgba(0,210,255,0.2);
            color: #00d2ff;
            padding: 4px 16px;
            border-radius: 20px;
            font-size: 0.85rem;
            margin-top: 24px;
        }
        a { color: #00d2ff; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Server Running</h1>
        <p class="subtitle">Python HTTP Server on Termux</p>
        <div class="card">
            <div class="info">
                <strong>Host:</strong> <span>0.0.0.0</span><br>
                <strong>Port:</strong> <span>""" + str(PORT) + """</span><br>
                <strong>Status:</strong> <span>✅ Active</span><br>
                <strong>Device:</strong> <span>Android via Termux</span>
            </div>
        </div>
        <div class="badge">🖥️ Serving from Android • {PORT}</div>
    </div>
</body>
</html>
""".replace("{PORT}", str(PORT))

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.send_header('Server', f'Termux-HTTP/{PORT}')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
        else:
            super().do_GET()

if __name__ == '__main__':
    print(f"\n  🌐 Starting web server on http://0.0.0.0:{PORT}")
    print(f"  📱  Access from browser on this device:  http://localhost:{PORT}")
    print(f"  🔌  Press Ctrl+C to stop\n")
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  ⛔ Server stopped.\n")
            httpd.server_close()
