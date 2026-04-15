cd C:\Users\MadsS\OneDrive\Skrivebord\Codes\Golf\MuchBetter

start cmd /k uv run python main.py
timeout /t 2

start cmd /k cloudflared tunnel --url http://localhost:5000