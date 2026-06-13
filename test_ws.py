import websocket
import json
import time

def on_message(ws, message):
    data = json.loads(message)
    print("Received Event:")
    print(json.dumps(data, indent=2))
    # Close after printing a few events
    if not hasattr(on_message, "count"):
        on_message.count = 0
    on_message.count += 1
    if on_message.count >= 5:
        ws.close()

def on_error(ws, error):
    print("Error:", error)

def on_close(ws, close_status_code, close_msg):
    print("### closed ###")

def on_open(ws):
    print("Connected. Subscribing...")
    ws.send(json.dumps({"method": "subscribeNewToken"}))
    ws.send(json.dumps({"method": "subscribeTrade", "keys": []}))

if __name__ == "__main__":
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp("wss://pumpportal.fun/api/data",
                              on_open=on_open,
                              on_message=on_message,
                              on_error=on_error,
                              on_close=on_close)
    ws.run_forever()
