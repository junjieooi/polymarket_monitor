#!/usr/bin/env python3
"""
Simple script to fetch your Telegram Chat ID.
Instructions:
1. Set TELEGRAM_BOT_TOKEN in your .env file or as environment variable
2. Send a message to your bot on Telegram (any message like "hello")
3. Run this script: python fetch_chatID.py
4. Copy the Chat ID it displays and add to your .env file
"""

import os
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get bot token from environment variable
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def get_chat_id():
    """Fetch and display Chat ID from Telegram bot."""

    if not BOT_TOKEN:
        print("=" * 70)
        print("ERROR: TELEGRAM_BOT_TOKEN not set!")
        print("=" * 70)
        print("\nPlease set your bot token in one of these ways:")
        print("  1. Create a .env file with: TELEGRAM_BOT_TOKEN=your_token_here")
        print("  2. Set environment variable: export TELEGRAM_BOT_TOKEN=your_token_here")
        return

    # API endpoint to get updates
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

    print("=" * 70)
    print("🔍 FETCHING TELEGRAM CHAT ID")
    print("=" * 70)
    print()

    try:
        # Make request to Telegram API
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        data = response.json()

        # Check if we got any updates
        if not data.get('ok'):
            print("❌ Error: Bot token might be invalid")
            print(f"Response: {data}")
            return

        updates = data.get('result', [])

        if not updates:
            print("⚠️  No messages found!")
            print()
            print("Please do the following:")
            print("1. Open Telegram and find your bot")
            print("2. Send any message to your bot (like 'hello')")
            print("3. Run this script again")
            return

        # Display all found chat IDs
        print("✅ Found the following chats:\n")

        chat_ids = set()
        for update in updates:
            message = update.get('message', {})
            chat = message.get('chat', {})

            chat_id = chat.get('id')
            chat_type = chat.get('type', 'unknown')
            first_name = chat.get('first_name', 'N/A')
            username = chat.get('username', 'N/A')
            title = chat.get('title', 'N/A')

            if chat_id:
                chat_ids.add(chat_id)
                print("-" * 70)
                print(f"Chat ID:    {chat_id}")
                print(f"Type:       {chat_type}")
                if chat_type == 'private':
                    print(f"Name:       {first_name}")
                    print(f"Username:   @{username}")
                else:
                    print(f"Group Name: {title}")
                print()

        if chat_ids:
            print("=" * 70)
            print("📋 COPY THIS CHAT ID TO YOUR SCRIPT:")
            print("=" * 70)
            for chat_id in chat_ids:
                print(f"\nTELEGRAM_CHAT_ID = \"{chat_id}\"")
            print()

    except requests.exceptions.RequestException as e:
        print(f"❌ Network error: {e}")
        print("Please check your internet connection and bot token.")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")


if __name__ == "__main__":
    get_chat_id()