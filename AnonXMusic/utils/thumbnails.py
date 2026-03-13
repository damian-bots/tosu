import os
import re
import textwrap

import aiofiles
import aiohttp
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from unidecode import unidecode
from youtubesearchpython.__future__ import VideosSearch

from AnonXMusic import app
from config import YOUTUBE_IMG_URL


def clear(text):
    """Removes special characters from a string for safe rendering."""
    list_words = text.split(" ")
    title = ""
    for i in list_words:
        if len(title) + len(i) < 60:
            title += " " + i
    return title.strip()


async def get_thumb(videoid):
    if os.path.isfile(f"cache/{videoid}.png"):
        return f"cache/{videoid}.png"

    url = f"https://www.youtube.com/watch?v={videoid}"
    try:
        results = VideosSearch(url, limit=1)
        search_data = (await results.next()).get("result", [])
        
        # Prevent crash if search fails
        if not search_data:
            return YOUTUBE_IMG_URL

        for result in search_data:
            try:
                title = result["title"]
                title = re.sub(r"\W+", " ", title)
                title = title.title()
            except:
                title = "Unsupported Title"
            try:
                duration = result["duration"]
            except:
                duration = "Unknown Mins"
                
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            
            try:
                views = result["viewCount"]["short"]
            except:
                views = "Unknown Views"
            try:
                channel = result["channel"]["name"]
            except:
                channel = "Unknown Channel"

        # Download the thumbnail
        async with aiohttp.ClientSession() as session:
            async with session.get(thumbnail) as resp:
                if resp.status == 200:
                    f = await aiofiles.open(f"cache/thumb{videoid}.png", mode="wb")
                    await f.write(await resp.read())
                    await f.close()

        # Load the original image
        youtube = Image.open(f"cache/thumb{videoid}.png").convert("RGBA")

        # ==========================================
        # 🎨 PREMIUM UI GENERATION START
        # ==========================================

        # 1. Create blurred & darkened background
        background = youtube.resize((1280, 720))
        background = background.filter(filter=ImageFilter.BoxBlur(30)) # Slightly heavier blur
        enhancer = ImageEnhance.Brightness(background)
        background = enhancer.enhance(0.35) # Slightly darker for better text contrast
        draw = ImageDraw.Draw(background)

        # 2. Load Fonts (Swapped weights: Now Playing = Bold, Title = Regular)
        try:
            # font.ttf is usually the bold/heavy font, font2.ttf is regular/light
            font_now_playing = ImageFont.truetype("AnonXMusic/assets/font.ttf", 38)
            font_title = ImageFont.truetype("AnonXMusic/assets/font2.ttf", 52) 
            font_sub = ImageFont.truetype("AnonXMusic/assets/font2.ttf", 32)
            font_small = ImageFont.truetype("AnonXMusic/assets/font2.ttf", 25)
        except Exception:
            # Fallback to default PIL font if assets are missing
            font_now_playing = ImageFont.load_default()
            font_title = ImageFont.load_default()
            font_sub = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # 3. Create the 1:1 Square Cover Art (Using ImageOps.fit to avoid black bars)
        cover = ImageOps.fit(youtube, (450, 450), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        
        # 4. Add Rounded Corners to Cover
        mask = Image.new('L', (450, 450), 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.rounded_rectangle([(0, 0), (450, 450)], radius=35, fill=255)
        cover.putalpha(mask)

        # 5. Create Drop Shadow for Cover Art
        shadow = Image.new('RGBA', (470, 470), (0, 0, 0, 180))
        shadow_mask = Image.new('L', (470, 470), 0)
        ImageDraw.Draw(shadow_mask).rounded_rectangle([(0, 0), (470, 470)], radius=35, fill=255)
        shadow.putalpha(shadow_mask)
        shadow = shadow.filter(ImageFilter.BoxBlur(18))
        
        # Paste Shadow, then paste Cover
        background.paste(shadow, (85, 100), shadow)
        background.paste(cover, (95, 110), cover)

        # 6. Add Text Elements (Right side)
        
        # "NOW PLAYING" Badge (Bolded)
        draw.text((600, 115), "N O W   P L A Y I N G", fill="white", font=font_now_playing)

        # Smart Title Wrapper (Max 2 lines, narrowed width to prevent bleed)
        wrapped_title = textwrap.wrap(title, width=19)
        y_pos = 180
        for line in wrapped_title[:2]:
            draw.text((600, y_pos), line, fill="white", font=font_title)
            y_pos += 65 # Tighter line spacing for regular font
            
        y_pos += 25
        
        # Channel & Views
        draw.text((600, y_pos), f"Artist:  {channel}", fill=(210, 210, 210), font=font_sub)
        y_pos += 50
        draw.text((600, y_pos), f"Views:  {views[:20]}", fill=(210, 210, 210), font=font_sub)

        # Bot Watermark
        bot_text = f"Powered by {unidecode(app.name)}"
        draw.text((600, 520), bot_text, fill=(140, 140, 140), font=font_small)

        # 7. Progress Bar UI
        bar_y = 620
        # Dark Background Track
        draw.line([(100, bar_y), (1180, bar_y)], fill=(80, 80, 80), width=8, joint="curve")
        # Bright Foreground Track (Fake 25% progress)
        draw.line([(100, bar_y), (370, bar_y)], fill="white", width=8, joint="curve")
        # Playhead Knob
        draw.ellipse([(360, bar_y - 12), (384, bar_y + 12)], fill="white")

        # 8. Time Indicators
        draw.text((100, 640), "00:00", fill=(200, 200, 200), font=font_small)
        
        # Calculate width of duration text to right-align it securely
        try:
            dur_width = draw.textlength(duration[:23], font=font_small)
        except AttributeError:
            dur_width = 80 # Fallback for old PIL versions
            
        draw.text((1180 - int(dur_width), 640), duration[:23], fill=(200, 200, 200), font=font_small)

        # Clean up temporary file
        try:
            os.remove(f"cache/thumb{videoid}.png")
        except:
            pass
            
        # Save final output
        background.save(f"cache/{videoid}.png")
        return f"cache/{videoid}.png"
        
    except Exception as e:
        print(f"Thumbnail Generation Error: {e}")
        return YOUTUBE_IMG_URL
