import os
import asyncio
import feedparser
import json
import requests
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# News sources
CRYPTO_NEWS_FEEDS = [
    "https://cointelegraph.com/feed",
    "https://feeds.decrypt.co/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptonews.com/feed/",
    "https://theblock.co/feed",
    "https://blockchair.com/feed",
    "https://messari.io/feed",
    "https://cryptoslate.com/feed/",
]

TON_KEYWORDS = [
    "TON", "TONCOIN", "Ton blockchain", "Telegram TON", "TON coin",
    "ton network", "ton token", "ton ecosystem", "crypto ton", "ton price",
    "ton trading", "ton news"
]

# Storage files
SENT_NEWS_FILE = "sent_news.json"
LAST_NEWS_FILE = "last_news.json"
USERS_LANG_FILE = "user_languages.json"

# Price cache
price_cache = {"price": None, "timestamp": None}
user_languages = {}


def load_user_languages():
    """Load user language preferences"""
    global user_languages
    if Path(USERS_LANG_FILE).exists():
        try:
            with open(USERS_LANG_FILE, 'r') as f:
                user_languages = json.load(f)
        except:
            user_languages = {}
    else:
        user_languages = {}


def save_user_language(user_id, lang):
    """Save user language preference"""
    global user_languages
    user_languages[str(user_id)] = lang
    with open(USERS_LANG_FILE, 'w') as f:
        json.dump(user_languages, f)


def get_user_language(user_id):
    """Get user language preference"""
    return user_languages.get(str(user_id), "ru")


def clean_html(text):
    """Remove HTML tags from text"""
    if not text:
        return ""
    text = re.sub('<[^<]+?>', '', text)
    text = text.replace('&quot;',
                        '"').replace('&amp;',
                                     '&').replace('&lt;',
                                                  '<').replace('&gt;', '>')
    text = text.replace('&#39;', "'").replace('&nbsp;', ' ')
    text = ' '.join(text.split())
    return text


CONTENT_HASHES_FILE = "content_hashes.json"


def load_content_hashes():
    """Load saved content hashes"""
    if Path(CONTENT_HASHES_FILE).exists():
        try:
            with open(CONTENT_HASHES_FILE, 'r') as f:
                return set(json.load(f))
        except:
            return set()
    return set()


def save_content_hashes(hashes):
    """Save content hashes"""
    with open(CONTENT_HASHES_FILE, 'w') as f:
        json.dump(list(hashes), f)


def get_content_hash(title: str, content: str) -> str:
    """Create hash of content to detect duplicates"""
    combined = f"{title}_{content}".lower()
    # Remove common words
    for word in [
            "ton", "news", "price", "blockchain", "crypto", "token", "musk",
            "elon", "twitter", "spacex"
    ]:
        combined = combined.replace(word, "")
    words = combined.split()[:15]  # More words for better uniqueness
    text_key = "_".join(words)
    return hashlib.md5(text_key.encode()).hexdigest()[:12]


class NewsManager:
    """Manages news state with duplicate detection"""

    def __init__(self):
        self.sent_news = self.load_sent_news()
        self.last_news = self.load_last_news()
        self.content_hashes = load_content_hashes()  # Load from file

    def load_sent_news(self):
        if Path(SENT_NEWS_FILE).exists():
            try:
                with open(SENT_NEWS_FILE, 'r') as f:
                    return set(json.load(f))
            except:
                return set()
        return set()

    def load_last_news(self):
        if Path(LAST_NEWS_FILE).exists():
            try:
                with open(LAST_NEWS_FILE, 'r') as f:
                    return json.load(f)
            except:
                return None
        return None

    def save_sent_news(self):
        with open(SENT_NEWS_FILE, 'w') as f:
            json.dump(list(self.sent_news), f)

    def save_last_news(self, news):
        with open(LAST_NEWS_FILE, 'w') as f:
            json.dump(news, f)

    def is_sent(self, link):
        return link in self.sent_news

    def is_duplicate_content(self, title: str, content: str) -> bool:
        content_hash = get_content_hash(title, content)
        if content_hash in self.content_hashes:
            print(f"⚠️  Дубликат (по содержанию): {title[:40]}")
            return True
        self.content_hashes.add(content_hash)
        save_content_hashes(self.content_hashes)  # Save to file
        return False

    def mark_sent(self, link):
        self.sent_news.add(link)
        self.save_sent_news()


news_manager = NewsManager()
load_user_languages()


def is_ton_related(text: str) -> bool:
    """Check if text mentions TON"""
    text_lower = text.lower()
    for keyword in TON_KEYWORDS:
        if keyword.lower() in text_lower:
            return True
    return False


async def analyze_news_with_ai(news_title: str,
                               news_content: str,
                               lang: str = "ru") -> str:
    """Analyze news using OpenAI in specified language"""
    if not OPENAI_API_KEY:
        if "down" in news_title.lower() or "fall" in news_title.lower():
            return "📉 Trend: Negative" if lang == "en" else "📉 Тренд: Отрицательный"
        elif "up" in news_title.lower() or "rise" in news_title.lower():
            return "📈 Trend: Positive" if lang == "en" else "📈 Тренд: Положительный"
        else:
            return "📊 Trend: Neutral" if lang == "en" else "📊 Тренд: Нейтральный"

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        lang_prompt = "English" if lang == "en" else "Russian"
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{
                "role":
                "user",
                "content":
                f"Analyze TON news ({lang_prompt}, 2 sentences): {news_title}"
            }],
            max_tokens=100,
            temperature=0.7)
        return response.choices[0].message.content
    except:
        return "Analysis unavailable" if lang == "en" else "📊 Анализ недоступен"


async def analyze_ton_price_impact(news_title: str, news_content: str, lang: str = "ru") -> str:
    """AI analysis of TON price growth/decline potential"""
    if not OPENAI_API_KEY:
        if "down" in news_title.lower() or "negative" in news_title.lower():
            return "📉 **Вероятный спад** - Это новость может привести к падению цены TON" if lang == "ru" else "📉 **Likely Decline** - This news may cause TON price to fall"
        elif "up" in news_title.lower() or "positive" in news_title.lower():
            return "📈 **Вероятный рост** - Это новость может привести к росту цены TON" if lang == "ru" else "📈 **Likely Growth** - This news may cause TON price to rise"
        else:
            return "↔️ **Нейтральное влияние** - Минимальное влияние на цену TON" if lang == "ru" else "↔️ **Neutral Impact** - Minimal effect on TON price"
    
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        
        lang_text = "Russian" if lang == "ru" else "English"
        prompt = f"""Analyze this TON/crypto news and predict price impact in {lang_text}:
Title: {news_title}
Content: {news_content}

Respond with ONLY one of these formats:
📈 **[Growth/Рост]** - Brief explanation (max 15 words)
📉 **[Decline/Спад]** - Brief explanation (max 15 words)
↔️ **[Neutral/Нейтральное]** - Brief explanation (max 15 words)"""
        
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.7
        )
        return response.choices[0].message.content
    except:
        return "📊 Analysis unavailable" if lang == "en" else "📊 Анализ недоступен"


async def get_ton_price():
    """Get TON price from Binance"""
    global price_cache

    if price_cache["price"] and price_cache["timestamp"]:
        if datetime.now() - price_cache["timestamp"] < timedelta(minutes=5):
            return price_cache["price"]

    try:
        ton_url = "https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT"
        ton_response = requests.get(ton_url, timeout=5)
        ton_data = ton_response.json()

        if "price" not in ton_data:
            return None

        price_usd = float(ton_data["price"])

        ticker_url = "https://api.binance.com/api/v3/ticker/24hr?symbol=TONUSDT"
        ticker_response = requests.get(ticker_url, timeout=5)
        ticker_data = ticker_response.json()
        change_24h = float(ticker_data.get("priceChangePercent", 0))

        usd_rub_rate = 80.0
        try:
            rub_response = requests.get(
                "https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            if rub_response.status_code == 200:
                rub_data = rub_response.json()
                if "rates" in rub_data and "RUB" in rub_data["rates"]:
                    usd_rub_rate = float(rub_data["rates"]["RUB"])
        except:
            pass

        price_rub = price_usd * usd_rub_rate
        change_emoji = "📈" if change_24h > 0 else "📉"

        result = {
            "price_usd": price_usd,
            "price_rub": price_rub,
            "change_24h": change_24h,
            "emoji": change_emoji
        }

        price_cache["price"] = result
        price_cache["timestamp"] = datetime.now()

        return result
    except Exception as e:
        print(f"❌ Price fetch error: {e}")

    return None


async def translate_text(text: str, target_lang: str) -> str:
    """Translate text to target language"""
    try:
        if target_lang == "ru":
            return text  # Already Russian
        elif target_lang == "en":
            import urllib.parse
            encoded_text = urllib.parse.quote(text[:500])
            url = f"https://api.mymemory.translated.net/get?q={encoded_text}&langpair=ru|en"
            response = requests.get(url, timeout=5)
            data = response.json()
            if data.get("responseStatus") == 200:
                return data["responseData"]["translatedText"]
        return text
    except:
        return text


async def fetch_rss_news():
    """Fetch TON news"""
    news_items = []

    for feed_url in CRYPTO_NEWS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")

                if is_ton_related(title) or is_ton_related(summary):
                    if not news_manager.is_sent(
                            link) and not news_manager.is_duplicate_content(
                                title, summary):
                        clean_content = clean_html(summary)
                        news_items.append({
                            "title":
                            clean_html(title),
                            "link":
                            link,
                            "content":
                            clean_content[:200]
                            if clean_content else "No description",
                        })
        except Exception as e:
            print(f"⚠️  Feed error from {feed_url}: {str(e)[:50]}")

    return news_items


async def send_news_alert(app, chat_id, news: dict, user_lang: str = "ru"):
    """Send news to Telegram in user's language"""
    try:
        # Get analysis in user's language
        analysis = await analyze_news_with_ai(news["title"], news["content"],
                                              user_lang)

        # Translate if user wants English
        if user_lang == "en":
            trans_title = await translate_text(news["title"], "en")
            trans_content = await translate_text(news["content"], "en")

            message = f"""📰 <b>TON NEWS</b>

<b>{trans_title}</b>

{trans_content}

<b>Analysis:</b>
{analysis}

🔗 <a href='{news["link"]}'>Read full article</a>

⏰ {datetime.now().strftime('%d.%m %H:%M')}"""
        else:
            # Russian
            message = f"""📰 <b>НОВОСТЬ О TON</b>

<b>{news['title']}</b>

{news['content']}

<b>📊 АНАЛИЗ:</b>
{analysis}

🔗 <a href='{news["link"]}'>Читать полностью</a>

⏰ {datetime.now().strftime('%d.%m %H:%M')}"""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Анализ цены" if user_lang == "ru" else "📊 Price Analysis", 
                                 callback_data="price_impact")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
             InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")]
        ])

        sent_msg = await app.bot.send_message(chat_id=chat_id,
                                              text=message,
                                              parse_mode="HTML",
                                              reply_markup=keyboard,
                                              disable_web_page_preview=True)

        news_manager.mark_sent(news["link"])
        news_manager.last_news = {**news, "message_id": sent_msg.message_id}
        news_manager.save_last_news(news_manager.last_news)
        print(f"✅ Отправлено ({user_lang}): {news['title'][:40]}")
        return True
    except Exception as e:
        print(f"Error sending: {e}")
        return False


# COMMAND HANDLERS
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start - ask for language"""
    user_id = update.effective_user.id

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru")],
         [InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en")]])

    message = """🚀 <b>TON NEWS BOT</b>

Выберите язык / Choose language:"""

    await update.message.reply_text(message,
                                    reply_markup=keyboard,
                                    parse_mode="HTML")


async def lastnews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /lastnews"""
    user_id = update.effective_user.id
    user_lang = get_user_language(user_id)

    if news_manager.last_news:
        news = news_manager.last_news
        analysis = await analyze_news_with_ai(news["title"], news["content"],
                                              user_lang)

        if user_lang == "en":
            trans_title = await translate_text(news["title"], "en")
            trans_content = await translate_text(news["content"], "en")

            message = f"""📰 <b>TON NEWS</b>

<b>{trans_title}</b>

{trans_content}

<b>Analysis:</b>
{analysis}

🔗 <a href='{news["link"]}'>Read full article</a>"""
        else:
            message = f"""📰 <b>ПОСЛЕДНЯЯ НОВОСТЬ</b>

<b>{news['title']}</b>

{news['content']}

<b>📊 АНАЛИЗ:</b>
{analysis}

🔗 <a href='{news["link"]}'>Читать полностью</a>"""

        await update.message.reply_text(message, parse_mode="HTML")
    else:
        if user_lang == "en":
            await update.message.reply_text(
                "😴 <b>No news yet</b>\n\nBot monitors 24/7!",
                parse_mode="HTML")
        else:
            await update.message.reply_text(
                "😴 <b>Новостей пока нет</b>\n\nБот мониторит 24/7!",
                parse_mode="HTML")


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ton"""
    user_id = update.effective_user.id
    user_lang = get_user_language(user_id)

    price_data = await get_ton_price()

    if price_data:
        if user_lang == "en":
            message = f"""💰 <b>TON PRICE (Binance)</b>

💵 <b>USD:</b> ${price_data['price_usd']:.4f}
₽ <b>RUB:</b> {price_data['price_rub']:.2f}₽

{price_data['emoji']} <b>24h:</b> {price_data['change_24h']:.2f}%"""
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Update", callback_data="price_refresh")
            ], [InlineKeyboardButton("📰 News", callback_data="lastnews")]])
        else:
            message = f"""💰 <b>КУРС TON (Binance)</b>

💵 <b>USD:</b> ${price_data['price_usd']:.4f}
₽ <b>RUB:</b> {price_data['price_rub']:.2f}₽

{price_data['emoji']} <b>24ч:</b> {price_data['change_24h']:.2f}%"""
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Обновить",
                                     callback_data="price_refresh")
            ], [InlineKeyboardButton("📰 Новости", callback_data="lastnews")]])

        await update.message.reply_text(message,
                                        reply_markup=keyboard,
                                        parse_mode="HTML")
    else:
        if user_lang == "en":
            await update.message.reply_text("❌ Price error", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Ошибка получения цены",
                                            parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help"""
    user_id = update.effective_user.id
    user_lang = get_user_language(user_id)

    if user_lang == "en":
        message = """❓ <b>HELP</b>

/start - Menu
/lastnews - Latest news
/ton - TON price"""
    else:
        message = """❓ <b>СПРАВКА</b>

/start - Меню
/lastnews - Последняя новость
/ton - Курс TON"""

    await update.message.reply_text(message, parse_mode="HTML")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle buttons"""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    # Language selection
    if query.data == "set_lang_ru":
        save_user_language(user_id, "ru")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📰 Последняя новость",
                                 callback_data="lastnews")
        ], [InlineKeyboardButton("💰 Курс TON", callback_data="price")
            ], [InlineKeyboardButton("❓ Помощь", callback_data="help")]])

        message = """🚀 <b>TON NEWS BOT v4.0</b>

Все новости теперь на русском!

<b>Что я делаю:</b>
📰 Мониторю новости о TON
💰 Показываю курс в реальном времени
📊 Анализирую влияние на цену
🔔 Без дубликатов!"""

        await query.edit_message_text(message,
                                      reply_markup=keyboard,
                                      parse_mode="HTML")

    elif query.data == "set_lang_en":
        save_user_language(user_id, "en")
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📰 Latest news", callback_data="lastnews")],
             [InlineKeyboardButton("💰 TON Price", callback_data="price")],
             [InlineKeyboardButton("❓ Help", callback_data="help")]])

        message = """🚀 <b>TON NEWS BOT v4.0</b>

All news in English now!

<b>What I do:</b>
📰 Monitor TON news
💰 Show real-time price
📊 Analyze market impact
🔔 No duplicates!"""

        await query.edit_message_text(message,
                                      reply_markup=keyboard,
                                      parse_mode="HTML")

    elif query.data == "price" or query.data == "price_refresh":
        price_data = await get_ton_price()
        if price_data:
            user_lang = get_user_language(user_id)
            if user_lang == "en":
                message = f"""💰 <b>TON PRICE (Binance)</b>

💵 <b>USD:</b> ${price_data['price_usd']:.4f}
₽ <b>RUB:</b> {price_data['price_rub']:.2f}₽

{price_data['emoji']} <b>24h:</b> {price_data['change_24h']:.2f}%"""
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Update",
                                         callback_data="price_refresh")
                ], [InlineKeyboardButton("📰 News", callback_data="lastnews")]])
            else:
                message = f"""💰 <b>КУРС TON (Binance)</b>

💵 <b>USD:</b> ${price_data['price_usd']:.4f}
₽ <b>RUB:</b> {price_data['price_rub']:.2f}₽

{price_data['emoji']} <b>24ч:</b> {price_data['change_24h']:.2f}%"""
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Обновить",
                                         callback_data="price_refresh")
                ], [
                    InlineKeyboardButton("📰 Новости", callback_data="lastnews")
                ]])

            await query.edit_message_text(message,
                                          reply_markup=keyboard,
                                          parse_mode="HTML")

    elif query.data == "help":
        user_lang = get_user_language(user_id)
        if user_lang == "en":
            message = """❓ <b>HELP</b>

/start - Menu
/lastnews - Latest news
/ton - TON price"""
        else:
            message = """❓ <b>СПРАВКА</b>

/start - Меню
/lastnews - Последняя новость
/ton - Курс TON"""
        await query.edit_message_text(message, parse_mode="HTML")

    elif query.data == "lastnews":
        user_lang = get_user_language(user_id)
        if news_manager.last_news:
            news = news_manager.last_news
            analysis = await analyze_news_with_ai(news["title"],
                                                  news["content"], user_lang)

            if user_lang == "en":
                trans_title = await translate_text(news["title"], "en")
                trans_content = await translate_text(news["content"], "en")
                message = f"""📰 <b>TON NEWS</b>

<b>{trans_title}</b>

{trans_content}

<b>Analysis:</b>
{analysis}

🔗 <a href='{news["link"]}'>Read full article</a>"""
            else:
                message = f"""📰 <b>НОВОСТЬ О TON</b>

<b>{news['title']}</b>

{news['content']}

<b>📊 АНАЛИЗ:</b>
{analysis}

🔗 <a href='{news["link"]}'>Читать полностью</a>"""

            await query.edit_message_text(message, parse_mode="HTML")

    elif query.data == "lang_en":
        save_user_language(user_id, "en")
        if news_manager.last_news:
            news = news_manager.last_news
            analysis = await analyze_news_with_ai(news["title"],
                                                  news["content"], "en")
            trans_title = await translate_text(news["title"], "en")
            trans_content = await translate_text(news["content"], "en")

            message = f"""📰 <b>TON NEWS</b>

<b>{trans_title}</b>

{trans_content}

<b>Analysis:</b>
{analysis}

🔗 <a href='{news["link"]}'>Read full article</a>"""

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
            ]])

            await query.edit_message_text(message,
                                          parse_mode="HTML",
                                          reply_markup=keyboard)
        await query.answer("Language changed to English")

    elif query.data == "lang_ru":
        save_user_language(user_id, "ru")
        if news_manager.last_news:
            news = news_manager.last_news
            analysis = await analyze_news_with_ai(news["title"],
                                                  news["content"], "ru")

            message = f"""📰 <b>НОВОСТЬ О TON</b>

<b>{news['title']}</b>

{news['content']}

<b>📊 АНАЛИЗ:</b>
{analysis}

🔗 <a href='{news["link"]}'>Читать полностью</a>"""

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
            ]])

            await query.edit_message_text(message,
                                          parse_mode="HTML",
                                          reply_markup=keyboard)
        await query.answer("Язык изменен на русский")
    
    elif query.data == "price_impact":
        user_lang = get_user_language(user_id)
        if news_manager.last_news:
            news = news_manager.last_news
            price_analysis = await analyze_ton_price_impact(news["title"], news["content"], user_lang)
            
            if user_lang == "en":
                message = f"""💹 <b>TON PRICE IMPACT ANALYSIS</b>

📈 <b>News Impact:</b>
{price_analysis}

<b>How this affects TON:</b>
This analysis predicts the potential short-term price movement based on the news sentiment and market relevance."""
            else:
                message = f"""💹 <b>АНАЛИЗ ВЛИЯНИЯ НА ЦЕНУ TON</b>

📈 <b>Влияние новости:</b>
{price_analysis}

<b>Как это влияет на TON:</b>
Этот анализ предсказывает возможное краткосрочное движение цены на основе тональности новости и её значимости для рынка."""
            
            await query.edit_message_text(message, parse_mode="HTML")
            await query.answer("Analysis ready ✅" if user_lang == "en" else "Анализ готов ✅")


async def monitor_news(app):
    """Monitor news background task"""
    print("\n" + "=" * 60)
    print("🤖 TON NEWS BOT v4.0")
    print("=" * 60)
    print("✅ Статус: Активен")
    print("🔄 Мониторинг: 2 минуты")
    print("📡 RSS-ленты: 4")
    print("💰 Курс: каждые 5 минут")
    print("🌍 Мультиязычность: ВКЛ (RU/EN)")
    print("🛡️  Защита от дубликатов: ВКЛ")
    print("=" * 60 + "\n")

    check_count = 0
    while True:
        try:
            check_count += 1
            current_time = datetime.now().strftime('%H:%M:%S')
            print(f"[{current_time}] Проверка #{check_count}...")

            news_items = await fetch_rss_news()

            if news_items:
                print(f"✅ Найдено {len(news_items)} уникальных новостей о TON")
                for news in news_items:
                    # Send to main user in their language
                    user_lang = get_user_language(TELEGRAM_CHAT_ID)
                    success = await send_news_alert(app, TELEGRAM_CHAT_ID,
                                                    news, user_lang)
                    if success:
                        await asyncio.sleep(2)
            else:
                print("ℹ️  Новых уникальных новостей о TON не найдено")

            await asyncio.sleep(120)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            await asyncio.sleep(60)


async def set_commands(app):
    """Register commands"""
    commands = [
        BotCommand("start", "Menu"),
        BotCommand("lastnews", "Latest news"),
        BotCommand("ton", "TON price"),
        BotCommand("help", "Help"),
    ]
    await app.bot.set_my_commands(commands)
    print("✅ Команды зарегистрированы")


async def main():
    """Main entry point"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return

    print("✅ Инициализация...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("lastnews", lastnews_command))
    app.add_handler(CommandHandler(["ton", "price"], price_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("✅ Обработчики загружены")

    await set_commands(app)

    async with app:
        await app.start()

        monitor_task = asyncio.create_task(monitor_news(app))

        print("🚀 Бот готов!\n")

        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            print("\n✋ Остановка...")
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
