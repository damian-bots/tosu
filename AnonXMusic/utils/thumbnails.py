# Authored By Certified Coders © 2025
# Modified for Team Arc - v1.0.0
import os
import re
import aiofiles
import aiohttp
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from youtubesearchpython.__future__ import VideosSearch
from config import YOUTUBE_IMG_URL
from AnonXMusic.core.dir import CACHE_DIR

# --- Layout Configuration ---
PANEL_W, PANEL_H = 800, 580
PANEL_X = (1280 - PANEL_W) // 2
PANEL_Y = 70
TRANSPARENCY = 230
CORNER_RADIUS = 30

# Image Positioning
THUMB_W, THUMB_H = 580, 326  # 16:9 Aspect Ratio
THUMB_X = PANEL_X + (PANEL_W - THUMB_W) // 2
THUMB_Y = PANEL_Y + 40

# Text Positioning
TEXT_CENTER_X = PANEL_X + (PANEL_W // 2)
TITLE_Y = THUMB_Y + THUMB_H + 25
META_Y = TITLE_Y + 45
BAR_Y = META_Y + 50

# Branding
BRAND_TEXT = "Powered By Team Arc"
BRAND_Y = PANEL_Y + PANEL_H - 50

MAX_TITLE_WIDTH = 700

# Font paths — updated for AnonXMusic assets directory
_FONT_TITLE_PATH = "AnonXMusic/assets/font2.ttf"
_FONT_REG_PATH   = "AnonXMusic/assets/font.ttf"
_FONT_BRAND_PATH = "AnonXMusic/assets/font2.ttf"


def trim_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    ellipsis = "..."
    if font.getlength(text) <= max_w:
        return text
    for i in range(len(text) - 1, 0, -1):
        if font.getlength(text[:i] + ellipsis) <= max_w:
            return text[:i] + ellipsis
    return ellipsis


async def get_thumb(videoid: str) -> str:
    cache_path = os.path.join(CACHE_DIR, f"{videoid}_v5.png")
    if os.path.exists(cache_path):
        return cache_path

    # --- 1. Fetch Video Metadata ---
    try:
        results = VideosSearch(f"https://www.youtube.com/watch?v={videoid}", limit=1)
        results_data = await results.next()
        result_items = results_data.get("result", [])
        if not result_items:
            raise ValueError("No results found.")
        data = result_items[0]

        raw_title = data.get("title", "Unsupported Title")
        title = re.sub(r"\W+", " ", raw_title).title()

        thumbnail = data.get("thumbnails", [{}])[0].get("url", YOUTUBE_IMG_URL)
        duration = data.get("duration")
        views = data.get("viewCount", {}).get("short", "Unknown Views")
        channel = data.get("channel", {}).get("name", "Unknown Channel")
    except Exception:
        title, thumbnail, duration, views, channel = (
            "Unsupported Title", YOUTUBE_IMG_URL, None, "Unknown Views", "Unknown Channel"
        )

    is_live = not duration or str(duration).strip().lower() in {"", "live", "live now"}
    duration_text = "LIVE" if is_live else duration or "00:00"

    # --- 2. Download Thumbnail ---
    thumb_path = os.path.join(CACHE_DIR, f"thumb{videoid}.png")
    downloaded = False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(thumbnail) as resp:
                if resp.status == 200:
                    async with aiofiles.open(thumb_path, "wb") as f:
                        await f.write(await resp.read())
                    downloaded = True
    except Exception:
        pass

    if not downloaded or not os.path.exists(thumb_path):
        return YOUTUBE_IMG_URL

    # --- 3. Graphics Composition ---
    try:
        try:
            font_title = ImageFont.truetype(_FONT_TITLE_PATH, 36)
            font_reg   = ImageFont.truetype(_FONT_REG_PATH, 22)
            font_brand = ImageFont.truetype(_FONT_BRAND_PATH, 20)
        except OSError:
            font_title = font_reg = font_brand = ImageFont.load_default()

        # Background — blurred & darkened
        base = Image.open(thumb_path).resize((1280, 720)).convert("RGBA")
        bg = base.filter(ImageFilter.GaussianBlur(radius=15))
        bg = ImageEnhance.Brightness(bg).enhance(0.5)

        # Frosted white panel
        overlay = Image.new("RGBA", (PANEL_W, PANEL_H), (255, 255, 255, TRANSPARENCY))
        mask = Image.new("L", (PANEL_W, PANEL_H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, PANEL_W, PANEL_H), CORNER_RADIUS, fill=255)
        bg.paste(overlay, (PANEL_X, PANEL_Y), mask)

        draw = ImageDraw.Draw(bg)

        # Thumbnail with rounded corners
        thumb_img = base.resize((THUMB_W, THUMB_H))
        thumb_mask = Image.new("L", (THUMB_W, THUMB_H), 0)
        ImageDraw.Draw(thumb_mask).rounded_rectangle((0, 0, THUMB_W, THUMB_H), 15, fill=255)
        bg.paste(thumb_img, (THUMB_X, THUMB_Y), thumb_mask)

        # --- 4. Text ---
        trunc_title = trim_to_width(title, font_title, MAX_TITLE_WIDTH)
        title_w = font_title.getlength(trunc_title)
        draw.text((TEXT_CENTER_X - title_w / 2, TITLE_Y), trunc_title, fill=(20, 20, 20), font=font_title)

        meta_text = f"{views}  •  {channel}"
        meta_w = font_reg.getlength(meta_text)
        draw.text((TEXT_CENTER_X - meta_w / 2, META_Y), meta_text, fill=(60, 60, 60), font=font_reg)

        # Progress bar
        BAR_WIDTH, BAR_HEIGHT = 550, 8
        BAR_START_X = TEXT_CENTER_X - (BAR_WIDTH // 2)

        draw.rounded_rectangle(
            (BAR_START_X, BAR_Y, BAR_START_X + BAR_WIDTH, BAR_Y + BAR_HEIGHT),
            radius=4, fill=(200, 200, 200)
        )
        FILL_WIDTH = int(BAR_WIDTH * 0.45)
        draw.rounded_rectangle(
            (BAR_START_X, BAR_Y, BAR_START_X + FILL_WIDTH, BAR_Y + BAR_HEIGHT),
            radius=4, fill=(20, 20, 20)
        )
        draw.ellipse(
            (BAR_START_X + FILL_WIDTH - 8, BAR_Y + (BAR_HEIGHT // 2) - 8,
             BAR_START_X + FILL_WIDTH + 8, BAR_Y + (BAR_HEIGHT // 2) + 8),
            fill=(20, 20, 20)
        )

        # Timestamps
        draw.text((BAR_START_X, BAR_Y + 20), "00:00", fill=(80, 80, 80), font=font_reg)
        dur_w = font_reg.getlength(duration_text)
        draw.text((BAR_START_X + BAR_WIDTH - dur_w, BAR_Y + 20), duration_text, fill=(20, 20, 20), font=font_reg)

        # --- 5. Branding ---
        brand_w = font_brand.getlength(BRAND_TEXT)
        draw.text(
            (TEXT_CENTER_X - brand_w / 2, BRAND_Y),
            BRAND_TEXT,
            fill=(100, 100, 100),
            font=font_brand,
        )

        # Cleanup
        try:
            os.remove(thumb_path)
        except OSError:
            pass

        bg.save(cache_path)
        return cache_path

    except Exception:
        return YOUTUBE_IMG_URL
