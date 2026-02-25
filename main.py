import telebot
import sqlite3
import os
import threading
import time
import shutil
from datetime import datetime, timedelta
from flask import Flask
import sys

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
# Берем токен из переменных окружения (обязательно для Render)
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    print("❌ Ошибка: TELEGRAM_TOKEN не найден в переменных окружения!")
    print("Добавьте TELEGRAM_TOKEN в секцию Environment на Render")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN)

# Пароль для доступа (лучше тоже вынести в переменные окружения)
PASSWORD = os.environ.get('BOT_PASSWORD', '0918')  # По умолчанию 0918, но можно задать через переменную

authorized_users = set()

# Создаем необходимые папки
os.makedirs('medicine_photos', exist_ok=True)
os.makedirs('trash_photos', exist_ok=True)

# ==================== БАЗА ДАННЫХ ====================
conn = sqlite3.connect('medicines.db', check_same_thread=False)
c = conn.cursor()

# Создаем основную таблицу
c.execute('DROP TABLE IF EXISTS medicines')
c.execute('''CREATE TABLE medicines(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    name TEXT NOT NULL,
    description TEXT,
    manufactured_date TEXT,
    expiry_date TEXT,
    photo_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

# Создаем таблицу для корзины
c.execute('''CREATE TABLE IF NOT EXISTS trash(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER,
    user_id INTEGER NOT NULL,
    username TEXT,
    deleted_by_id INTEGER,
    deleted_by_username TEXT,
    name TEXT NOT NULL,
    description TEXT,
    manufactured_date TEXT,
    expiry_date TEXT,
    photo_path TEXT,
    deleted_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

print("✅ Таблицы созданы")


# ==================== КЛАВИАТУРЫ ====================
def kb():
    k = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row('➕ Добавить', '🔍 Поиск')
    k.row('📋 Список', '⚠️ Срок годности')
    k.row('🗑 Корзина', '👥 Мои лекарства')
    k.row('❌ Удалить')
    return k


def auth_kb():
    k = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row('🔑 Ввести пароль')
    return k


states = {}


# ==================== АВТОРИЗАЦИЯ ====================
def is_authorized(user_id):
    return user_id in authorized_users


def auth_required(func):
    def wrapper(message):
        if is_authorized(message.from_user.id):
            return func(message)
        else:
            bot.send_message(message.chat.id,
                             "🔒 Доступ запрещен!\nВведите пароль для входа:",
                             reply_markup=auth_kb())

    return wrapper


@bot.message_handler(commands=['start'])
def start(m):
    if is_authorized(m.from_user.id):
        bot.send_message(m.chat.id,
                         "👋 Бот для лекарств\n\n"
                         "➕ Добавить - новое лекарство\n"
                         "🔍 Поиск - найти по названию\n"
                         "📋 Список - все лекарства\n"
                         "⚠️ Срок годности - проверка\n"
                         "👥 Мои лекарства - только ваши\n"
                         "❌ Удалить - удалить ЛЮБОЕ лекарство\n"
                         "🗑 Корзина - просмотреть и восстановить\n\n"
                         "⚠️ ВНИМАНИЕ: удаленные лекарства можно восстановить из корзины!",
                         reply_markup=kb())
    else:
        bot.send_message(m.chat.id,
                         "🔒 Для доступа к боту введите пароль:",
                         reply_markup=auth_kb())


@bot.message_handler(func=lambda m: m.text == '🔑 Ввести пароль')
def handle_password_button(m):
    if is_authorized(m.from_user.id):
        bot.send_message(m.chat.id, "✅ Вы уже авторизованы!", reply_markup=kb())
        return
    msg = bot.send_message(m.chat.id, "Введите пароль:")
    bot.register_next_step_handler(msg, check_password)


@bot.message_handler(func=lambda m: not is_authorized(m.from_user.id) and m.text != '🔑 Ввести пароль')
def handle_unauthorized(m):
    if m.text.strip() == PASSWORD:
        authorized_users.add(m.from_user.id)
        bot.send_message(m.chat.id,
                         "✅ Пароль верный! Добро пожаловать в бот.",
                         reply_markup=kb())
        start(m)
    else:
        bot.send_message(m.chat.id,
                         "❌ Неверный пароль! Нажмите кнопку '🔑 Ввести пароль' для повторной попытки.",
                         reply_markup=auth_kb())


def check_password(m):
    if m.text.strip() == PASSWORD:
        authorized_users.add(m.from_user.id)
        bot.send_message(m.chat.id,
                         "✅ Пароль верный! Добро пожаловать в бот.",
                         reply_markup=kb())
        start(m)
    else:
        bot.send_message(m.chat.id,
                         "❌ Неверный пароль! Попробуйте еще раз.",
                         reply_markup=auth_kb())


# ==================== ДОБАВЛЕНИЕ ====================
@bot.message_handler(func=lambda m: m.text == '➕ Добавить')
@auth_required
def add(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name or f"id{uid}"

    if uid not in states:
        states[uid] = {'user_id': uid, 'username': username}

    if 'name' not in states[uid]:
        bot.send_message(m.chat.id, "Название:")
        bot.register_next_step_handler(m, lambda msg: state(msg, 'name'))
    elif 'description' not in states[uid]:
        bot.send_message(m.chat.id, "Описание (-):")
        bot.register_next_step_handler(m, lambda msg: state(msg, 'description'))
    elif 'manufactured_date' not in states[uid]:
        bot.send_message(m.chat.id, "📅 Дата производства (ДД.ММ.ГГГГ, или '-'):")
        bot.register_next_step_handler(m, lambda msg: state(msg, 'manufactured_date'))
    elif 'expiry_date' not in states[uid]:
        bot.send_message(m.chat.id, "📅 Срок годности до (ДД.ММ.ГГГГ, или '-'):")
        bot.register_next_step_handler(m, lambda msg: state(msg, 'expiry_date'))
    else:
        bot.send_message(m.chat.id, "📸 Фото или -:")
        bot.register_next_step_handler(m, add_photo)


def state(m, key):
    uid = m.from_user.id
    val = m.text.strip()

    if key in ['manufactured_date', 'expiry_date'] and val != '-':
        try:
            day, month, year = map(int, val.split('.'))
            datetime(year, month, day)
            states[uid][key] = val
        except:
            bot.send_message(m.chat.id, "❌ Неверный формат! Используйте ДД.ММ.ГГГГ")
            bot.register_next_step_handler(bot.send_message(m.chat.id, f"{key}:"), lambda msg: state(msg, key))
            return
    else:
        states[uid][key] = '' if key == 'description' and val == '-' else val

    add(m)


def add_photo(m):
    uid = m.from_user.id
    d = states.pop(uid, {})
    p = None
    if m.photo:
        try:
            f = bot.get_file(m.photo[-1].file_id)
            p = f"medicine_photos/{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            with open(p, 'wb') as img:
                img.write(bot.download_file(f.file_path))
        except:
            return bot.send_message(m.chat.id, "❌ Ошибка фото")

    c.execute("""INSERT INTO medicines(user_id, username, name, description, manufactured_date, expiry_date, photo_path) 
                 VALUES(?,?,?,?,?,?,?)""",
              (uid, d.get('username'), d.get('name', ''), d.get('description', ''),
               d.get('manufactured_date'), d.get('expiry_date'), p))
    conn.commit()

    msg = f"✅ Добавлено: {d.get('name')}"
    if d.get('manufactured_date'):
        msg += f"\n📅 Произведено: {d.get('manufactured_date')}"
    if d.get('expiry_date'):
        msg += f"\n📅 Годен до: {d.get('expiry_date')}"

    bot.send_message(m.chat.id, msg, reply_markup=kb())


# ==================== ПОИСК ====================
@bot.message_handler(func=lambda m: m.text == '🔍 Поиск')
@auth_required
def search_s(m):
    bot.register_next_step_handler(bot.send_message(m.chat.id, "Название для поиска:"), search)


def search(m):
    c.execute("SELECT * FROM medicines WHERE name LIKE ? ORDER BY created_at DESC",
              (f'%{m.text.strip()}%',))
    meds = c.fetchall()
    if meds:
        bot.send_message(m.chat.id, f"🔍 Найдено: {len(meds)}")
        for med in meds: card(m.chat.id, med)
    else:
        bot.send_message(m.chat.id, "❌ Ничего не найдено")


# ==================== СПИСОК ====================
@bot.message_handler(func=lambda m: m.text == '📋 Список')
@auth_required
def lst(m):
    c.execute("SELECT * FROM medicines ORDER BY created_at DESC")
    meds = c.fetchall()
    if not meds: return bot.send_message(m.chat.id, "📭 База пуста")
    bot.send_message(m.chat.id, f"📋 Всего: {len(meds)}")
    for med in meds: card(m.chat.id, med)


# ==================== МОИ ЛЕКАРСТВА ====================
@bot.message_handler(func=lambda m: m.text == '👥 Мои лекарства')
@auth_required
def my_meds(m):
    c.execute("SELECT * FROM medicines WHERE user_id=? ORDER BY created_at DESC", (m.from_user.id,))
    meds = c.fetchall()
    if not meds: return bot.send_message(m.chat.id, "📭 У вас нет лекарств")
    bot.send_message(m.chat.id, f"👥 Ваши лекарства: {len(meds)}")
    for med in meds: card(m.chat.id, med)


# ==================== ПРОВЕРКА СРОКА ====================
@bot.message_handler(func=lambda m: m.text == '⚠️ Срок годности')
@auth_required
def exp_chk(m):
    c.execute("""SELECT * FROM medicines 
                 WHERE expiry_date IS NOT NULL 
                 AND expiry_date!='' AND expiry_date!='-'""")
    meds = c.fetchall()

    if not meds:
        bot.send_message(m.chat.id, "📭 Нет лекарств со сроком годности")
        return

    today = datetime.now().date()
    expired = []
    soon = []

    for med in meds:
        try:
            day, month, year = map(int, med[6].split('.'))
            exp_date = datetime(year, month, day).date()
            days = (exp_date - today).days

            if days < 0:
                expired.append((med, abs(days)))
            elif days <= 30:
                soon.append((med, days))
        except:
            continue

    if expired:
        bot.send_message(m.chat.id, "🔴 ПРОСРОЧЕННЫЕ:")
        for med, days in expired:
            msg = f"❌ {med[3]} (ID: {med[0]}, @{med[2]})\n📅 Просрочено на {days} дн."
            bot.send_message(m.chat.id, msg)

    if soon:
        bot.send_message(m.chat.id, "🟡 СКОРО ИСТЕКАЮТ:")
        for med, days in soon:
            msg = f"⚠️ {med[3]} (ID: {med[0]}, @{med[2]})\n📅 Осталось {days} дн."
            bot.send_message(m.chat.id, msg)

    if not expired and not soon:
        bot.send_message(m.chat.id, "✅ У всех нормальный срок")


# ==================== КОРЗИНА С ВОССТАНОВЛЕНИЕМ ====================
@bot.message_handler(func=lambda m: m.text == '🗑 Корзина')
@auth_required
def show_trash(m):
    c.execute("SELECT * FROM trash ORDER BY deleted_at DESC")
    trash_items = c.fetchall()

    if not trash_items:
        bot.send_message(m.chat.id, "🗑 Корзина пуста")
        return

    bot.send_message(m.chat.id, f"🗑 В корзине: {len(trash_items)} лекарств(а)")

    for item in trash_items:
        try:
            id, orig_id, uid, username, del_id, del_username, n, d, manuf, exp, p, del_time = item

            restore_kb = telebot.types.InlineKeyboardMarkup()
            restore_kb.add(telebot.types.InlineKeyboardButton(
                text=f"↩️ Восстановить #{id}",
                callback_data=f"restore_{id}"
            ))

            cap = f"🗑 УДАЛЕНО (ID в корзине: {id})\n"
            cap += f"🆔 Бывший ID: {orig_id}\n"
            cap += f"💊 {n}\n"
            if d: cap += f"📝 {d}\n"
            if manuf and manuf != '-': cap += f"🏭 Произведено: {manuf}\n"
            if exp and exp != '-': cap += f"📅 Годен до: {exp}\n"
            cap += f"👤 Добавил: @{username}\n"
            cap += f"🗑 Удалил: @{del_username}\n"
            cap += f"⏰ Удалено: {del_time[:16]}"

            if p and os.path.exists(p):
                with open(p, 'rb') as f:
                    bot.send_photo(m.chat.id, f, caption=cap, reply_markup=restore_kb)
            else:
                bot.send_message(m.chat.id, cap, reply_markup=restore_kb)
        except Exception as e:
            print(f"Ошибка показа из корзины: {e}")
            continue


@bot.callback_query_handler(func=lambda call: call.data.startswith('restore_'))
def handle_restore(call):
    try:
        trash_id = int(call.data.split('_')[1])

        c.execute("""SELECT original_id, user_id, username, name, description, 
                            manufactured_date, expiry_date, photo_path 
                     FROM trash WHERE id=?""", (trash_id,))
        item = c.fetchone()

        if not item:
            bot.answer_callback_query(call.id, "❌ Лекарство не найдено в корзине!")
            return

        orig_id, user_id, username, name, desc, manuf, exp, photo_path = item
        restorer_name = call.from_user.username or call.from_user.first_name or f"id{call.from_user.id}"

        new_photo_path = None
        if photo_path and os.path.exists(photo_path):
            try:
                new_photo_path = photo_path.replace('trash_photos', 'medicine_photos')
                shutil.copy2(photo_path, new_photo_path)
            except Exception as e:
                print(f"Ошибка восстановления фото: {e}")

        c.execute("""INSERT INTO medicines(
            id, user_id, username, name, description, manufactured_date, expiry_date, photo_path) 
            VALUES(?,?,?,?,?,?,?,?)""",
                  (orig_id, user_id, username, name, desc, manuf, exp, new_photo_path))

        c.execute("DELETE FROM trash WHERE id=?", (trash_id,))

        if photo_path and os.path.exists(photo_path):
            try:
                os.remove(photo_path)
            except:
                pass

        conn.commit()

        bot.answer_callback_query(call.id, f"✅ Лекарство #{orig_id} восстановлено!")

        bot.send_message(call.message.chat.id,
                         f"✅ Лекарство #{orig_id} '{name}' восстановлено из корзины!\n"
                         f"👤 Добавил: @{username}\n"
                         f"↩️ Восстановил: @{restorer_name}")

        c.execute("SELECT DISTINCT user_id FROM medicines UNION SELECT DISTINCT user_id FROM trash")
        users = c.fetchall()

        for user in users:
            if user[0] != call.from_user.id and user[0] in authorized_users:
                try:
                    bot.send_message(user[0],
                                     f"↩️ Лекарство #{orig_id} '{name}' (добавленное @{username})\n"
                                     f"восстановлено пользователем @{restorer_name}")
                except:
                    pass

    except Exception as e:
        print(f"Ошибка восстановления: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка восстановления")


# ==================== УДАЛЕНИЕ ====================
@bot.message_handler(func=lambda m: m.text == '❌ Удалить')
@auth_required
def del_s(m):
    c.execute("SELECT id, name, username FROM medicines ORDER BY id")
    meds = c.fetchall()

    if not meds:
        return bot.send_message(m.chat.id, "📭 База пуста")

    list_msg = "📋 ВСЕ лекарства в базе:\n"
    for med in meds:
        list_msg += f"ID: {med[0]} - {med[1]} (добавил @{med[2]})\n"

    bot.send_message(m.chat.id, list_msg)
    bot.send_message(m.chat.id, "⚠️ ВНИМАНИЕ: Лекарство переместится в КОРЗИНУ!\nЕго можно будет восстановить оттуда.")

    msg = bot.send_message(m.chat.id, "Введите ID лекарства для удаления:")
    bot.register_next_step_handler(msg, delete)


def delete(m):
    try:
        i = int(m.text.strip())
    except:
        return bot.send_message(m.chat.id, "❌ Введите число!")

    c.execute("""SELECT user_id, username, name, description, 
                        manufactured_date, expiry_date, photo_path 
                 FROM medicines WHERE id=?""", (i,))
    r = c.fetchone()

    if r:
        user_id, username, name, desc, manuf, exp, photo_path = r
        deleter_name = m.from_user.username or m.from_user.first_name or f"id{m.from_user.id}"

        trash_photo_path = None
        if photo_path and os.path.exists(photo_path):
            try:
                trash_photo_path = photo_path.replace('medicine_photos', 'trash_photos')
                shutil.copy2(photo_path, trash_photo_path)
            except Exception as e:
                print(f"Ошибка копирования фото в корзину: {e}")

        c.execute("""INSERT INTO trash(
            original_id, user_id, username, deleted_by_id, deleted_by_username,
            name, description, manufactured_date, expiry_date, photo_path) 
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (i, user_id, username, m.from_user.id, deleter_name,
                   name, desc, manuf, exp, trash_photo_path or photo_path))

        if photo_path and os.path.exists(photo_path):
            try:
                os.remove(photo_path)
            except:
                pass

        c.execute("DELETE FROM medicines WHERE id=?", (i,))
        conn.commit()

        bot.send_message(m.chat.id,
                         f"✅ Лекарство #{i} '{name}' перемещено в КОРЗИНУ!\n"
                         f"👤 Добавил: @{username}\n"
                         f"🗑 Удалил: @{deleter_name}\n"
                         f"🗑 Чтобы восстановить, зайдите в корзину и нажмите кнопку под лекарством.")

        c.execute("SELECT DISTINCT user_id FROM medicines UNION SELECT DISTINCT user_id FROM trash")
        users = c.fetchall()

        for user in users:
            if user[0] != m.from_user.id and user[0] in authorized_users:
                try:
                    bot.send_message(user[0],
                                     f"🗑 Лекарство #{i} '{name}' (добавленное @{username})\n"
                                     f"перемещено в корзину пользователем @{deleter_name}")
                except:
                    pass
    else:
        bot.send_message(m.chat.id, "❌ Лекарство с таким ID не найдено")


# ==================== КАРТОЧКА ====================
def card(cid, med):
    try:
        id, uid, username, n, d, manuf, exp, p, cr = med
        cap = f"🆔 {id} | 👤 @{username}\n💊 {n}\n"
        if d: cap += f"📝 {d}\n"
        if manuf and manuf != '-': cap += f"🏭 Произведено: {manuf}\n"
        if exp and exp != '-':
            try:
                day, month, year = map(int, exp.split('.'))
                exp_date = datetime(year, month, day).date()
                days = (exp_date - datetime.now().date()).days
                if days < 0:
                    cap += f"❌ ПРОСРОЧЕНО! Годен до: {exp} (-{abs(days)} дн.)\n"
                elif days <= 30:
                    cap += f"⚠️ Годен до: {exp} (ост.{days} дн.)\n"
                else:
                    cap += f"✅ Годен до: {exp}\n"
            except:
                cap += f"📅 Годен до: {exp}\n"
        else:
            cap += "📅 Срок: -\n"
        cap += f"⏰ Добавлено: {cr[:16]}"

        if p and os.path.exists(p):
            with open(p, 'rb') as f:
                return bot.send_photo(cid, f, caption=cap)
        bot.send_message(cid, cap)
    except:
        bot.send_message(cid, "❌ Ошибка")


# ==================== УВЕДОМЛЕНИЯ ====================
def send_daily_notifications():
    while True:
        try:
            now = datetime.now()
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now > target:
                target += timedelta(days=1)

            time.sleep((target - now).total_seconds())

            c.execute("SELECT DISTINCT user_id FROM medicines")
            users = c.fetchall()

            for user in users:
                if user[0] in authorized_users:
                    try:
                        c.execute("SELECT * FROM medicines WHERE user_id=?", (user[0],))
                        meds = c.fetchall()
                        expired = []
                        for med in meds:
                            if med[6] and med[6] != '-':
                                try:
                                    d = map(int, med[6].split('.'))
                                    if (datetime(*d).date() - datetime.now().date()).days < 0:
                                        expired.append(med)
                                except:
                                    pass
                        if expired:
                            bot.send_message(user[0], f"🔔 У вас {len(expired)} просроченных лекарств!")
                    except:
                        pass
                    time.sleep(0.5)
        except Exception as e:
            print(f"Ошибка в уведомлениях: {e}")
            time.sleep(60)


def start_notification_thread():
    thread = threading.Thread(target=send_daily_notifications, daemon=True)
    thread.start()
    print("📅 Уведомления запущены")


# ==================== DEFAULT ====================
@bot.message_handler(func=lambda m: True)
def default(m):
    if is_authorized(m.from_user.id):
        bot.send_message(m.chat.id, "Используйте кнопки меню", reply_markup=kb())
    else:
        bot.send_message(m.chat.id, "🔒 Введите пароль для доступа", reply_markup=auth_kb())


# ==================== FLASK-СЕРВЕР ДЛЯ RENDER ====================
app = Flask(__name__)


@app.route('/')
def home():
    return "🤖 Telegram бот для лекарств работает!"


@app.route('/health')
def health():
    return "OK", 200


def run_bot():
    """Запуск бота в отдельном потоке"""
    print("✅ Бот запущен (требуется пароль)")
    print(f"🔑 Пароль: {PASSWORD}")
    print("🗑 Корзина с кнопками восстановления")
    start_notification_thread()
    bot.infinity_polling()


# ==================== ТОЧКА ВХОДА ====================
if __name__ == '__main__':
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Запускаем Flask-сервер для Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)