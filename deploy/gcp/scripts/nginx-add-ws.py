from pathlib import Path

path = Path("/etc/nginx/sites-enabled/algo-trading")
text = path.read_text()
if "location /ws" in text:
    print("ws already configured")
else:
    ws = """
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
"""
    text = text.replace("location / {", ws + "\n    location / {", 1)
    path.write_text(text)
    print("ws block added")
