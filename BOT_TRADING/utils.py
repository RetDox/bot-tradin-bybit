logs = []

def log(msg):
    print(msg)
    logs.append(msg)
    if len(logs) > 100:
        logs.pop(0)