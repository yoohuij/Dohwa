"""Developer preview: python preview_profiles.py SAMPLE_SOURCE_IMAGE."""
import io
import sys
from pathlib import Path
from PIL import Image, ImageDraw
from profile_card import COLORS, render_card, font

def main():
    root = Path(__file__).resolve().parent
    out = root / 'previews'
    out.mkdir(exist_ok=True)
    with Image.open(sys.argv[1]) as source:
        w,h = source.size
        avatar = source.crop((int(w*.038),int(h*.15),int(w*.468),int(h*.845)))
    data = io.BytesIO()
    avatar.save(data,format='PNG')
    sheet = Image.new('RGB',(1500,1320),'#e6edf2')
    d = ImageDraw.Draw(sheet)
    for index,category in enumerate(COLORS):
        card = render_card(data.getvalue(),'도화','도화의 쉼터',category,15*3600,3,index%2==0)
        (out / f'card_{index+1:02}.png').write_bytes(card)
        with Image.open(io.BytesIO(card)) as image:
            thumbnail = image.resize((480,288),Image.Resampling.LANCZOS)
        x,y = 10+(index%3)*500,10+(index//3)*330
        sheet.paste(thumbnail,(x,y))
        d.text((x+240,y+301),category,font=font(22),fill='#17243b',anchor='mt')
    sheet.save(out/'all_cards.jpg',quality=92)

if __name__ == '__main__':
    main()
