import discord
from discord.ext import commands
import re
import logging
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta, timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import asyncio
import traceback

# Настройка логирования
logger = logging.getLogger('discord')
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('bot.log', encoding='utf-8', mode='a')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.handlers = [file_handler, console_handler]

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
GOOGLE_SHEET_NAME = os.getenv('GOOGLE_SHEET_NAME')
GOOGLE_CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE')

# Настройка Google Sheets
SCOPE = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, SCOPE)
client = gspread.authorize(creds)
sheet = client.open(GOOGLE_SHEET_NAME).sheet1

# Discord бот
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Заголовки таблицы
HEADERS = [
    'Дата', 'Пользователь', 'Тикер', 'Ссылка на пост',
    'Риск', 'Entry', 'SL', 'TP', 'Результат', 'XP', 'Символ', 'Голоса Так'
]

def extract_xp(content: str):
    pattern = r'\b(win|lose|be)\s*([+-]?\d*[.,]?\d*)\s*\$'
    match = re.search(pattern, content, re.I)
    if match:
        op, num = match.group(1).lower(), match.group(2).replace(',', '.')
        val = float(num) if num and num != '0' else 0.0
        if op == 'lose' and val > 0:
            val = -val
        if op == 'be':
            val = 0.0
        return val, '$'
    if re.search(r'\b(win|lose|be)\s*[+-]?\d*[.,]?\d*\b', content, re.I):
        return 'NO_DOLLAR', None
    return None, None

def extract_ticker(content: str):
    first = content.strip().split('\n')[0].split()[0]
    stop = {
        'risk','риск','entry','твх','sl','stop','стоп',
        'tp','тп','stoploss','стоплосс'
    }
    return first.upper() if first.lower() not in stop else 'Не указан'

def extract_param(content, keys):
    pat = r'(' + '|'.join(keys) + r')[\s:=-]*([\d.,]+)'
    m = re.search(pat, content, re.I)
    return m.group(2) if m else 'Не указан'

extract_risk  = lambda c: extract_param(c, ['risk','риск'])
extract_entry = lambda c: extract_param(c, ['entry','твх'])
extract_sl    = lambda c: extract_param(c, ['sl','stop','стоп','stoploss','стоплосс'])
extract_tp    = lambda c: extract_param(c, ['tp','тп'])

def ensure_headers(ws):
    if ws.row_values(1) != HEADERS:
        ws.insert_row(HEADERS, 1)

def on_message_message_error_cleanup(msg):
    logger.error(f'Ошибка в on_message для {msg.id}')

@bot.event
async def on_ready():
    ensure_headers(sheet)
    logger.info(f'{bot.user} запущен.')

# ----- Функция для запуска голосования -----
async def start_vote_for_message(message: discord.Message):
    """
    Создает опрос "Ви заробили на цьому кейсі?" под данным сообщением.
    Варианты: Так, Ні, Скіпнув. Длительность — 3 дня.
    """
    try:
        poll = discord.Poll(
            question="Ви заробили на цьому кейсі?",
            duration=timedelta(days=3)
        )
        poll.add_answer(text="Так")
        poll.add_answer(text="Ні")
        poll.add_answer(text="Скіпнув")

        await message.reply(content="Ви заробили на цьому кейсі?", poll=poll)
        logger.info(f'Опрос создан для сообщения {message.id}')
    except discord.Forbidden:
        await message.channel.send("Помилка: у мене недостатньо прав для створення опросу.")
        logger.error(f'Недостатньо прав для створення опросу в каналі {message.channel.id}')
    except Exception as e:
        await message.channel.send("Виникла помилка при створенні опросу.")
        logger.error(f'Помилка при створенні опросу: {str(e)}')

# ----- Основной обработчик сообщений -----
@bot.event
async def on_message(msg: discord.Message):
    try:
        if msg.author.bot or msg.channel.id != CHANNEL_ID:
            return

        if msg.content.startswith('!'):
            await bot.process_commands(msg)
            return

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        link = f'https://discord.com/channels/{msg.guild.id}/{msg.channel.id}/{msg.id}'
        xp, sym = extract_xp(msg.content)
        low = msg.content.lower()
        is_idea   = bool(re.search(r'\b(entry|твх|risk|риск)\b', low))
        is_result = bool(re.search(r'\b(win|lose|be|close)\b', low))
        is_update = bool(re.search(r'\bupdate\b', low))

        # 1) Идея
        if is_idea and not is_result and not is_update:
            row = [
                now, msg.author.name, extract_ticker(msg.content), link,
                extract_risk(msg.content), extract_entry(msg.content),
                extract_sl(msg.content), extract_tp(msg.content),
                '', '', '$', '0'
            ]
            sheet.append_row(row)
            await msg.channel.send(f'✅ Идея сохранена ({msg.author.name})', delete_after=10)
            return

        # 2) Результат
        elif is_result:
            if not (msg.reference and msg.reference.message_id):
                await msg.channel.send(
                    f"{msg.author.mention} ❌ Чтобы отправить результат, ответьте на сообщение с идеей.",
                    delete_after=30
                )
                return
            if xp == 'NO_DOLLAR':
                await msg.channel.send(
                    f"{msg.author.mention} ❌ Неверный формат результата! Обязательно указывайте символ $ "
                    "(например: win 5$, lose -10$, be 0$).",
                    delete_after=30
                )
                return

            # обновляем строку в Google Sheets
            rep = str(msg.reference.message_id)
            rows = sheet.get_all_values()
            idx = next(
                (i+2 for i,r in enumerate(rows[1:])
                 if len(r)>3 and r[3].endswith(rep)),
                None
            )
            if idx:
                current = sheet.row_values(idx) + ['']*12
                current[8] = link
                if xp not in (None, 'NO_DOLLAR'):
                    current[9], current[10] = str(xp), sym
                sheet.update(range_name=f'A{idx}:L{idx}', values=[current[:12]])

            # если в тексте есть "win" — запускаем голосование
            if re.search(r'\bwin\b', msg.content, re.IGNORECASE):
                await start_vote_for_message(msg)

            return

        # 3) Апдейт
        elif is_update:
            if not (msg.reference and msg.reference.message_id):
                await msg.channel.send(
                    f"{msg.author.mention} ❌ Чтобы обновить идею, ответьте на исходное сообщение.",
                    delete_after=30
                )
                return
            await msg.channel.send(f'✅ Апдейт принят ({msg.author.name})', delete_after=5)
            return

        # 4) Неверный формат
        else:
            await msg.channel.send(
                f"{msg.author.mention} ❌ Придерживайтесь формата сообщений!",
                delete_after=10
            )
            return

    except Exception:
        on_message_message_error_cleanup(msg)

# ----- Команда для ручного старта голосования -----
@bot.command(name='start_vote')
@commands.has_permissions(administrator=True)
async def start_vote_cmd(ctx, link: str):
    """Админ может запустить голосование по ссылке на пост."""
    try:
        guild_id, channel_id, message_id = link.rstrip('/').split('/')[-3:]
        channel = bot.get_channel(int(channel_id))
        target = await channel.fetch_message(int(message_id))
    except Exception:
        return await ctx.send("❌ Неверная ссылка на сообщение.", delete_after=10)

    await start_vote_for_message(target)
    await ctx.send("✅ Голосування запущено.", delete_after=3)

# ----- Команда экспорта и сбора результатов опросов -----
@bot.command()
@commands.has_permissions(administrator=True)
async def export(ctx):
    status = await ctx.send('⏳ Экспорт...')
    try:
        ws = client.open(GOOGLE_SHEET_NAME).sheet1
        ensure_headers(ws)

        # 1) Сбор завершённых опросов
        polls_data = {}
        now = datetime.now(timezone.utc)
        def get_votes(ans):
            for attr in ('votes','count','votes_count','vote_count'):
                if hasattr(ans, attr):
                    return getattr(ans, attr)
            return len(getattr(ans, 'voters', []))
        async for m in ctx.channel.history(limit=None, oldest_first=True):
            if m.author.bot and m.poll and m.poll.expires_at <= now and m.reference:
                polls_data[str(m.reference.message_id)] = sum(
                    get_votes(a) for a in m.poll.answers if a.text == 'Так'
                )

        # 2) Сбор всех идей и результатов
        all_rows = ws.get_all_values()[1:]
        existing_links = {r[3] for r in all_rows if len(r) > 3 and r[3]}

        new_rows = []
        updates = []
        async for m in ctx.channel.history(limit=None, oldest_first=True):
            if m.author.bot or m.content.startswith('!'):
                continue
            link = f'https://discord.com/channels/{m.guild.id}/{m.channel.id}/{m.id}'
            low = m.content.lower()
            is_idea = bool(re.search(r'\b(entry|твх|risk|риск)\b', low))
            is_result = bool(re.search(r'\b(win|lose|be|close)\b', low))
            if is_idea and link not in existing_links:
                new_rows.append([
                    m.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                    m.author.name,
                    extract_ticker(m.content),
                    link,
                    extract_risk(m.content),
                    extract_entry(m.content),
                    extract_sl(m.content),
                    extract_tp(m.content),
                    '', '', '$', '0'
                ])
            elif is_result and m.reference:
                xp_val, sym_val = extract_xp(m.content)
                yes = str(polls_data.get(str(m.id), 0))
                updates.append((str(m.reference.message_id), link, xp_val, sym_val, yes))

        # 3) Добавляем новые идеи
        if new_rows:
            ws.append_rows(new_rows, value_input_option='USER_ENTERED')

        # 4) Обновляем результаты батчем
        all_rows = ws.get_all_values()[1:]
        link_to_idx = {
            r[3].split('/')[-1]: idx+2
            for idx, r in enumerate(all_rows)
            if len(r)>3 and r[3]
        }

        batch = []
        for ref_id, link, xp_val, sym_val, yes in updates:
            idx = link_to_idx.get(ref_id)
            if not idx:
                continue
            current = ws.row_values(idx) + ['']*12
            current[8]  = link
            current[11] = yes
            if xp_val not in (None, 'NO_DOLLAR'):
                current[9]  = str(xp_val)
                current[10] = sym_val or ''
            batch.append({'range': f'A{idx}:L{idx}', 'values': [current[:12]]})

        if batch:
            ws.batch_update(batch)

        await status.edit(content='✅ Экспорт завершен.')
        await status.delete(delay=3)
    except Exception as e:
        logger.error(f'Ошибка экспорта: {e}')
        await status.delete()

if __name__ == '__main__':
    bot.run(TOKEN)
