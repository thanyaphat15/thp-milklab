"""MilkLab Caption Generator (S1).

Usage:
    python caption_generator.py

Reads GOOGLE_API_KEY from env. Generates a Thai caption for a milk menu item.
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from google import genai


PROMPT_TEMPLATE = """\
คุณคือ social media manager ของร้าน MilkLab° ร้านนมสดกลางคืน

จงเขียนแคปชั่นภาษาไทย 2 ถึง 3 ประโยคโปรโมตเมนู: {menu}
{tone_instruction}

เงื่อนไข:
- โทนสนุก ใช้คำง่าย ใส่ emoji ได้
- ต้องมีชื่อเมนู ราคา ส่วนผสม
- ต้องมี call-to-action ปิดท้าย เช่น สั่งเลย หรือ ทักแชท
- ห้ามใช้ em dash
"""

TONE_VARIANTS = [
    "น่ารัก",
    "เจนซี",
    "มินิมอล",
]


def generate_caption(menu: str, api_key: str | None = None, tone: str | None = None) -> str:
    """Generate a Thai caption for the given milk menu item."""
    key = api_key or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not set in env or argument")
    client = genai.Client(api_key=key)
    tone_instruction = f"ใช้โทน: {tone}" if tone else ""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=PROMPT_TEMPLATE.format(
            menu=menu, tone_instruction=tone_instruction),
    )
    return response.text or ""


def generate_caption_with_retry(
    menu: str,
    api_key: str | None = None,
    tone: str | None = None,
    max_attempts: int = 3,
) -> str:
    last_caption = ""
    for attempt in range(1, max_attempts + 1):
        caption = generate_caption(menu, api_key=api_key, tone=tone).strip()
        last_caption = caption
        if 0 < len(caption) <= 280:
            return caption
        if attempt < max_attempts:
            continue
    raise RuntimeError(
        f"Failed to generate a caption under 280 characters after {max_attempts} attempts. "
        f"Last caption length: {len(last_caption)}"
    )


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Generate a Thai caption for a MilkLab menu item."
    )
    parser.add_argument(
        "--menu",
        help="Menu text to promote. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=1,
        help="Number of captions to generate (default: 1).",
    )
    parser.add_argument(
        "--variant",
        action="store_true",
        help="Generate 3 caption variants in tones: น่ารัก, เจนซี, มินิมอล.",
    )
    parser.add_argument(
        "--api-key",
        help="Google API key. If omitted, reads from GOOGLE_API_KEY environment variable.",
    )
    args = parser.parse_args()

    menu = args.menu
    if not menu:
        try:
            menu = input("เมนูที่จะโปรโมต: ").strip()
        except EOFError:
            menu = ""

    if not menu:
        print("กรุณาใส่ชื่อเมนู")
        return 1

    if args.variant and args.n != 1:
        print("--variant cannot be used together with --n")
        return 1

    if args.variant:
        for index, tone in enumerate(TONE_VARIANTS, start=1):
            print(f"--- Caption {index}: {tone} ---")
            caption = generate_caption_with_retry(
                menu, api_key=args.api_key, tone=tone
            )
            print(caption)
            if index < len(TONE_VARIANTS):
                print()
        return 0

    if args.n < 1:
        print("--n must be at least 1")
        return 1

    for index in range(1, args.n + 1):
        if args.n > 1:
            print(f"--- Caption {index} ---")
        caption = generate_caption_with_retry(menu, api_key=args.api_key)
        print(caption)
        if index < args.n:
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
