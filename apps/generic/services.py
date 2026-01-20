# generic/services.py
import os
import time
import threading
import requests
from dotenv import load_dotenv

load_dotenv()

discord_bot_token = os.getenv("discord_bot_token")
indexer_channel_id = os.getenv("INDEXER_DISCORD_CHANNEL_ID")
engineer_webhook = os.getenv("ENGINEER_WEBHOOK", "http://10.241.68.122:8765")
engineer_secret = os.getenv("ENGINEER_SECRET", "eng_7Kx9mPqR3vNcYhW2jL5sT8bF4aD6eG1i")

# --- Deferred Alerting System ---
ALERT_GRACE_PERIOD = int(os.getenv("ALERT_GRACE_PERIOD", "60"))
_pending_alerts = {}
_pending_alerts_lock = threading.Lock()
_last_heartbeat = {}


def _get_alert_key(msg, network, app):
    normalized = msg[:80].strip()
    return f"{network}:{app}:{normalized}"


def schedule_alert(msg, network="N/A", app="N/A"):
    key = _get_alert_key(msg, network, app)
    now = time.time()
    with _pending_alerts_lock:
        if key in _pending_alerts:
            _pending_alerts[key]["count"] += 1
            cnt = _pending_alerts[key]["count"]
            print(f"[ALERT] Error recurring (count={cnt}): {msg[:60]}...")
        else:
            _pending_alerts[key] = {
                "msg": msg, "network": network, "app": app,
                "scheduled_time": now + ALERT_GRACE_PERIOD,
                "first_seen": now, "count": 1
            }
            print(f"[ALERT] Scheduled alert (grace={ALERT_GRACE_PERIOD}s): {msg[:60]}...")


def record_heartbeat(network, app):
    context_key = f"{network}:{app}"
    now = time.time()
    _last_heartbeat[context_key] = now
    with _pending_alerts_lock:
        keys_to_remove = []
        for key, data in _pending_alerts.items():
            if key.startswith(context_key + ":") and data["first_seen"] < now:
                print(f"[ALERT] Cancelled (recovered): {data['msg'][:60]}...")
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del _pending_alerts[key]


def process_pending_alerts():
    now = time.time()
    alerts_sent = 0
    with _pending_alerts_lock:
        keys_to_remove = []
        for key, data in _pending_alerts.items():
            if now >= data["scheduled_time"]:
                print(f"[ALERT] Grace period expired, sending: {data['msg'][:60]}...")
                _send_indexer_alert_immediate(
                    data["msg"], data["network"], data["app"],
                    occurrence_count=data["count"]
                )
                keys_to_remove.append(key)
                alerts_sent += 1
        for key in keys_to_remove:
            del _pending_alerts[key]
    return alerts_sent


def get_pending_alert_count():
    with _pending_alerts_lock:
        return len(_pending_alerts)


def send_discord_message(msg, channel_id):
    if not discord_bot_token:
        print("ERROR: discord_bot_token is not configured.")
        return
    url = f"https://discordapp.com/api/channels/{channel_id}/messages"
    headers = {"Authorization": "Bot " + discord_bot_token}
    body = {"content": msg}
    try:
        response = requests.post(url, headers=headers, data=body)
        response.raise_for_status()
        print(f"Successfully sent Discord message to channel {channel_id}.")
        return {"data": f"Status code {response.status_code}"}
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to send Discord message: {e}")
        return {"error": str(e)}


def send_engineer_alert(msg, project="etherlink-indexer"):
    if not engineer_webhook:
        print("WARNING: ENGINEER_WEBHOOK not configured.")
        return
    try:
        headers = {"Content-Type": "application/json", "X-Engineer-Secret": engineer_secret}
        payload = {"alert": msg, "project": project}
        response = requests.post(engineer_webhook, json=payload, headers=headers, timeout=5)
        if response.status_code == 200:
            print(f"Successfully sent alert to Engineer: {response.json().get('status', 'ok')}")
        else:
            print(f"Engineer webhook returned {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"WARNING: Failed to send Engineer alert: {e}")


def _send_indexer_alert_immediate(msg, network="N/A", app="N/A", occurrence_count=1):
    if not indexer_channel_id:
        print("WARNING: INDEXER_DISCORD_CHANNEL_ID not set.")
        return
    count_note = f" (occurred {occurrence_count}x)" if occurrence_count > 1 else ""
    formatted_msg = "\n".join([
        "\U0001F6A8 **CRITICAL INDEXER ERROR** \U0001F6A8",
        "---",
        f"**Network:** `{network.upper()}`",
        f"**App:** `{app.upper()}`",
        "---",
        f"**Disruption Detected{count_note}:**",
        "```",
        msg,
        "```",
        "---",
        "Please investigate the indexer service immediately."
    ])
    result = send_discord_message(formatted_msg, indexer_channel_id)
    engineer_msg = f"[{network.upper()}] [{app.upper()}] {msg}"
    send_engineer_alert(engineer_msg, project="etherlink-indexer")
    return result


def send_indexer_alert(msg, network="N/A", app="N/A", immediate=False):
    """Send an alert. By default deferred with grace period. Set immediate=True for critical errors."""
    if immediate:
        _send_indexer_alert_immediate(msg, network, app)
    else:
        schedule_alert(msg, network, app)
