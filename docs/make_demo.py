"""Renders docs/demo.gif from real noemu output (text and screenshots captured from runs).

    python make_demo.py RUNS_DIR docs/demo.gif "evidence line 1" "evidence line 2" ...

RUNS_DIR holds jetsnack.txt (crawl), jetsnack-before.png and jetsnack-after.png, jetchat-repro.txt (commands by hand), jetchat.png (the screen before the second
Send), calendar-644.txt and filemanager-111.txt (crawls).
"""
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG, TERM, FG, DIM, ACCENT, RED, YELLOW, GREEN = (13, 17, 23), (22, 27, 34), (230, 237, 243), (139, 148, 158), \
    (88, 166, 255), (248, 81, 73), (227, 179, 65), (86, 211, 100)
FONTS = "/usr/share/fonts/truetype/dejavu/"
font = lambda name, size: ImageFont.truetype(FONTS + name, size)
MONO, SANS, BOLD = "DejaVuSansMono.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"


def card(title: str, lines: list, title_color=FG):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((80, 110), title, font=font(BOLD, 50), fill=title_color)
    y = 220
    for text, color, size in lines:
        d.text((80, y), text, font=font(SANS, size), fill=color)
        y += int(size * 1.7)
    return img


def color_of(line: str):
    if line.startswith(("java.", "kotlin.", "the app crashed")) or "typed text lost" in line or "elements before" in line:
        return RED
    if line.startswith("crawled"):
        return GREEN
    if line.startswith("$"):
        return ACCENT
    return FG


def terminal(lines: list, caption: str, x0=0, width=W, img=None, cols=88):
    img = img or Image.new("RGB", (W, H), TERM)
    d = ImageDraw.Draw(img)
    d.rectangle((x0, 0, x0 + width, H), fill=TERM)
    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse((x0 + 24 + i * 26, 20, x0 + 40 + i * 26, 36), fill=c)
    y = 70
    for raw in lines:
        for part in textwrap.wrap(raw, cols, subsequent_indent="  ") or [""]:
            if y > H - 130:
                break
            d.text((x0 + 40, y), part, font=font(MONO, 20), fill=color_of(raw))
            y += 30
    d.rectangle((0, H - 96, W, H), fill=BG)
    d.text((40, H - 76), caption, font=font(SANS, 26), fill=YELLOW)
    return img


def phone_shot(path: Path) -> Image.Image:
    shot = Image.open(path).convert("RGB")
    return shot.resize((int(shot.width * (H - 96) / shot.height), H - 96))


def crawl_lines(text: str, keep: int) -> list:
    """The summary and the first findings, without stack frames."""
    out = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("at ") and not l.startswith("seconds")]
    return out[:keep]


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    frames, ms = [], []
    frames.append(card("noemu", [
        ("Run your Android app with no emulator, and let it find the bugs.", FG, 34),
        ("", FG, 10),
        ("The app runs on the JVM (Robolectric) from your Gradle project. Nothing to set up.", DIM, 26),
        ("noemu crawl taps through it, rotates the device as it goes, and reports", DIM, 26),
        ("crashes and anything a rotation loses: typed text, state, stacked views.", DIM, 26),
    ], ACCENT)); ms.append(5000)

    img = Image.new("RGB", (W, H), TERM)
    x, labels = W, []
    for name, label in (("jetsnack-after.png", "rotated and back"), ("jetsnack-before.png", "typed \"cupcake\"")):
        shot = phone_shot(src / name)
        x -= shot.width
        img.paste(shot, (x, 0))
        labels.append((x, label))
    img = terminal(["$ noemu start && noemu crawl 100"] + crawl_lines((src / "jetsnack.txt").read_text(), 3),
                   "Google's Jetsnack sample:", width=x, img=img, cols=50)
    d = ImageDraw.Draw(img)
    d.text((40, H - 42), "a rotation wipes the search", font=font(SANS, 26), fill=YELLOW)
    for lx, label in labels:
        d.text((lx + 16, H - 66), label, font=font(BOLD, 24), fill=FG)
    frames.append(img); ms.append(8000)

    phone = phone_shot(src / "jetchat.png")
    img = Image.new("RGB", (W, H), TERM)
    img.paste(phone, (0, 0))
    repro = ["$ " + l if l.startswith("noemu ") else l for l in (src / "jetchat-repro.txt").read_text().splitlines()]
    frames.append(terminal(repro, "Google's Jetchat sample: send the same message twice and it crashes",
                           x0=phone.width, width=W - phone.width, img=img, cols=58)); ms.append(8000)

    frames.append(terminal(["$ noemu crawl 100"] + crawl_lines((src / "calendar-644.txt").read_text(), 7),
                           "Fossify Calendar before its fix for #644 \"App crashes after rotating several times\"")); ms.append(8000)
    frames.append(terminal(["$ noemu start --activity ...ReadTextActivity --data file:///tmp/list.txt",
                            "$ noemu crawl 60"] + crawl_lines((src / "filemanager-111.txt").read_text(), 5),
                           "Fossify File Manager #111: the editor lost unsaved text on rotation")); ms.append(7000)
    if sys.argv[3:]:
        frames.append(card("Measured on real apps", [(line, FG if i == 0 else DIM, 28) for i, line in enumerate(sys.argv[3:])]))
        ms.append(8000)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=True)
    print("wrote", out, len(frames), "frames")


if __name__ == "__main__":
    main()
