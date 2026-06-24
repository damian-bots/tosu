import os
from PIL import Image, ImageDraw, ImageFont

def generate_stats_image(stats, total_chats):
    # 1. Create a dark background image
    W, H = 800, 500
    img = Image.new('RGB', (W, H), color=(25, 25, 25))
    draw = ImageDraw.Draw(img)

    # 2. Load Fonts (Falls back to default if assets are missing)
    try:
        title_font = ImageFont.truetype("AnonXMusic/assets/font.ttf", 45)
        bold_font = ImageFont.truetype("AnonXMusic/assets/font.ttf", 30)
        reg_font = ImageFont.truetype("AnonXMusic/assets/font2.ttf", 30)
    except Exception:
        title_font = ImageFont.load_default()
        bold_font = ImageFont.load_default()
        reg_font = ImageFont.load_default()

    # 3. Draw Titles
    draw.text((W//2, 50), "Bot Growth Statistics", fill=(255, 255, 255), font=title_font, anchor="mm")
    draw.text((W//2, 110), f"Total Active Chats: {total_chats}", fill=(180, 180, 180), font=reg_font, anchor="mm")

    # 4. Draw Table Headers
    headers = ["Period", "Joined", "Left", "Net Growth"]
    x_positions = [80, 280, 450, 600]
    y_start = 180

    for i, header in enumerate(headers):
        draw.text((x_positions[i], y_start), header, fill=(100, 200, 255), font=bold_font)

    # Draw separator line
    draw.line([(70, 230), (730, 230)], fill=(100, 100, 100), width=3)

    # 5. Draw Table Data
    periods = ["Daily", "Weekly", "Monthly", "Yearly"]
    keys = ["daily", "weekly", "monthly", "yearly"]
    
    y_pos = 260
    for period, key in zip(periods, keys):
        data = stats.get(key, {})
        j = data.get("joined", 0)
        l = data.get("left", 0)
        net = j - l
        sign = "+" if net > 0 else ""

        # Color coding: Green for joins, Red for leaves
        draw.text((x_positions[0], y_pos), period, fill=(255, 255, 255), font=reg_font)
        draw.text((x_positions[1], y_pos), str(j), fill=(100, 255, 100), font=reg_font) 
        draw.text((x_positions[2], y_pos), str(l), fill=(255, 100, 100), font=reg_font) 
        draw.text((x_positions[3], y_pos), f"{sign}{net}", fill=(255, 255, 255), font=reg_font)

        y_pos += 50

    # 6. Save and return path
    os.makedirs("cache", exist_ok=True)
    file_path = "cache/stats_chart.png"
    img.save(file_path)
    return file_path
