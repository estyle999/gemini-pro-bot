import threading
from gemini_pro_bot.bot import start_bot
from server import start_health_server

if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    start_bot()
