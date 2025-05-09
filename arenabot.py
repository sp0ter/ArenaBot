import discord
from discord.ext import commands
import re
import logging
from dotenv import load_dotenv
import os
from datetime import timedelta

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

# Завантаження змінних середовища з .env файлу
load_dotenv()

# Конфігурація
TOKEN = os.getenv('DISCORD_BOT_TOKEN')  # Токен бота з .env
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))  # ID каналу з .env

# Налаштування інтентів
intents = discord.Intents.default()
intents.message_content = True  # Для читання вмісту повідомлень
intents.messages = True  # Для обробки подій повідомлень

# Ініціалізація бота
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    logging.info(f'Бот {bot.user} підключений і готовий до роботи!')

@bot.event
async def on_message(message):
    # Ігноруємо повідомлення від ботів і повідомлення поза вказаним каналом
    if message.author.bot or message.channel.id != CHANNEL_ID:
        return

    # Пошук слова "win" у повідомленні (ігноруємо регістр)
    if re.search(r'\bwin\b', message.content, re.IGNORECASE):
        logging.info(f'Виявлено слово "win" у повідомленні {message.id} від {message.author}')
        try:
            # Створення опросу
            poll = discord.Poll(
                question="Ви заробили на цьому кейсі?",
                duration=timedelta(days=3)  # 3 доби
            )
            # Додавання відповідей до опросу
            poll.add_answer(text="Так")
            poll.add_answer(text="Ні")
            poll.add_answer(text="Скіпнув")

            # Варіант 1: Опрос як відповідь на повідомлення
            await message.reply(content="Ви заробили на цьому кейсі?", poll=poll)
            logging.info(f'Опрос створено як відповідь на повідомлення {message.id}')

        except discord.Forbidden:
            await message.channel.send("Помилка: у мене недостатньо прав для створення опросу.")
            logging.error(f'Недостатньо прав для створення опросу в каналі {message.channel.id}')
        except Exception as e:
            await message.channel.send("Виникла помилка при створенні опросу.")
            logging.error(f'Помилка при створенні опросу: {str(e)}')

    # Обробка команд (якщо будуть додані)
    await bot.process_commands(message)

# Запуск бота
if __name__ == "__main__":
    if not TOKEN or not CHANNEL_ID:
        logging.error("Токен бота або ID каналу не вказані в .env файлі")
    else:
        bot.run(TOKEN)
