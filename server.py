"""
Icarus Live Map - lokaler HTTP-Server
Starten: python server.py
Oeffnet automatisch http://localhost:8080
"""
import http.server
import threading
import webbrowser
import os

PORT = 8080
os.chdir(os.path.dirname(os.path.abspath(__file__)))

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Kein Logspam in der Konsole

server = http.server.HTTPServer(("", PORT), Handler)
print(f"[+] Icarus Live Map -> http://localhost:{PORT}")
print("    Ctrl+C zum Beenden\n")
threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\n[+] Server gestoppt.")
