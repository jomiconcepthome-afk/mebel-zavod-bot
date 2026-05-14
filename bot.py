import logging
import os
import threading
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes, ConversationHandler)
import gspread
from google.oauth2.service_account import Credentials

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8629040648:AAHBLeQlhNmMUPlZdzLdKA4PDDl4NnMHGGA"
SPREADSHEET_ID = "1nw-0J3AFRTRBZBpua859wjCFKjxUcZazHcJYqax0byg"
CREDENTIALS_FILE = "credentials.json"

SHEET_OPERATIONS = "Операции"
SHEET_SALARIES = "Зарплаты"
SHEET_USERS = "Пользователи"
SHEET_SETTINGS = "Настройки"

ROLE_DIRECTOR = "директор"
ROLE_ACCOUNTANT = "бухгалтер"
ROLE_SUPPLIER = "снабженец"

EXPENSE_CATEGORIES = ["🪵 Материалы", "👷 Зарплаты", "💡 Коммунальные", "⚙️ Производство"]
INCOME_CATEGORIES = ["🛋 Продажи мебели", "💰 Аванс", "📦 Прочие поступления"]

(CATEGORY, AMOUNT, COMMENT,
 SALARY_NAME, SALARY_POSITION, SALARY_BASE, SALARY_ADVANCE) = range(7)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== FLASK ====================
flask_app = Flask(__name__)

@flask_app.route('/miniapp')
def miniapp():
    with open('miniapp.html', encoding='utf-8') as f:
        return f.read()

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port)

# ==================== GOOGLE SHEETS ====================
def get_sheet(sheet_name):
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(sheet_name)

def get_user_role(telegram_id: int):
    try:
        sheet = get_sheet(SHEET_USERS)
        records = sheet.get_all_records()
        logger.info(f"Ищем ID: {telegram_id}, всего записей: {len(records)}")
        for row in records:
            row_id = str(row.get("Telegram ID", "")).strip()
            logger.info(f"Строка: ID={row_id}, роль={row.get('Роль')}, активен={row.get('Активен')}")
            if row_id == str(telegram_id):
                aktivnost = str(row.get("Активен", "")).lower().strip()
                if aktivnost == "да":
                    return row.get("Роль", "").lower().strip(), row.get("Имя", "Пользователь")
                else:
                    return row.get("Роль", "ожидание").lower().strip(), row.get("Имя", "Пользователь")
        return None, None
    except Exception as e:
        logger.error(f"Ошибка получения роли: {e}")
        return None, None

def register_user(telegram_id: int, name: str):
    try:
        sheet = get_sheet(SHEET_USERS)
        records = sheet.get_all_records()
        for row in records:
            if str(row.get("Telegram ID", "")).strip() == str(telegram_id):
                return False  # уже есть
        sheet.append_row([telegram_id, name, "ожидание", "нет"])
        return True
    except Exception as e:
        logger.error(f"Ошибка регистрации: {e}")
        return False

def get_next_number(sheet_name):
    try:
        sheet = get_sheet(sheet_name)
        records = sheet.get_all_records()
        return len(records) + 1
    except:
        return 1

def add_operation(data: dict, user_name: str):
    sheet = get_sheet(SHEET_OPERATIONS)
    num = get_next_number(SHEET_OPERATIONS)
    today = datetime.now().strftime("%d.%m.%Y")
    row = [num, today, data["type"], data["category"],
           data["amount"], user_name, data.get("comment", "")]
    sheet.append_row(row)

def add_salary(data: dict, user_name: str):
    sheet = get_sheet(SHEET_SALARIES)
    num = get_next_number(SHEET_SALARIES)
    today = datetime.now().strftime("%d.%m.%Y")
    total = int(data["base"]) - int(data["advance"])
    row = [num, today, data["worker_name"], data["position"],
           data["base"], data["advance"], total, user_name]
    sheet.append_row(row)
    return total

def check_limit(category: str, amount: int):
    try:
        sheet = get_sheet(SHEET_SETTINGS)
        records = sheet.get_all_records()
        cat_map = {
            "🪵 Материалы": "лимит_материалы",
            "👷 Зарплаты": "лимит_зарплаты",
            "💡 Коммунальные": "лимит_коммунальные",
            "⚙️ Производство": "лимит_производство",
        }
        param = cat_map.get(category)
        if not param:
            return None, None
        for row in records:
            if row.get("Параметр") == param:
                limit = int(row.get("Значение", 0))
                ops_sheet = get_sheet(SHEET_OPERATIONS)
                ops = ops_sheet.get_all_records()
                month = datetime.now().strftime("%m.%Y")
                total = sum(
                    abs(int(str(r.get("Сумма", 0)).replace("-", "").replace("+", "")))
                    for r in ops
                    if r.get("Категория") == category
                    and r.get("Тип") == "Расход"
                    and str(r.get("Дата", "")).endswith(month)
                )
                return limit, total + amount
        return None, None
    except Exception as e:
        logger.error(f"Ошибка проверки лимита: {e}")
        return None, None

def get_director_id():
    try:
        sheet = get_sheet(SHEET_USERS)
        records = sheet.get_all_records()
        for row in records:
            if str(row.get("Роль", "")).lower() == ROLE_DIRECTOR:
                return int(row.get("Telegram ID", 0))
        return None
    except:
        return None

# ==================== КЛАВИАТУРЫ ====================
def main_keyboard(role):
    buttons = [
        [InlineKeyboardButton("📥 Расход / Xarajat", callback_data="type_expense")],
        [InlineKeyboardButton("📤 Доход / Daromad", callback_data="type_income")],
    ]
    if role in [ROLE_ACCOUNTANT, ROLE_DIRECTOR]:
        buttons.append([InlineKeyboardButton("📋 Сводка сегодня", callback_data="summary")])
    if role == ROLE_DIRECTOR:
        buttons.append([InlineKeyboardButton("📈 Дашборд", callback_data="dashboard")])
        buttons.append([InlineKeyboardButton("⚙️ Настройки", callback_data="settings")])
    buttons.append([InlineKeyboardButton("🕐 Мои записи", callback_data="my_records")])
    return InlineKeyboardMarkup(buttons)

def category_keyboard(cat_type):
    cats = EXPENSE_CATEGORIES if cat_type == "expense" else INCOME_CATEGORIES
    buttons = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in cats]
    buttons.append([InlineKeyboardButton("❌ Отмена / Bekor", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def skip_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить / O'tish", callback_data="skip_comment")],
        [InlineKeyboardButton("❌ Отмена / Bekor", callback_data="cancel")]
    ])

def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить / Tasdiqlash", callback_data="confirm")],
        [InlineKeyboardButton("❌ Отмена / Bekor", callback_data="cancel")]
    ])

def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад / Orqaga", callback_data="back_main")]
    ])

# ==================== ХЕНДЛЕРЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_full_name = update.effective_user.full_name
    role, name = get_user_role(user_id)

    if not role:
        # Новый пользователь — регистрируем
        registered = register_user(user_id, user_full_name)
        if registered:
            await update.message.reply_text(
                f"👋 Salom, {user_full_name}!\n\n"
                f"✅ Siz ro'yxatdan o'tdingiz!\n"
                f"Вы зарегистрированы!\n\n"
                f"⏳ Direktor sizga rol berishi kerak.\n"
                f"Директор должен открыть таблицу и дать вам роль.\n\n"
                f"После получения роли напишите /start снова."
            )
        else:
            await update.message.reply_text(
                f"⏳ {user_full_name}, ваша заявка уже отправлена.\n"
                f"Ожидайте пока директор даст вам доступ.\n"
                f"Потом напишите /start снова."
            )
        return ConversationHandler.END

    if role == "ожидание":
        await update.message.reply_text(
            f"⏳ {name}, sizning so'rovingiz ko'rib chiqilmoqda.\n"
            f"Ваш запрос ещё не обработан.\n"
            f"Директор должен дать вам роль в таблице.\n"
            f"Потом напишите /start снова."
        )
        return ConversationHandler.END

    context.user_data["role"] = role
    context.user_data["name"] = name
    text = (
        f"Assalomu alaykum, {name}! 👋\n\n"
        f"🏭 JM Мебельный завод — Учёт\n"
        f"Роль: {role.upper()}\n\n"
        f"Нима qilmoqchisiz? / Что хотите сделать?"
    )
    await update.message.reply_text(text, reply_markup=main_keyboard(role))
    return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    role, name = get_user_role(user_id)
    if not role or role == "ожидание":
        await query.edit_message_text("⛔️ Доступ запрещён. Напишите /start")
        return ConversationHandler.END
    context.user_data["role"] = role
    context.user_data["name"] = name

    if data == "type_expense":
        context.user_data["op_type"] = "Расход"
        await query.edit_message_text(
            "📋 Kategoriyani tanlang / Выберите категорию:",
            reply_markup=category_keyboard("expense")
        )
        return CATEGORY
    elif data == "type_income":
        context.user_data["op_type"] = "Доход"
        await query.edit_message_text(
            "📋 Kategoriyani tanlang / Выберите категорию:",
            reply_markup=category_keyboard("income")
        )
        return CATEGORY
    elif data == "summary":
        await show_summary(query)
    elif data == "dashboard":
        await show_dashboard(query)
    elif data == "settings":
        await show_users(query)
    elif data == "my_records":
        await show_my_records(query, name)
    elif data == "back_main":
        await query.edit_message_text(
            "Нима qilmoqchisiz? / Что хотите сделать?",
            reply_markup=main_keyboard(role)
        )
    elif data == "cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ Bekor qilindi / Отменено.", reply_markup=main_keyboard(role))
    return ConversationHandler.END

async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    role = context.user_data.get("role", ROLE_SUPPLIER)
    if data == "cancel":
        await query.edit_message_text("❌ Отменено.", reply_markup=main_keyboard(role))
        return ConversationHandler.END
    category = data.replace("cat_", "")
    context.user_data["category"] = category
    if category == "👷 Зарплаты":
        await query.edit_message_text("👷 Работник ismi / Имя работника:")
        return SALARY_NAME
    await query.edit_message_text(
        f"✅ {category}\n\n💰 Summani kiriting / Введите сумму (цифры):"
    )
    return AMOUNT

async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.replace(" ", "").replace(",", ""))
        context.user_data["amount"] = amount
        category = context.user_data.get("category", "")
        op_type = context.user_data.get("op_type", "Расход")
        await update.message.reply_text(
            f"📌 {op_type}: {category}\n💰 {amount:,} сум\n\n💬 Izoh / Комментарий:",
            reply_markup=skip_keyboard()
        )
        return COMMENT
    except:
        await update.message.reply_text("❌ Faqat raqam / Только цифры!")
        return AMOUNT

async def get_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    role = context.user_data.get("role", ROLE_SUPPLIER)
    name = context.user_data.get("name", "")
    if query:
        await query.answer()
        if query.data == "skip_comment":
            context.user_data["comment"] = ""
            await save_operation(query, context, name, role)
        elif query.data == "cancel":
            await query.edit_message_text("❌ Отменено.", reply_markup=main_keyboard(role))
    else:
        context.user_data["comment"] = update.message.text.strip()
        await save_operation_msg(update, context, name, role)
    return ConversationHandler.END

async def save_operation(query, context, name, role):
    try:
        amount = context.user_data["amount"]
        op_type = context.user_data["op_type"]
        category = context.user_data["category"]
        comment = context.user_data.get("comment", "")
        add_operation({"type": op_type, "category": category, "amount": amount, "comment": comment}, name)
        text = (
            f"✅ Saqlandi! / Записано!\n\n"
            f"📌 {op_type}: {category}\n"
            f"💰 {amount:,} сум\n"
            f"💬 {comment or '—'}\n"
            f"👤 {name}\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        if op_type == "Расход":
            limit, spent = check_limit(category, amount)
            if limit and spent and spent > limit:
                text += f"\n\n⚠️ Лимит превышен!\n{spent:,} / {limit:,} сум"
                director_id = get_director_id()
                if director_id:
                    await query.get_bot().send_message(
                        director_id,
                        f"🔔 ЛИМИТ ПРЕВЫШЕН!\nКатегория: {category}\n"
                        f"Потрачено: {spent:,} / {limit:,} сум\nДобавил: {name}"
                    )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Yana / Ещё", callback_data="type_expense")],
            [InlineKeyboardButton("🏠 Главная", callback_data="back_main")]
        ])
        await query.edit_message_text(text, reply_markup=buttons)
    except Exception as e:
        await query.edit_message_text(f"❌ Xatolik: {e}")

async def save_operation_msg(update, context, name, role):
    try:
        amount = context.user_data["amount"]
        op_type = context.user_data["op_type"]
        category = context.user_data["category"]
        comment = context.user_data.get("comment", "")
        add_operation({"type": op_type, "category": category, "amount": amount, "comment": comment}, name)
        text = (
            f"✅ Saqlandi!\n\n📌 {op_type}: {category}\n"
            f"💰 {amount:,} сум\n💬 {comment or '—'}\n"
            f"👤 {name}\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Yana / Ещё", callback_data="type_expense")],
            [InlineKeyboardButton("🏠 Главная", callback_data="back_main")]
        ])
        await update.message.reply_text(text, reply_markup=buttons)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def salary_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["worker_name"] = update.message.text.strip()
    await update.message.reply_text("💼 Lavozim / Должность:")
    return SALARY_POSITION

async def salary_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["position"] = update.message.text.strip()
    await update.message.reply_text("💰 Oylik / Оклад (сум):")
    return SALARY_BASE

async def salary_base(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base = int(update.message.text.replace(" ", "").replace(",", ""))
        context.user_data["salary_base"] = base
        await update.message.reply_text("💵 Avans / Аванс (0 если нет):")
        return SALARY_ADVANCE
    except:
        await update.message.reply_text("❌ Только цифры!")
        return SALARY_BASE

async def salary_advance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        advance = int(update.message.text.replace(" ", "").replace(",", ""))
        base = context.user_data["salary_base"]
        total = base - advance
        context.user_data["salary_advance"] = advance
        name = context.user_data["name"]
        text = (
            f"📋 Tekshiring:\n\n"
            f"👷 {context.user_data['worker_name']}\n"
            f"💼 {context.user_data['position']}\n"
            f"💰 Оклад: {base:,} сум\n"
            f"💵 Аванс: {advance:,} сум\n"
            f"✅ К выдаче: {total:,} сум\n"
            f"👤 {name}"
        )
        await update.message.reply_text(text, reply_markup=confirm_keyboard())
        return COMMENT
    except:
        await update.message.reply_text("❌ Только цифры!")
        return SALARY_ADVANCE

async def show_summary(query):
    try:
        sheet = get_sheet(SHEET_OPERATIONS)
        records = sheet.get_all_records()
        today = datetime.now().strftime("%d.%m.%Y")
        today_ops = [r for r in records if r.get("Дата") == today]
        total_expense = sum(int(str(r.get("Сумма", 0)).replace("-", "").replace("+", "")) for r in today_ops if r.get("Тип") == "Расход")
        total_income = sum(int(str(r.get("Сумма", 0)).replace("-", "").replace("+", "")) for r in today_ops if r.get("Тип") == "Доход")
        text = f"📋 Сводка за {today}:\n\n📤 Доходы: +{total_income:,}\n📥 Расходы: -{total_expense:,}\n💰 Баланс: {total_income-total_expense:,} сум\n\n"
        if today_ops:
            text += "📝 Последние:\n"
            for r in today_ops[-5:]:
                sign = "+" if r.get("Тип") == "Доход" else "-"
                amt = int(str(r.get("Сумма", 0)).replace("-", "").replace("+", ""))
                text += f"• {r.get('Категория')} {sign}{amt:,} — {r.get('Кто внёс')}\n"
        else:
            text += "Записей нет"
        await query.edit_message_text(text, reply_markup=back_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def show_dashboard(query):
    try:
        sheet = get_sheet(SHEET_OPERATIONS)
        records = sheet.get_all_records()
        month = datetime.now().strftime("%m.%Y")
        month_ops = [r for r in records if str(r.get("Дата", "")).endswith(month)]
        total_income = sum(int(str(r.get("Сумма", 0)).replace("-", "").replace("+", "")) for r in month_ops if r.get("Тип") == "Доход")
        total_expense = sum(int(str(r.get("Сумма", 0)).replace("-", "").replace("+", "")) for r in month_ops if r.get("Тип") == "Расход")
        text = f"📈 Дашборд — {datetime.now().strftime('%B %Y')}\n\n📤 Доходы: +{total_income:,}\n📥 Расходы: -{total_expense:,}\n💰 Баланс: {total_income-total_expense:,} сум\n\n📊 По категориям:\n"
        for cat in EXPENSE_CATEGORIES:
            cat_total = sum(int(str(r.get("Сумма", 0)).replace("-", "").replace("+", "")) for r in month_ops if r.get("Категория") == cat and r.get("Тип") == "Расход")
            if cat_total > 0:
                text += f"• {cat}: {cat_total:,} сум\n"
        await query.edit_message_text(text, reply_markup=back_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def show_my_records(query, name):
    try:
        sheet = get_sheet(SHEET_OPERATIONS)
        records = sheet.get_all_records()
        my_records = [r for r in records if r.get("Кто внёс") == name][-5:]
        text = "🕐 Мои последние записи:\n\n"
        if my_records:
            for r in my_records:
                sign = "+" if r.get("Тип") == "Доход" else "-"
                amt = int(str(r.get("Сумма", 0)).replace("-", "").replace("+", ""))
                text += f"• {r.get('Дата')} {r.get('Категория')} {sign}{amt:,}\n  {r.get('Комментарий') or '—'}\n\n"
        else:
            text += "Записей нет"
        await query.edit_message_text(text, reply_markup=back_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def show_users(query):
    try:
        sheet = get_sheet(SHEET_USERS)
        records = sheet.get_all_records()
        text = "👥 Пользователи:\n\n"
        for r in records:
            status = "✅" if str(r.get("Активен", "")).lower() == "да" else "⏳"
            text += f"{status} {r.get('Имя')} — {r.get('Роль')}\n"
        await query.edit_message_text(text, reply_markup=back_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

# ==================== ЗАПУСК ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(button_handler)
        ],
        states={
            CATEGORY: [CallbackQueryHandler(category_chosen)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            COMMENT: [
                CallbackQueryHandler(get_comment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_comment)
            ],
            SALARY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, salary_name)],
            SALARY_POSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, salary_position)],
            SALARY_BASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, salary_base)],
            SALARY_ADVANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, salary_advance)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False
    )
    app.add_handler(conv_handler)
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Flask сервер запущен!")
    main()
