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

async def send_ephemeral_error(channel: discord.TextChannel, user: discord.User, message: str):
    try:
        thread = await channel.create_thread(
            name=f"Помилка {user.name}",
            type=discord.ChannelType.private_thread,
            invitable=False
        )
        await thread.send(f"{user.mention} {message}")
        await asyncio.sleep(10)
        await thread.delete()
    except Exception as e:
        logger.error(f"Ошибка создания/удаления ветки: {e}")


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
        logger.error(f'Помилка при створенні опросу: {e}')

# ----- Основной обработчик сообщений -----
@bot.event
async def on_message(msg: discord.Message):
    try:
        if msg.author.bot or msg.channel.id != CHANNEL_ID:
            return

        if msg.content.startswith('!'):
            await bot.process_commands(msg)
            return
        

        # Проверка упоминания роли в сообщении
        missing_role_id = 1363900324397449507 #arena role
        if missing_role_id not in msg.raw_role_mentions:
            await send_ephemeral_error(
                msg.channel,
                msg.author,
                f"❕В сообщении желательно упомянуть роль <@&{missing_role_id}>."
            )

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
            await msg.add_reaction('✅')
            return

        # 2) Результат
        if is_result:
            if not (msg.reference and msg.reference.message_id):
                await send_ephemeral_error(
                    msg.channel, msg.author,
                    "❌ Чтобы отправить результат, ответьте на сообщение с идеей."
                )
                return
            if xp == 'NO_DOLLAR':
                await send_ephemeral_error(
                    msg.channel, msg.author,
                    "❌ Неверный формат результата! Указывайте символ $ (например: win 5$)."
                )
                return

            rep = str(msg.reference.message_id)
            rows = sheet.get_all_values()
            idx = next(
                (i+2 for i, r in enumerate(rows[1:])
                 if len(r) > 3 and r[3].endswith(rep)),
                None
            )
            if idx:
                current = sheet.row_values(idx) + ['']*12
                current[8]  = link
                current[9], current[10] = str(xp), sym
                sheet.update(values=[current[:12]], range_name=f'A{idx}:L{idx}')

            if re.search(r'\bwin\b', msg.content, re.IGNORECASE):
                await start_vote_for_message(msg)
            return

        # 3) Апдейт сразу в on_message
        if is_update:
            if not (msg.reference and msg.reference.message_id):
                await send_ephemeral_error(
                    msg.channel, msg.author,
                    "❌ Чтобы обновить, ответьте на сообщение с идеей."
                )
                return

            rep = str(msg.reference.message_id)
            rows = sheet.get_all_values()
            idx = next(
                (i+2 for i, r in enumerate(rows[1:])
                 if len(r) > 3 and r[3].endswith(rep)),
                None
            )
            if idx:
                new_risk  = extract_risk(msg.content)
                new_entry = extract_entry(msg.content)
                new_sl    = extract_sl(msg.content)
                new_tp    = extract_tp(msg.content)
                # Обновляем только указанные поля
                if new_risk  != 'Не указан':
                    sheet.update_cell(idx, 5, new_risk)
                if new_entry != 'Не указан':
                    sheet.update_cell(idx, 6, new_entry)
                if new_sl    != 'Не указан':
                    sheet.update_cell(idx, 7, new_sl)
                if new_tp    != 'Не указан':
                    sheet.update_cell(idx, 8, new_tp)
            await msg.add_reaction('✅')
            return

        # 4) Неверный формат
        await send_ephemeral_error(
            msg.channel, msg.author,
            "❌ Придерживайтесь формата сообщений!"
        )
    except Exception:
        on_message_message_error_cleanup(msg)

# ----- Команда для ручного старта голосования -----
@bot.command(name='start_vote')
@commands.has_permissions(administrator=True)
async def start_vote_cmd(ctx, link: str):
    """Админ может запустить голосование по ссылке на пост."""
    try:
        _, channel_id, message_id = link.rstrip('/').split('/')[-3:]
        channel = bot.get_channel(int(channel_id))
        target = await channel.fetch_message(int(message_id))
    except Exception:
        return await ctx.send("❌ Неверная ссылка на сообщение.", delete_after=10)

    await start_vote_for_message(target)

# ----- Команда экспорта и сбора результатов опросов -----
@bot.command()
@commands.has_permissions(administrator=True)
async def export(ctx):
    try:
        ws = client.open(GOOGLE_SHEET_NAME).sheet1
        ensure_headers(ws)
        batch = []

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

        # 2) Сбор всех идей, результатов и апдейтов
        all_rows = ws.get_all_values()[1:]
        existing_links = {r[3] for r in all_rows if len(r)>3 and r[3]}
        new_rows, updates = [], []

        async for m in ctx.channel.history(limit=None, oldest_first=True):
            if m.author.bot or m.content.startswith('!'):
                continue

            link = f'https://discord.com/channels/{m.guild.id}/{m.channel.id}/{m.id}'
            low = m.content.lower()
            is_idea   = bool(re.search(r'\b(entry|твх|risk|риск)\b', low))
            is_result = bool(re.search(r'\b(win|lose|be|close)\b', low))
            is_update = bool(re.search(r'\bupdate\b', low))

            # новые идеи
            if is_idea and not is_result and not is_update and link not in existing_links:
                new_rows.append([
                    m.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                    m.author.name, extract_ticker(m.content), link,
                    extract_risk(m.content), extract_entry(m.content),
                    extract_sl(m.content), extract_tp(m.content),
                    '', '', '$', '0'
                ])

            # результаты
            elif is_result and m.reference:
                xp_val, sym_val = extract_xp(m.content)
                yes = str(polls_data.get(str(m.id), 0))
                updates.append((str(m.reference.message_id), link, xp_val, sym_val, yes))

            # апдейты
            elif is_update and m.reference:
                ref_id = str(m.reference.message_id)
                idx = next(
                    (i+2 for i, r in enumerate(all_rows)
                     if len(r)>3 and r[3].endswith(ref_id)),
                    None
                )
                if idx:
                    current = ws.row_values(idx) + ['']*12
                    current[4] = extract_risk(m.content)
                    current[5] = extract_entry(m.content)
                    current[6] = extract_sl(m.content)
                    current[7] = extract_tp(m.content)
                    batch.append({
                        'range': f'A{idx}:L{idx}',
                        'values': [current[:12]]
                    })

        # 3) Запись новых идей
        if new_rows:
            ws.append_rows(new_rows, value_input_option='USER_ENTERED')

        # 4) Запись результатов и апдейтов батчем
        all_rows = ws.get_all_values()[1:]
        link_to_idx = {
            r[3].split('/')[-1]: i+2
            for i, r in enumerate(all_rows) if len(r)>3 and r[3]
        }

        # результаты
        for ref_id, link, xp_val, sym_val, yes in updates:
            idx = link_to_idx.get(ref_id)
            if not idx:
                continue
            current = ws.row_values(idx) + ['']*12
            current[8]  = link
            current[11] = yes
            if xp_val not in (None, 'NO_DOLLAR'):
                current[9], current[10] = str(xp_val), sym_val or ''
            batch.append({'range': f'A{idx}:L{idx}', 'values': [current[:12]]})

        if batch:
            ws.batch_update(batch)

        logger.info('Экспорт завершен.')
        await ctx.message.add_reaction('✅')
    except Exception as e:
        logger.error(f'Ошибка экспорта: {e}')

if __name__ == '__main__':
    bot.run(TOKEN)
