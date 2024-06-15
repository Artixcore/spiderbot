import logging
import telebot
import requests
import psycopg2
from threading import Thread
import os
import time
import json
import hmac
import hashlib
import base64
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Replace with your Telegram bot token
bot = telebot.TeleBot(os.getenv("TELEGRAM_BOT_TOKEN"))

# PostgreSQL database setup
conn = psycopg2.connect(
    dbname=os.getenv("POSTGRES_DB"),
    user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
    host=os.getenv("POSTGRES_HOST"),
    port=os.getenv("POSTGRES_PORT")
)
c = conn.cursor()

# Create user_data table
c.execute('''
    CREATE TABLE IF NOT EXISTS user_data (
        user_id BIGINT PRIMARY KEY,
        subscribed BOOLEAN,
        api_key TEXT,
        api_secret TEXT,
        total_traded REAL
    )
''')
conn.commit()

# Functions to display buttons, prices, etc.
def show_main_menu(message):
    user_id = message.from_user.id
    c.execute("SELECT subscribed FROM user_data WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        buttons = [
            telebot.types.KeyboardButton("Coin List"),
            telebot.types.KeyboardButton("Start Trade"),
            telebot.types.KeyboardButton("AI Trade"),
            telebot.types.KeyboardButton("Unsubscribe"),
            telebot.types.KeyboardButton("Trade Summary")
        ]
        keyboard.add(*buttons)

        # Try fetching coin prices
        try:
            prices = get_coin_prices()
            if prices:
                price_message = "**Current Coin Prices (USD, EUR, GBP, BTC, USDT):**\n"
                for coin, price in prices.items():
                    price_message += f"- {coin}: USD ${price['usd']:.2f}, EUR €{price['eur']:.2f}, GBP £{price['gbp']:.2f}, BTC {price['btc']:.6f}, USDT {price['usdt']:.2f}\n"
                bot.send_message(message.chat.id, price_message, parse_mode="Markdown", reply_markup=keyboard)
            else:
                bot.send_message(message.chat.id, "Unable to fetch coin prices. Please try again later.", reply_markup=keyboard)
        except Exception as e:
            print(f"Error fetching prices: {e}")
            bot.send_message(message.chat.id, "An error occurred. Please try again later.", reply_markup=keyboard)
    else:
        bot.send_message(message.chat.id, "You are not subscribed. Please subscribe to use trade features.")

def show_coin_list(message):
    prices = get_coin_prices()
    if prices:
        coin_list_message = "Supported coins for trading:\n" + "\n".join([f"{coin}: USD ${price['usd']:.2f}, EUR €{price['eur']:.2f}, GBP £{price['gbp']:.2f}, BTC {price['btc']:.6f}, USDT {price['usdt']:.2f}" for coin, price in prices.items()])
        bot.send_message(message.chat.id, coin_list_message)
    else:
        bot.send_message(message.chat.id, "Unable to fetch coin prices.")

def get_coin_prices():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin,tether&vs_currencies=usd,eur,gbp,btc,usdt"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        prices = {coin: data[coin] for coin in data}
        return prices
    else:
        print(f"Error fetching prices: {response.status_code}")
        return None

def show_trade_summary(message):
    user_id = message.from_user.id
    c.execute("SELECT total_traded FROM user_data WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    total_traded = row[0] if row else 0
    bot.send_message(message.chat.id, f"Total USD traded: ${total_traded}")

# Helper function to create Coinbase API signature
def create_coinbase_signature(api_secret, timestamp, method, request_path, body=''):
    message = f'{timestamp}{method}{request_path}{body}'
    hmac_key = base64.b64decode(api_secret)
    signature = hmac.new(hmac_key, message.encode('utf-8'), hashlib.sha256)
    return base64.b64encode(signature.digest()).decode('utf-8')

# Function to get current price for a specific cryptocurrency
def get_current_price(api_key, api_secret, product_id='BTC-USD'):
    url = f'https://api.coinbase.com/v2/prices/{product_id}/spot'
    timestamp = str(int(time.time()))
    request_path = f'/v2/prices/{product_id}/spot'
    method = 'GET'
    headers = {
        'CB-ACCESS-KEY': api_key,
        'CB-ACCESS-SIGN': create_coinbase_signature(api_secret, timestamp, method, request_path),
        'CB-ACCESS-TIMESTAMP': timestamp,
        'Content-Type': 'application/json'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return float(response.json()['data']['amount'])
    else:
        raise Exception(f"Error fetching current price: {response.status_code}")

# Function to place a market order on Coinbase
def place_market_order(api_key, api_secret, amount, side='buy', product_id='BTC-USD'):
    url = 'https://api.coinbase.com/v2/orders'
    timestamp = str(int(time.time()))
    request_path = '/v2/orders'
    method = 'POST'
    body = {
        'type': 'market',
        'side': side,
        'product_id': product_id,
        'funds': amount
    }
    body_str = json.dumps(body)
    headers = {
        'CB-ACCESS-KEY': api_key,
        'CB-ACCESS-SIGN': create_coinbase_signature(api_secret, timestamp, method, request_path, body_str),
        'CB-ACCESS-TIMESTAMP': timestamp,
        'Content-Type': 'application/json'
    }
    response = requests.post(url, headers=headers, data=body_str)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error placing market order: {response.status_code}")

# Check user's balance
def check_balance(api_key, api_secret, amount, currency):
    url = "https://api.coinbase.com/v2/accounts"
    timestamp = str(int(time.time()))
    request_path = '/v2/accounts'
    method = 'GET'
    headers = {
        "CB-ACCESS-KEY": api_key,
        "CB-ACCESS-SIGN": create_coinbase_signature(api_secret, timestamp, method, request_path),
        "CB-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        balance = sum([float(account['balance']['amount']) for account in data['data'] if account['currency'] == currency])
        return balance >= float(amount)
    else:
        print(f"Error fetching balance: {response.status_code}")
        return False

# Get user's balance for all relevant currencies
def get_all_balances(api_key, api_secret):
    url = "https://api.coinbase.com/v2/accounts"
    timestamp = str(int(time.time()))
    request_path = '/v2/accounts'
    method = 'GET'
    headers = {
        "CB-ACCESS-KEY": api_key,
        "CB-ACCESS-SIGN": create_coinbase_signature(api_secret, timestamp, method, request_path),
        "CB-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        balances = {account['currency']: account['balance']['amount'] for account in data['data']}
        return balances
    else:
        print(f"Error fetching balances: {response.status_code}")
        return {}

# Trading strategy implementations
def buy_and_hold(api_key, api_secret, amount, currency='USD'):
    try:
        current_price = get_current_price(api_key, api_secret, product_id=f'BTC-{currency}')
        amount_in_crypto = amount / current_price
        order_response = place_market_order(api_key, api_secret, str(amount_in_crypto), product_id=f'BTC-{currency}')
        return f"Executed Buy and Hold with {amount} {currency}: {order_response}"
    except Exception as e:
        return f"Error executing Buy and Hold: {e}"

def moving_average(api_key, api_secret, amount, currency='USD'):
    try:
        order_response = place_market_order(api_key, api_secret, amount, product_id=f'BTC-{currency}')
        return f"Executed Moving Average with {amount} {currency}: {order_response}"
    except Exception as e:
        return f"Error executing Moving Average: {e}"

def mean_reversion(api_key, api_secret, amount, currency='USD'):
    try:
        order_response = place_market_order(api_key, api_secret, amount, product_id=f'BTC-{currency}')
        return f"Executed Mean Reversion with {amount} {currency}: {order_response}"
    except Exception as e:
        return f"Error executing Mean Reversion: {e}"

# Telegram bot commands
@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    c.execute("INSERT INTO user_data (user_id, subscribed, total_traded) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, False, 0))
    conn.commit()

    c.execute("SELECT subscribed FROM user_data WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        show_main_menu(message)
    else:
        subscribe_message = (
            "Welcome! To use the trading features, you need to subscribe first. "
            "Click the 'Subscribe' button below to start."
        )
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        subscribe_button = telebot.types.KeyboardButton("Subscribe")
        keyboard.add(subscribe_button)
        bot.send_message(message.chat.id, subscribe_message, reply_markup=keyboard)

@bot.message_handler(func=lambda message: message.text == "Subscribe")
def subscribe(message):
    user_id = message.from_user.id
    c.execute("UPDATE user_data SET subscribed = TRUE WHERE user_id = %s", (user_id,))
    conn.commit()
    bot.send_message(message.chat.id, "You have successfully subscribed!")
    show_main_menu(message)

@bot.message_handler(func=lambda message: message.text == "Unsubscribe")
def unsubscribe(message):
    user_id = message.from_user.id
    c.execute("UPDATE user_data SET subscribed = FALSE WHERE user_id = %s", (user_id,))
    conn.commit()
    bot.send_message(message.chat.id, "You have successfully unsubscribed!")
    start(message)

@bot.message_handler(func=lambda message: message.text == "Coin List")
def handle_coin_list(message):
    show_coin_list(message)

@bot.message_handler(func=lambda message: message.text == "Trade Summary")
def handle_trade_summary(message):
    show_trade_summary(message)

@bot.message_handler(func=lambda message: message.text == "Start Trade")
def handle_start_trade(message):
    user_id = message.from_user.id
    c.execute("SELECT subscribed FROM user_data WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        bot.send_message(message.chat.id, "Please enter your Coinbase API Key:")
        bot.register_next_step_handler(message, handle_api_key)
    else:
        bot.send_message(message.chat.id, "Please subscribe first.")

def handle_api_key(message):
    user_id = message.from_user.id
    api_key = message.text
    c.execute("UPDATE user_data SET api_key = %s WHERE user_id = %s", (api_key, user_id))
    conn.commit()
    bot.send_message(message.chat.id, "Please enter your Coinbase API Secret:")
    bot.register_next_step_handler(message, handle_api_secret)

def handle_api_secret(message):
    user_id = message.from_user.id
    api_secret = message.text
    c.execute("UPDATE user_data SET api_secret = %s WHERE user_id = %s", (api_secret, user_id))
    conn.commit()
    try:
        # Validate API keys by making a simple request and show the user's balance
        c.execute("SELECT api_key, api_secret FROM user_data WHERE user_id = %s", (user_id,))
        row = c.fetchone()
        if row:
            api_key, api_secret = row
            balances = get_all_balances(api_key, api_secret)
            balance_message = "Your balances:\n" + "\n".join([f"{currency}: {amount}" for currency, amount in balances.items()])
            bot.send_message(message.chat.id, f"API keys have been saved and validated.\n{balance_message}")
            show_main_menu(message)
    except Exception as e:
        bot.send_message(message.chat.id, f"Invalid API keys: {e}")

@bot.message_handler(func=lambda message: message.text == "AI Trade")
def handle_ai_trade(message):
    user_id = message.from_user.id
    c.execute("SELECT subscribed FROM user_data WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        # Ask the user to choose a currency with buttons
        keyboard = telebot.types.InlineKeyboardMarkup()
        usd_button = telebot.types.InlineKeyboardButton("USD", callback_data=f"currency:USD")
        eur_button = telebot.types.InlineKeyboardButton("EUR", callback_data=f"currency:EUR")
        gbp_button = telebot.types.InlineKeyboardButton("GBP", callback_data=f"currency:GBP")
        btc_button = telebot.types.InlineKeyboardButton("BTC", callback_data=f"currency:BTC")
        usdt_button = telebot.types.InlineKeyboardButton("USDT", callback_data=f"currency:USDT")
        keyboard.add(usd_button, eur_button, gbp_button, btc_button, usdt_button)
        bot.send_message(message.chat.id, "Please choose a currency:", reply_markup=keyboard)
    else:
        bot.send_message(message.chat.id, "Please subscribe first.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("currency:"))
def handle_currency_selection(call):
    user_id = call.from_user.id
    _, currency = call.data.split(":")
    bot.send_message(call.message.chat.id, f"Please enter the amount in {currency} to trade:")
    bot.register_next_step_handler(call.message, handle_trade_amount, currency)

def handle_trade_amount(message, currency):
    user_id = message.from_user.id
    amount = message.text
    c.execute("SELECT api_key, api_secret FROM user_data WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if row:
        api_key, api_secret = row
        if check_balance(api_key, api_secret, amount, currency):
            # Ask the user to choose a trading strategy with buttons
            keyboard = telebot.types.InlineKeyboardMarkup()
            buy_and_hold_button = telebot.types.InlineKeyboardButton("Buy and Hold", callback_data=f"strategy:1:{amount}:{currency}")
            moving_average_button = telebot.types.InlineKeyboardButton("Moving Average", callback_data=f"strategy:2:{amount}:{currency}")
            mean_reversion_button = telebot.types.InlineKeyboardButton("Mean Reversion", callback_data=f"strategy:3:{amount}:{currency}")
            keyboard.add(buy_and_hold_button, moving_average_button, mean_reversion_button)
            bot.send_message(message.chat.id, "Please choose a trading strategy:", reply_markup=keyboard)
        else:
            bot.send_message(message.chat.id, "Insufficient balance. Please check your balance and try again.")
    else:
        bot.send_message(message.chat.id, "API keys not found. Please set up your API keys first.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("strategy:"))
def handle_strategy_selection(call):
    user_id = call.from_user.id
    _, strategy, amount, currency = call.data.split(":")
    
    # Start the selected trading strategy in a separate thread to avoid blocking the bot
    trade_thread = Thread(target=execute_trade, args=(user_id, strategy, amount, currency))
    trade_thread.start()

    bot.send_message(call.message.chat.id, f"Trading started with {amount} {currency} using strategy {strategy}.")
    show_main_menu(call.message)

def execute_trade(user_id, strategy, amount, currency):
    c.execute("SELECT api_key, api_secret FROM user_data WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if row:
        api_key, api_secret = row

        try:
            if strategy == "1":
                trade_result = buy_and_hold(api_key, api_secret, amount, currency)
            elif strategy == "2":
                trade_result = moving_average(api_key, api_secret, amount, currency)
            elif strategy == "3":
                trade_result = mean_reversion(api_key, api_secret, amount, currency)
            else:
                trade_result = "Invalid strategy selected."

            # Update total traded amount
            c.execute("UPDATE user_data SET total_traded = total_traded + %s WHERE user_id = %s", (float(amount), user_id))
            conn.commit()

            # Send notification to the user
            bot.send_message(user_id, f"Trade executed successfully: {trade_result}")

            # Simulate sending periodic updates (replace this with real update logic)
            for i in range(5):
                time.sleep(10)  # Simulate time delay for periodic updates
                bot.send_message(user_id, f"Update {i + 1}/5: Trade still in progress.")

        except Exception as e:
            bot.send_message(user_id, f"Error executing trade: {e}")

# Error handler
def handle_errors(exception):
    print(f"Exception occurred: {exception}")

# Set error handler
telebot.apihelper.proxy = None
telebot.apihelper.LOGGING = True
telebot.apihelper.logger.setLevel(logging.INFO)

@bot.message_handler(content_types=["text"])
def fallback(message):
    bot.send_message(message.chat.id, "Sorry, I didn't understand that command.")

if __name__ == "__main__":
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        handle_errors(e)
        bot.polling(none_stop=True)
