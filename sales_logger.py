import os
import sys
import json
import argparse
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests

# 1. ตั้งค่า Argument Parser เพื่อรับค่าผ่าน command-line
parser = argparse.ArgumentParser(description="Sales Logger CLI")
parser.add_argument("--menu", required=True, help="ชื่อเมนูอาหาร/สินค้า")
parser.add_argument("--qty", type=int, required=True, help="จำนวนที่ขายได้")
parser.add_argument("--price", type=float, required=True,
                    help="ราคาสินค้าต่อหน่วย")
args = parser.parse_args()

# คำนวณราคารวมและเวลาปัจจุบัน
total_price = args.qty * args.price
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ดึงค่า Credentials จาก Environment Variables
creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
# คุณสามารถระบุ ID ของ Sheet ไว้ที่ Secret หรือใส่ตรงนี้เลยก็ได้
spreadsheet_id = os.environ.get("SPREADSHEET_ID")

# ตรวจสอบเบื้องต้นว่ามี Credentials ไหม
if not creds_json:
    print("Error: Missing GOOGLE_SHEETS_CREDENTIALS environment variable.", file=sys.stderr)
    sys.exit(1)

# 2. ฟังก์ชันการเชื่อมต่อและส่งข้อมูลเข้า Google Sheets
try:
    # โหลด Credentials จาก JSON String
    info = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)

    # เปิด Google Sheet (แนะนำให้ใช้ Sheet ID จาก URL ของชีตคุณ)
    sheet = client.open_by_key(spreadsheet_id).sheet1

    # ข้อมูลที่จะบันทึก [Timestamp, Menu, Qty, Price, Total]
    row_data = [timestamp, args.menu, args.qty, args.price, total_price]
    sheet.append_row(row_data)
    print(f"Successfully logged to Sheets: {row_data}")

# 3. Handle case Sheets ไม่ accessible (ตามโจทย์สั่งให้ print + exit 1 ด้วยข้อความที่เข้าใจง่าย)
except Exception as e:
    print(
        f"❌ Error: Cannot access or write to Google Sheets.\nReason: {e}", file=sys.stderr)
    sys.exit(1)


# 4. ฟังก์ชันส่ง Notification (เลือกใช้ตามที่คุณตั้งค่าในส่วนที่ 1.2)
def send_notification(message):
    # --- กรณีเลือก Telegram ---
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat_id:
        tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
        payload = {"chat_id": tg_chat_id, "text": message}
        try:
            requests.post(tg_url, json=payload, timeout=10)
            print("Telegram notification sent.")
        except Exception as err:
            print(f"Warning: Failed to send Telegram: {err}")

    # --- กรณีเลือก LINE OA ---
    line_token = os.environ.get("LINE_CHANNEL_TOKEN")
    if line_token:
        # หากใช้ LINE Notify API
        line_url = "https://notify-api.line.me/api/notify"
        headers = {"Authorization": f"Bearer {line_token}"}
        payload = {"message": message}
        try:
            requests.post(line_url, headers=headers, data=payload, timeout=10)
            print("LINE notification sent.")
        except Exception as err:
            print(f"Warning: Failed to send LINE: {err}")


# สร้างข้อความสำหรับแจ้งเตือน
notification_msg = (
    f"\n🔔 [New Sale Alert]\n"
    f"รายการ: {args.menu}\n"
    f"จำนวน: {args.qty} ชิ้น\n"
    f"ราคาต่อหน่วย: {args.price} บาท\n"
    f"ยอดรวมทั้งหมด: {total_price} บาท\n"
    f"เวลาบันทึก: {timestamp}"
)

# ส่งแจ้งเตือนไปยังแชนเนลที่เลือก
send_notification(notification_msg)
