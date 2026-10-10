"""Upcoming Indian occasions (fixed-date national days and anniversaries). Lunar festivals move every
year, so the topic picker is also asked to add any that fall in the window from its own knowledge."""
from datetime import date, timedelta

FIXED = {  # (month, day): occasion
    (1, 12): "National Youth Day (Swami Vivekananda's birth anniversary)",
    (1, 23): "Netaji Subhas Chandra Bose's birth anniversary (Parakram Diwas)",
    (1, 26): "Republic Day of India",
    (1, 30): "Martyrs' Day (Mahatma Gandhi's death anniversary)",
    (2, 28): "National Science Day (C. V. Raman effect)",
    (3, 23): "Shaheed Diwas (Bhagat Singh, Rajguru, Sukhdev)",
    (4, 13): "Jallianwala Bagh massacre anniversary / Baisakhi",
    (4, 14): "Ambedkar Jayanti",
    (5, 10): "Anniversary of the 1857 revolt at Meerut",
    (6, 21): "International Yoga Day",
    (7, 26): "Kargil Vijay Diwas",
    (8, 6): "Hiroshima bombing anniversary",
    (8, 9): "Quit India Movement anniversary",
    (8, 15): "Independence Day of India",
    (8, 29): "National Sports Day (Major Dhyan Chand)",
    (9, 5): "Teachers' Day (Dr. Sarvepalli Radhakrishnan)",
    (10, 2): "Gandhi Jayanti / Lal Bahadur Shastri Jayanti",
    (10, 31): "Sardar Vallabhbhai Patel's birth anniversary / Indira Gandhi's death anniversary",
    (11, 14): "Children's Day (Jawaharlal Nehru)",
    (11, 26): "Constitution Day / 26/11 Mumbai attacks anniversary",
    (12, 16): "Vijay Diwas (1971 war)",
    (12, 25): "Christmas",
}


def upcoming(days=12, today=None):
    today = today or date.today()
    out = []
    for k in range(1, days + 1):
        d = today + timedelta(days=k)
        if (d.month, d.day) in FIXED:
            out.append(f"{d:%d %b} (in {k} days): {FIXED[(d.month, d.day)]}")
    return out
