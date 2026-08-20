# Session 4 Pivot: Sandwich Cloud Kitchen Sales Logger
import os
import sys
import json
import argparse
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests

# 1. ตั้งค่า Argument Parser เพื่อรับค่าผ่าน command-line
parser = argparse.ArgumentParser(description="Sandwich Cloud Kitchen Sales Logger CLI")
parser.add_argument("--menu", required=True, help="ชื่อเมนูแซนด์วิช เช่น 'เอ้กซันเดย์'")
parser.add_argument("--qty", type=int, required=True, help="จำนวนที่ขาย")
parser.add_argument("--price", type=float, required=True,
                    help="ราคาสินค้าต่อหน่วย")
parser.add_argument("--slot", default="A", choices=["A", "B", "catering"],
                    help="รอบเวลาจัดส่ง: A=07:30, B=11:30, catering=ตามนัด (default: A)")
parser.add_argument("--order-type", default="personal", choices=["personal", "catering"],
                    help="ประเภทออเดอร์: personal=ทานเอง, catering=จัดเลี้ยง (default: personal)")
parser.add_argument("--allergy-note", default="",
                    help="หมายเหตุแพ้อาหาร เช่น 'ไม่เอากลูเตน' หรือ 'แพ้ถั่ว'")
parser.add_argument("--customization", default="",
                    help="หมายเหตุปรับแต่งอื่นๆ เช่น 'แยกซอส' หรือ 'ไม่ใส่ผัก' หรือ 'เปลี่ยนขนมปังโฮล์วีต'")
args = parser.parse_args()

# คำนวณราคารวมและเวลาปัจจุบัน
total_price = args.qty * args.price
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ดึงค่า Credentials จาก Environment Variables
creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
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

    # Schema: [Timestamp, Menu, Qty, Price, Total, DeliverySlot, OrderType, AllergyNote, Customization]
    row_data = [
        timestamp,
        args.menu,
        args.qty,
        args.price,
        total_price,
        args.slot,
        args.order_type,
        args.allergy_note,
        args.customization,
    ]
    sheet.append_row(row_data)
    print(f"Successfully logged to Sheets: {row_data}")

# 3. Handle case Sheets ไม่ accessible
except Exception as e:
    print(
        f"❌ Error: Cannot access or write to Google Sheets.\nReason: {e}", file=sys.stderr)
    sys.exit(1)


# 4. ฟังก์ชันส่ง Notification (เลือกใช้ตามที่ตั้งค่า)
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
        line_url = "https://notify-api.line.me/api/notify"
        headers = {"Authorization": f"Bearer {line_token}"}
        payload = {"message": message}
        try:
            requests.post(line_url, headers=headers, data=payload, timeout=10)
            print("LINE notification sent.")
        except Exception as err:
            print(f"Warning: Failed to send LINE: {err}")


# สร้างข้อความสำหรับแจ้งเตือน (Branding ใหม่ + field ใหม่)
allergy_line = f"\n⚠️ หมายเหตุแพ้อาหาร: {args.allergy_note}" if args.allergy_note else ""
custom_line = f"\n📝 ปรับแต่ง: {args.customization}" if args.customization else ""
notification_msg = (
    f"\n🥪 [ร้านแซนด์วิดวะ — New Order]\n"
    f"รายการ: {args.menu}\n"
    f"จำนวน: {args.qty} ชิ้น/กล่อง\n"
    f"ราคาต่อหน่วย: {args.price} บาท\n"
    f"ยอดรวมทั้งหมด: {total_price} บาท\n"
    f"รอบจัดส่ง: {args.slot}\n"
    f"ประเภทออเดอร์: {args.order_type}"
    f"{allergy_line}"
    f"{custom_line}\n"
    f"เวลาบันทึก: {timestamp}"
)

# ส่งแจ้งเตือนไปยังแชนเนลที่เลือก
send_notification(notification_msg)
