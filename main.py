from gemini_pro_bot.bot import start_bot
from server import start_health_server

if __name__ == "__main__":
    start_health_server()  # Запускаем HTTP сервер для Render
    start_bot()  # Запускаем бота
