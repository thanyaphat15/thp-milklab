"""MilkLab Agent Harness (S2).

Usage:
    python agent_harness.py --cmd "บันทึกขายนมหมี 2 ขวด ขวดละ 65"

รับคำสั่งภาษาไทย ส่งให้ Gemini พร้อม tool schema parse response เป็น tool call
เรียก tool จริง print trace log และบันทึกลง agent_trace.log
"""

import argparse
import json
import os
import re
import subprocess
import sys

from datetime import datetime
from dotenv import load_dotenv
from google import genai
import gspread
from google.oauth2.service_account import Credentials
import requests

LOG_FILE = "agent_trace.log"

TOOL_SCHEMA = [
    {
        "type": "function",
        "name": "log_sale",
        "description": "บันทึกการขายลง Google Sheets และส่ง notification",
        "parameters": {
            "type": "object",
            "properties": {
                "menu": {"type": "string", "description": "ชื่อเมนู"},
                "qty": {"type": "integer", "description": "จำนวนที่ขาย"},
                "price": {"type": "number", "description": "ราคาต่อหน่วย"},
            },
            "required": ["menu", "qty", "price"],
        },
    },
    {
        "type": "function",
        "name": "query_sales",
        "description": "ดูยอดขายของวันที่ระบุ",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "วันที่ format YYYY-MM-DD"},
            },
            "required": ["date"],
        },
    },
    {
        "type": "function",
        "name": "send_alert",
        "description": "ส่ง message แจ้งเตือนผ่าน Bot",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    },
]


def log_trace(event_type: str, message: str):
    """บันทึกเหตุการณ์ลงไฟล์ agent_trace.log ในรูปแบบ timestamp | event_type | message."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"{timestamp} | {event_type} | {message}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")


def parse_command(cmd: str, api_key: str | None = None) -> dict:
    """ส่ง cmd ไป Gemini พร้อม TOOL_SCHEMA แล้วดึง tool call ใน response."""
    key = api_key or os.environ.get(
        "GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GOOGLE_API_KEY or GEMINI_API_KEY not set in environment or argument")

    client = genai.Client(api_key=key)

    # 1. ดึงวันที่ปัจจุบันของเครื่อง
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 2. ใส่ context วันปัจจุบันลงไปใน System Instruction
    system_instruction = (
        f"Today's date is {today_str}. "
        "Always parse the user intent into one of the provided function tools verbatim. "
        "Do NOT refuse or decline the input even if the values are negative, zero, or invalid. "
        "Always output the function call."
    )

    response = client.interactions.create(
        model="gemini-2.5-flash",
        input=f"{system_instruction}\nUser command: {cmd}",
        tools=TOOL_SCHEMA,
    )

    # ... (โค้ดดึง steps / fallback_text ด้านล่างเหมือนเดิม) ...

    steps = getattr(response, "steps", None) or []
    for step in steps:
        if getattr(step, "type", None) == "function_call":
            name = getattr(step, "name", None)
            args = getattr(step, "arguments", None)
            if name and isinstance(args, dict):
                return {"tool": name, "args": args}

    fallback_text = getattr(response, "output_text", None)
    if fallback_text:
        match = re.search(
            r'{\s*"tool"\s*:\s*"(?P<tool>[^"]+)"\s*,\s*"args"\s*:\s*(?P<args>\{.*\})\s*}',
            fallback_text,
            re.S,
        )
        if match:
            return {
                "tool": match.group("tool"),
                "args": json.loads(match.group("args")),
            }

    raise RuntimeError("ไม่สามารถ parse คำสั่งเป็น tool call ได้")


def _load_sheets_worksheet():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not creds_json or not spreadsheet_id:
        raise RuntimeError(
            "GOOGLE_SHEETS_CREDENTIALS or SPREADSHEET_ID not set in environment"
        )

    info = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(spreadsheet_id).sheet1


def _send_notification(message: str) -> str:
    sent_channels = []
    errors = []

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat_id:
        tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
        payload = {"chat_id": tg_chat_id, "text": message}
        try:
            requests.post(tg_url, json=payload, timeout=10)
            sent_channels.append("Telegram")
        except Exception as err:
            errors.append(f"Telegram: {err}")

    line_token = os.environ.get("LINE_CHANNEL_TOKEN")
    if line_token:
        line_url = "https://notify-api.line.me/api/notify"
        headers = {"Authorization": f"Bearer {line_token}"}
        payload = {"message": message}
        try:
            requests.post(line_url, headers=headers, data=payload, timeout=10)
            sent_channels.append("LINE")
        except Exception as err:
            errors.append(f"LINE: {err}")

    if not sent_channels and not errors:
        return "No notification channel configured"

    result = []
    if sent_channels:
        result.append("Sent notification via " + ", ".join(sent_channels))
    if errors:
        result.append("Errors: " + "; ".join(errors))
    return ". ".join(result)


def _query_sales(date: str) -> str:
    # ถ้าส่งคำว่า 'today' หรือค่าว่างมา ให้ใช้ YYYY-MM-DD ของวันปัจจุบัน
    if not date or str(date).lower() == "today":
        date = datetime.now().strftime("%Y-%m-%d")

    sheet = _load_sheets_worksheet()
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return f"No sales data found for {date}"

    total_qty = 0
    total_amount = 0.0
    records = 0
    for row in rows[1:]:
        if len(row) < 5:
            continue
        timestamp, _, qty_str, _, total_str = row[:5]
        if not timestamp.startswith(date):
            continue
        try:
            qty = int(qty_str)
            total = float(total_str)
        except ValueError:
            continue
        total_qty += qty
        total_amount += total
        records += 1

    if records == 0:
        return f"No sales found for {date}"

    return (
        f"ยอดขายวันที่ {date}: {records} รายการ, จำนวน {total_qty}, ยอดรวม {total_amount:.2f} บาท"
    )


def _log_sale(menu: str, qty: int, price: float) -> str:
    sheet = _load_sheets_worksheet()
    next_row = len(sheet.get_all_values()) + 1
    sales_logger_path = os.path.join(
        os.path.dirname(__file__), "sales_logger.py")
    result = subprocess.run(
        [
            sys.executable,
            sales_logger_path,
            "--menu",
            menu,
            "--qty",
            str(qty),
            "--price",
            str(price),
        ],
        text=True,
        capture_output=True,
        env=os.environ,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sales_logger failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    output = result.stdout.strip()
    return f"row appended at A{next_row}. {output}"


def _format_tool_args(args: dict) -> str:
    formatted = []
    for key, value in args.items():
        if isinstance(value, bool):
            formatted.append(f"{key}: {str(value).lower()}")
        else:
            formatted.append(f"{key}: {value}")
    return "{" + ", ".join(formatted) + "}"


def dispatch_tool(tool_call: dict) -> str:
    """เรียก tool ตาม tool_call["tool"] พร้อม guardrails validation."""
    tool = tool_call.get("tool")
    args = tool_call.get("args", {})
    if not tool or not isinstance(args, dict):
        raise RuntimeError("Invalid tool call payload")

    if tool == "log_sale":
        menu = str(args.get("menu", ""))
        qty = int(args.get("qty", 0))
        price = float(args.get("price", 0.0))

        # --- Guardrails Validation ---
        if not menu.strip():
            raise ValueError("ValueError: menu name cannot be empty")
        if qty <= 0:
            raise ValueError("ValueError: quantity must be positive")
        if price < 0:
            raise ValueError("ValueError: price cannot be negative")

        return _log_sale(menu, qty, price)

    if tool == "query_sales":
        date = str(args.get("date", ""))
        return _query_sales(date)

    if tool == "send_alert":
        message = str(args.get("message", ""))
        if not message.strip():
            raise ValueError("ValueError: alert message cannot be empty")
        return _send_notification(message)

    raise RuntimeError(f"Unknown tool: {tool}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--cmd", required=True, help="คำสั่งภาษาไทย")
    args = parser.parse_args()

    # 1. Print และบันทึก user_input
    print(f"[USER] {args.cmd}")
    log_trace("user_input", args.cmd)

    try:
        # 2. Parse คำสั่ง
        tool_call = parse_command(args.cmd)
        formatted_args = _format_tool_args(tool_call["args"])

        print(f"[LLM]  tool={tool_call['tool']} args={formatted_args}")
        log_trace("llm_response", json.dumps(tool_call))

        # 3. Dispatch tool (พร้อม Guardrails)
        result = dispatch_tool(tool_call)

        print(f"[TOOL] {tool_call['tool']} {result}")
        print(f"[USER] ← {result}")
        log_trace("tool_result", f"{tool_call['tool']} OK: {result}")

    except Exception as e:
        error_msg = str(e)
        print(f"[TOOL ERROR] {error_msg}")
        log_trace("tool_error", error_msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
