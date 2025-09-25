#!/bin/bash
pip uninstall -y python-telegram-bot
pip install --no-cache-dir --upgrade --force-reinstall python-telegram-bot==20.7
pip install --no-cache-dir aiohttp python-dotenv nest_asyncio
python main.py
