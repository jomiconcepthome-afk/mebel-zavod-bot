import logging
import os
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import gspread
from google.oauth2.service_account import Credentials

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8629040648:AAHBLeQlhNmMUPlZdzLdKA4PDDl4NnMHGGA"
SPREADSHEET_ID = "1nw-0J3AFRTRBZBpua859wjCFKjxUcZazHcJYqax0byg"
CREDENTIALS_FILE = "credentials.json"

# Листы таблицы
SHEET_OPERATIONS = "Операции"
SHEET_SALARIES = "Зарплаты"
SHEET_USERS = "Пользователи"
SHEET_SETTINGS = "Настройки"

# Роли
ROLE_DIRECTOR = "директор"
ROLE_ACCOUNTANT = "бухгалтер"
ROLE_SUPPLIER = "снабженец"

# Категории расходов
EXPENSE_CATEGORIES = ["🪵 Материалы", "👷 Зарплаты", "💡 Коммунальные", "⚙️ Производство"]
INCOME_CATEGORIES = ["🛋 Продажи мебели", "💰 Аванс", "📦 Прочие поступления"]

# Состояния разговора
(TYPE, CATEGORY, AMOUNT, COMMENT,
 SALARY_NAME, SALARY_POSITION, SALARY_BASE, SALARY_ADVANCE,
 ADD_USER_ID, ADD_USER_NAME, ADD_USER_ROLE) = range(11)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== GOOGLE SHEETS ====================
def get_sheets_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet(sheet_name):
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(sheet_name)

def get_user_role(telegram_id: int):
    try:
        sheet = get_sheet(SHEET_USERS)
        records = sheet.get_all_records()
        for row in records:
            if str(row.get("Telegram ID", "")) == str(telegram_id):
                if str(row.get("Активен", "")).lower() == "да":
                    return row.get("Роль", "").lower(), row.get("Имя", "Пользователь")
        return None, None
    except Exception as e:
        logger.error(f"Ошибка получения роли: {e}")
        return None, None

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
        buttons.append([InlineKeyboardButton("📈 Дашборд по категориям", callback_data="dashboard")])
        buttons.append([InlineKeyboardButton("⚙️ Настройки / Sozlamalar", callback_data="settings")])
    buttons.append([InlineKeyboardButton("🕐 Мои записи / Yozuvlarim", callback_data="my_records")])
    return InlineKeyboardMarkup(buttons)

def category_keyboard(cat_type):
    cats = EXPENSE_CATEGORIES if cat_type == "expense" else INCOME_CATEGORIES
    buttons = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in cats]
    buttons.append([InlineKeyboardButton("❌ Отмена / Bekor", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить / Tasdiqlash", callback_data="confirm")],
        [InlineKeyboardButton("❌ Отмена / Bekor", callback_data="cancel")]
    ])

def skip_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить / O'tish", callback_data="skip_comment")],
        [InlineKeyboardButton("❌ Отмена / Bekor", callback_data="cancel")]
    ])

def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Пользователи", callback_data="settings_users")],
        [InlineKeyboardButton("🔔 Лимиты по категориям", callback_data="settings_limits")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")]
    ])

# ==================== ХЕНДЛЕРЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role, name = get_user_role(user_id)

    if not role:
        await update.message.reply_text(
            "⛔️ Siz ro'yxatda yo'qsiz.\n"
            "Вы не зарегистрированы в системе.\n"
            "Директорга murojaat qiling / Обратитесь к директору."
        )
        return ConversationHandler.END

    context.user_data["role"] = role
    context.user_data["name"] = name

    greeting = (
        f"Assalomu alaykum, {name}! 👋\n"
        f"Salom, {name}!\n\n"
        f"🏭 JM Мебельный завод — Учёт\n"
        f"Роль / Lavozim: {role.upper()}\n\n"
        f"Нима qilmoqchisiz? / Что хотите сделать?"
    )
    await update.message.reply_text(greeting, reply_markup=main_keyboard(role))
    return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    role, name = get_user_role(user_id)

    if not role:
        await query.edit_message_text("⛔️ Доступ запрещён.")
        return ConversationHandler.END

    context.user_data["role"] = role
    context.user_data["name"] = name

    # Тип операции
    if data in ["type_expense", "type_income"]:
        context.user_data["op_type"] = "Расход" if data == "type_expense" else "Доход"
        cat_type = "expense" if data == "type_expense" else "income"
        text = "📋 Kategoriyani tanlang / Выберите категорию:"
        await query.edit_message_text(text, reply_markup=category_keyboard(cat_type))
        return CATEGORY

    # Сводка
    elif data == "summary":
        await show_summary(query)
        return ConversationHandler.END

    # Дашборд
    elif data == "dashboard":
        await show_dashboard(query)
        return ConversationHandler.END

    # Настройки
    elif data == "settings":
        await query.edit_message_text("⚙️ Настройки / Sozlamalar:", reply_markup=settings_keyboard())
        return ConversationHandler.END

    # Мои записи
    elif data == "my_records":
        await show_my_records(query, name)
        return ConversationHandler.END

    # Настройки пользователи
    elif data == "settings_users":
        await show_users(query)
        return ConversationHandler.END

    # Отмена
    elif data == "cancel":
        context.user_data.clear()
        context.user_data["role"] = role
        context.user_data["name"] = name
        await query.edit_message_text(
            "❌ Bekor qilindi / Отменено.\n\nНима qilmoqchisiz?",
            reply_markup=main_keyboard(role)
        )
        return ConversationHandler.END

    elif data == "back_main":
        await query.edit_message_text("Нима qilmoqchisiz?", reply_markup=main_keyboard(role))
        return ConversationHandler.END

    return ConversationHandler.END

async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        role = context.user_data.get("role", ROLE_SUPPLIER)
        await query.edit_message_text("❌ Отменено.", reply_markup=main_keyboard(role))
        return ConversationHandler.END

    category = data.replace("cat_", "")
    context.user_data["category"] = category

    if category == "👷 Зарплаты":
        await query.edit_message_text(
            "👷 Работник ismi / Имя работника:\n(Masalan: Alisher Karimov)"
        )
        return SALARY_NAME

    await query.edit_message_text(
        f"✅ Kategoriya: {category}\n\n"
        f"💰 Summani kiriting / Введите сумму (только цифры):"
    )
    return AMOUNT

async def salary_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["worker_name"] = update.message.text.strip()
    await update.message.reply_text("💼 Lavozim / Должность:\n(Masalan: Usta, Snabjenets...)")
    return SALARY_POSITION

async def salary_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["position"] = update.message.text.strip()
    await update.message.reply_text("💰 Oylik maosh / Оклад (сум):")
    return SALARY_BASE

async def salary_base(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base = int(update.message.text.replace(" ", "").replace(",", ""))
        context.user_data["salary_base"] = base
        await update.message.reply_text(
            "💵 Avans miqdori / Сумма аванса (сум):\n"
            "(Agar avans yo'q bo'lsa / Если аванса нет — напишите 0)"
        )
        return SALARY_ADVANCE
    except:
        await update.message.reply_text("❌ Faqat raqam / Только цифры! Qaytadan / Повторите:")
        return SALARY_BASE

async def salary_advance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        advance = int(update.message.text.replace(" ", "").replace(",", ""))
        base = context.user_data["salary_base"]
        total = base - advance
        name = context.user_data["name"]

        text = (
            f"📋 Tekshiring / Проверьте:\n\n"
            f"👷 Работник: {context.user_data['worker_name']}\n"
            f"💼 Должность: {context.user_data['position']}\n"
            f"💰 Оклад: {base:,} сум\n"
            f"💵 Аванс: {advance:,} сум\n"
            f"✅ Итого к выдаче: {total:,} сум\n"
            f"👤 Кто внёс: {name}"
        )
        context.user_data["salary_advance"] = advance
        await update.message.reply_text(text, reply_markup=confirm_keyboard())
        return COMMENT
    except:
        await update.message.reply_text("❌ Faqat raqam / Только цифры!")
        return SALARY_ADVANCE

async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.replace(" ", "").replace(",", ""))
        context.user_data["amount"] = amount
        op_type = context.user_data.get("op_type", "Расход")
        category = context.user_data.get("category", "")

        text = (
            f"📋 Tekshiring:\n"
            f"📌 Тип: {op_type}\n"
            f"🏷 Категория: {category}\n"
            f"💰 Сумма: {amount:,} сум\n\n"
            f"💬 Izoh qo'shing / Добавьте комментарий:"
        )
        await update.message.reply_text(text, reply_markup=skip_keyboard())
        return COMMENT
    except:
        await update.message.reply_text("❌ Faqat raqam / Только цифры! Qaytadan:")
        return AMOUNT

async def get_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == "skip_comment":
            context.user_data["comment"] = ""
            await finalize(query, context, is_salary=False)
        elif query.data == "confirm":
            await finalize(query, context, is_salary=True)
        elif query.data == "cancel":
            role = context.user_data.get("role", ROLE_SUPPLIER)
            await query.edit_message_text("❌ Отменено.", reply_markup=main_keyboard(role))
    else:
        context.user_data["comment"] = update.message.text.strip()
        await finalize_msg(update, context)
    return ConversationHandler.END

async def finalize(query, context, is_salary=False):
    name = context.user_data.get("name", "Неизвестно")
    role = context.user_data.get("role", ROLE_SUPPLIER)

    try:
        if is_salary:
            data = {
                "worker_name": context.user_data["worker_name"],
                "position": context.user_data["position"],
                "base": context.user_data["salary_base"],
                "advance": context.user_data["salary_advance"],
            }
            total = add_salary(data, name)
            op_data = {
                "type": "Расход",
                "category": "👷 Зарплаты",
                "amount": total,
                "comment": f"{data['worker_name']} · зарплата"
            }
            add_operation(op_data, name)
            text = (
                f"✅ Saqlandi! / Записано!\n\n"
                f"👷 {data['worker_name']}\n"
                f"💼 {data['position']}\n"
                f"💰 Оклад: {int(data['base']):,} сум\n"
                f"💵 Аванс: {int(data['advance']):,} сум\n"
                f"✅ Выдано: {total:,} сум\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        else:
            amount = context.user_data["amount"]
            op_type = context.user_data["op_type"]
            category = context.user_data["category"]
            comment = context.user_data.get("comment", "")
            op_data = {"type": op_type, "category": category,
                       "amount": amount, "comment": comment}
            add_operation(op_data, name)

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
                    text += f"\n\n⚠️ DIQQAT! Limit oshdi!\nЛимит превышен!\n{spent:,} / {limit:,} сум"
                    director_id = get_director_id()
                    if director_id:
                        await query.get_bot().send_message(
                            director_id,
                            f"🔔 ЛИМИТ ПРЕВЫШЕН!\n"
                            f"Категория: {category}\n"
                            f"Потрачено: {spent:,} / {limit:,} сум\n"
                            f"Добавил: {name}"
                        )

        context.user_data.clear()
        context.user_data["role"] = role
        context.user_data["name"] = name

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Yana kiritish / Ещё запись", callback_data=f"type_expense")],
            [InlineKeyboardButton("🏠 Bosh sahifa / Главная", callback_data="back_main")]
        ])
        await query.edit_message_text(text, reply_markup=buttons)

    except Exception as e:
        logger.error(f"Ошибка записи: {e}")
        await query.edit_message_text(f"❌ Xatolik / Ошибка: {e}\n\nQaytadan urinib ko'ring.")

async def finalize_msg(update, context):
    name = context.user_data.get("name", "Неизвестно")
    role = context.user_data.get("role", ROLE_SUPPLIER)
    amount = context.user_data["amount"]
    op_type = context.user_data["op_type"]
    category = context.user_data["category"]
    comment = context.user_data.get("comment", "")

    try:
        op_data = {"type": op_type, "category": category,
                   "amount": amount, "comment": comment}
        add_operation(op_data, name)

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

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Yana / Ещё", callback_data="type_expense")],
            [InlineKeyboardButton("🏠 Главная", callback_data="back_main")]
        ])
        await update.message.reply_text(text, reply_markup=buttons)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def show_summary(query):
    try:
        sheet = get_sheet(SHEET_OPERATIONS)
        records = sheet.get_all_records()
        today = datetime.now().strftime("%d.%m.%Y")
        today_ops = [r for r in records if r.get("Дата") == today]

        total_expense = sum(
            int(str(r.get("Сумма", 0)).replace("-", "").replace("+", ""))
            for r in today_ops if r.get("Тип") == "Расход"
        )
        total_income = sum(
            int(str(r.get("Сумма", 0)).replace("-", "").replace("+", ""))
            for r in today_ops if r.get("Тип") == "Доход"
        )

        text = f"📋 Bugungi svodka / Сводка за {today}:\n\n"
        text += f"📤 Доходы: +{total_income:,} сум\n"
        text += f"📥 Расходы: -{total_expense:,} сум\n"
        text += f"💰 Баланс: {total_income - total_expense:,} сум\n\n"

        if today_ops:
            text += "📝 Записи:\n"
            for r in today_ops[-5:]:
                sign = "+" if r.get("Тип") == "Доход" else "-"
                text += f"• {r.get('Категория')} {sign}{int(str(r.get('Сумма',0)).replace('-','').replace('+','')):,} — {r.get('Кто внёс')}\n"
        else:
            text += "Записей нет / Yozuvlar yo'q"

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад / Orqaga", callback_data="back_main")]
        ]))
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def show_dashboard(query):
    try:
        sheet = get_sheet(SHEET_OPERATIONS)
        records = sheet.get_all_records()
        month = datetime.now().strftime("%m.%Y")

        month_ops = [r for r in records if str(r.get("Дата", "")).endswith(month)]
        total_income = sum(
            int(str(r.get("Сумма", 0)).replace("-", "").replace("+", ""))
            for r in month_ops if r.get("Тип") == "Доход"
        )
        total_expense = sum(
            int(str(r.get("Сумма", 0)).replace("-", "").replace("+", ""))
            for r in month_ops if r.get("Тип") == "Расход"
        )

        text = f"📈 Дашборд — {datetime.now().strftime('%B %Y')}\n\n"
        text += f"📤 Доходы: +{total_income:,} сум\n"
        text += f"📥 Расходы: -{total_expense:,} сум\n"
        text += f"💰 Баланс: {total_income - total_expense:,} сум\n\n"
        text += "📊 По категориям:\n"

        for cat in EXPENSE_CATEGORIES:
            cat_total = sum(
                int(str(r.get("Сумма", 0)).replace("-", "").replace("+", ""))
                for r in month_ops
                if r.get("Категория") == cat and r.get("Тип") == "Расход"
            )
            if cat_total > 0:
                text += f"• {cat}: {cat_total:,} сум\n"

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")]
        ]))
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def show_my_records(query, name):
    try:
        sheet = get_sheet(SHEET_OPERATIONS)
        records = sheet.get_all_records()
        my_records = [r for r in records if r.get("Кто внёс") == name][-5:]

        text = f"🕐 Mening yozuvlarim / Мои последние записи:\n\n"
        if my_records:
            for r in my_records:
                sign = "+" if r.get("Тип") == "Доход" else "-"
                text += (f"• {r.get('Дата')} {r.get('Категория')} "
                         f"{sign}{int(str(r.get('Сумма',0)).replace('-','').replace('+','')):,} сум\n"
                         f"  {r.get('Комментарий') or '—'}\n\n")
        else:
            text += "Записей нет / Yozuvlar yo'q"

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")]
        ]))
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def show_users(query):
    try:
        sheet = get_sheet(SHEET_USERS)
        records = sheet.get_all_records()
        text = "👥 Пользователи / Foydalanuvchilar:\n\n"
        for r in records:
            status = "✅" if str(r.get("Активен", "")).lower() == "да" else "❌"
            text += f"{status} {r.get('Имя')} — {r.get('Роль')}\n"

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="settings")]
        ]))
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
    main()
