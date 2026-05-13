import datetime

# orari news (semplice demo)
# puoi poi collegare API vere
NEWS_TIMES = [
    (14, 30),  # esempio news USA
    (16, 0),
]

def is_news_time():

    now = datetime.datetime.now()

    for h, m in NEWS_TIMES:
        news_time = now.replace(hour=h, minute=m, second=0)

        diff = abs((now - news_time).total_seconds())

        # blocca 10 minuti prima/dopo
        if diff < 600:
            return True

    return False