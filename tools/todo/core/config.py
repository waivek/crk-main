"""Global game-reset settings."""

from datetime import time
from zoneinfo import ZoneInfo

# Cookie Run: Kingdom resets at midnight KST. "Game days" (e.g. Fri/Sat/Sun tasks) are KST days.
RESET_TZ = ZoneInfo("Asia/Seoul")
DAILY_RESET = time(0, 0)
WEEKLY_RESET_DAY = 0  # Monday (datetime.weekday() numbering)
HISTORY_LIMIT = 100
