"""Build a TwitchEmotes (v2) emote pack from a folder of images.

Drop PNG/JPG/WEBP/GIF files into source/ (file name = the word you type in
chat, e.g. source/gachiW.gif -> gachiW), then:

    python build.py                 # builds build/<pack>/
    python build.py --install       # ...and copies it into Forever's AddOns
    python build.py --zip           # ...and makes build/<pack>.zip for friends

Static images become 32x32 TGAs. Animated GIF/WEBP become a 32px-wide vertical
strip of frames padded to a power-of-two height, plus animation metadata that
the base addon's animator understands (same layout as TwitchEmotes_Hardcore).
Needs Pillow: python -m pip install --user Pillow
"""
import argparse
import datetime
import json
import math
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageSequence

HERE = Path(__file__).resolve().parent
DEFAULT_WOW = Path(r"D:\Games\World of Warcraft\_forever_")
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga", ".avif"}
NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")  # base addon splits words on space , ' < > ? - . !
MAX_TEX = 8192   # tallest strip the base packs use
MAX_FPS = 30     # animator ticks every 33 ms
CHAT = 28        # chat display height, same as the base addon
WIDE = 1.25      # static images at least this much wider than tall keep their width
INTERFACE = "16001, 120100, 120105"  # Forever, plus retail so it loads there too


def write_tga(img: Image.Image, path: Path) -> None:
    """Uncompressed 32-bit BGRA TGA, top-left origin (same as Hardcore's files)."""
    img = img.convert("RGBA")
    w, h = img.size
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 32, 0x28)
    r, g, b, a = img.split()
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()
    path.write_bytes(header + data)


def fit(frame: Image.Image, size: int, height: int = None) -> Image.Image:
    """Scale to fit size x height (default square) keeping aspect ratio, centered on transparency."""
    frame = frame.convert("RGBA")
    height = height or size
    s = min(size / frame.width, height / frame.height)
    if s != 1:
        frame = frame.resize((max(1, round(frame.width * s)), max(1, round(frame.height * s))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, height), (0, 0, 0, 0))
    canvas.paste(frame, ((size - frame.width) // 2, (height - frame.height) // 2), frame)
    return canvas


def trim(img: Image.Image) -> Image.Image:
    """Crop fully transparent borders so the emote fills its chat slot."""
    img = img.convert("RGBA")
    box = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    return img.crop(box) if box else img


def static_emote(img: Image.Image, size: int):
    """Static texture plus the |T size string. Wide images get a 2:1 texture and
    texture coordinates cropped to the content, so they keep their shape in chat."""
    img = trim(img)
    if img.width / img.height < WIDE:
        return fit(img, size), f":{CHAT}:{CHAT}"
    tex = fit(img, size * 2, size)
    box = tex.getchannel("A").getbbox() or (0, 0, size * 2, size)
    left, right = box[0], box[2]
    width = round(CHAT * (right - left) / size)
    return tex, f":{CHAT}:{width}:0:0:{size * 2}:{size}:{left}:{right}:0:{size}"


def next_pow2(n: int) -> int:
    return 1 << max(0, math.ceil(math.log2(n)))


def load_frames(path: Path):
    """Returns (frames, durations_ms). One frame for static images."""
    im = Image.open(path)
    if not getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) < 2:
        return [im.convert("RGBA")], None
    frames, durs = [], []
    for fr in ImageSequence.Iterator(im):
        fr.load()  # AVIF only fills info["duration"] once the frame is decoded
        d = fr.info.get("duration", im.info.get("duration", 100)) or 100
        if d < 20:  # browsers treat 0-10 ms GIF delays as 100 ms
            d = 100
        frames.append(fr.convert("RGBA"))
        durs.append(d)
    return frames, durs


def resample(frames, durs, size):
    """Resample a variable-delay animation onto a fixed frame rate."""
    total = sum(durs)
    max_frames = MAX_TEX // size
    fps = min(MAX_FPS, 1000 / min(durs))
    n = max(2, round(total / 1000 * fps))
    if n > max_frames:
        n = max_frames
    starts, t = [], 0
    for d in durs:
        starts.append(t)
        t += d
    out = []
    for i in range(n):
        at = (i + 0.5) * total / n
        k = max(j for j, s in enumerate(starts) if s <= at)
        out.append(frames[k])
    return out, round(n / (total / 1000), 2)


def existing_names(wow: Path, pack: str):
    """Emote names already used by the base addon and other installed packs."""
    names = {}
    addons = wow / "Interface" / "AddOns"
    if not addons.is_dir():
        return names
    pat = re.compile(r'\[\s*"([^"]+)"\s*\]\s*=[^\n]*\.tga', re.I)
    for d in addons.glob("TwitchEmotes*"):
        if d.name == pack:
            continue
        for lua in d.glob("*.lua"):
            for m in pat.finditer(lua.read_text("utf-8", errors="ignore")):
                names.setdefault(m.group(1), d.name)
    return names


def emote_name(stem: str) -> str:
    """'pepe laugh-hard (1)' -> 'pepeLaughHard1': chat-safe, keeps existing caps."""
    stem = re.sub(r"[-_ .]\d+x$", "", stem)  # 7TV/BTTV size tags: 'LELU-4x' -> 'LELU'
    words = [w for w in re.split(r"[^A-Za-z0-9]+", stem) if w]
    if not words:
        return ""
    return words[0] + "".join(w[:1].upper() + w[1:] for w in words[1:])


def ingest(incoming: Path, source: Path) -> None:
    """Move images from incoming/ into source/ under chat-safe names."""
    if not incoming.is_dir():
        return
    taken = {p.stem.lower() for p in source.iterdir() if p.suffix.lower() in EXTS}
    for f in sorted(incoming.iterdir(), key=lambda p: p.name.lower()):
        if f.name.startswith(".") or f.is_dir():
            continue
        if f.suffix.lower() not in EXTS:
            print(f"  incoming: left {f.name} (not an image type the build reads)")
            continue
        try:
            Image.open(f).verify()
        except Exception:
            print(f"  incoming: left {f.name} (could not be opened as an image)")
            continue
        base = emote_name(f.stem) or "emote"
        name, i = base, 2
        while name.lower() in taken:
            name, i = f"{base}{i}", i + 1
        taken.add(name.lower())
        f.rename(source / (name + f.suffix.lower()))
        note = "" if name == f.stem else f"  (from '{f.name}')"
        print(f"  incoming: added {name}{note}")


def find_icon(spec, src: Path, emotes):
    """Icon source: icon.* next to build.py, else the emote named in pack.json, else the first emote."""
    for ext in EXTS:
        p = HERE / ("icon" + ext)
        if p.exists():
            return p
    if spec:
        for p in src.iterdir():
            if p.stem == spec and p.suffix.lower() in EXTS:
                return p
        print(f"  warning: icon '{spec}' not found in source/, using the first emote")
    first = emotes[0][0]
    return next(p for p in src.iterdir() if p.stem == first and p.suffix.lower() in EXTS)


def preview_sheet(src: Path, emotes, anim, path: Path) -> None:
    """Grid of every emote (frame 1 for animated) with its name, for checking the build."""
    cell, cols = 96, 8
    rows = math.ceil(len(emotes) / cols)
    sheet = Image.new("RGBA", (cols * cell, rows * cell), (40, 42, 48, 255))
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    for i, (name, tga, _) in enumerate(emotes):
        f = next(p for p in src.iterdir() if p.stem == name and p.suffix.lower() in EXTS)
        img = fit(load_frames(f)[0][0], 64)
        x, y = (i % cols) * cell, (i // cols) * cell
        sheet.paste(img, (x + 16, y + 6), img)
        label = name if len(name) <= 14 else name[:13] + "~"
        d.text((x + 4, y + 74), label + (" *" if tga in anim else ""), fill=(230, 230, 230, 255))
    sheet.save(path)


def build(args) -> Path:
    src = Path(args.source)
    pack = args.name
    out = HERE / "build" / pack
    if out.exists():
        shutil.rmtree(out)
    (out / "emotes").mkdir(parents=True)

    taken = existing_names(Path(args.wow), pack)
    emotes, anim, problems = [], {}, []
    seen = {}
    for f in sorted(src.iterdir(), key=lambda p: p.name.lower()):
        if f.suffix.lower() not in EXTS:
            continue
        name = f.stem
        if not NAME_RE.match(name):
            problems.append(f"skip {f.name}: name must be letters, digits or _ only")
            continue
        if name.lower() in seen:
            problems.append(f"skip {f.name}: same name as {seen[name.lower()]}")
            continue
        seen[name.lower()] = f.name
        if name in taken:
            print(f"  warning: '{name}' is also in {taken[name]}; whichever loads last wins")
        frames, durs = load_frames(f)
        tga = name + ".tga"
        suffix = None
        if durs is None:
            tex, suffix = static_emote(frames[0], args.static_size)
            write_tga(tex, out / "emotes" / tga)
            wide = suffix != f":{CHAT}:{CHAT}"
            print(f"  {name:24s} static {tex.width}x{tex.height}" + (f", wide ({suffix.split(':')[2]}px in chat)" if wide else ""))
            if not wide:
                suffix = None
        else:
            frames, fps = resample(frames, durs, args.size)
            n = len(frames)
            h = next_pow2(n * args.size)
            strip = Image.new("RGBA", (args.size, h), (0, 0, 0, 0))
            for i, fr in enumerate(frames):
                strip.paste(fit(fr, args.size), (0, i * args.size))
            write_tga(strip, out / "emotes" / tga)
            anim[tga] = dict(nFrames=n, frameWidth=args.size, frameHeight=args.size,
                             imageWidth=args.size, imageHeight=h, framerate=fps)
            print(f"  {name:24s} animated {n} frames @ {fps} fps, {args.size}x{h}")
        emotes.append((name, tga, suffix))

    for p in problems:
        print("  " + p)
    if not emotes:
        sys.exit(f"No images found in {src}")

    def lua_str(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["-- Generated by build.py. Do not edit; re-run the build instead.",
             "local _, ns = ...", f"ns.title = {lua_str(args.title)}",
             f"ns.slash = {lua_str(args.slash) if args.slash else 'nil'}", "ns.emotes = {"]
    lines += [f"    {{{lua_str(n)}, {lua_str(t)}" + (f", {lua_str(s)}" if s else "") + "},"
              for n, t, s in emotes]
    lines += ["}", "ns.anim = {"]
    for t, a in anim.items():
        fields = ", ".join(f"{k} = {v}" for k, v in a.items())
        lines.append(f"    [{lua_str(t)}] = {{{fields}}},")
    lines.append("}")
    (out / "emotes.lua").write_text("\n".join(lines) + "\n", "utf-8")

    shutil.copy(HERE / "main.lua", out / "main.lua")

    icon_src = find_icon(args.icon, src, emotes)
    write_tga(fit(load_frames(icon_src)[0][0], 64), out / "icon.tga")
    preview_sheet(src, emotes, anim, out.parent / "preview.png")

    version = args.version
    toc =[f"## Interface: {INTERFACE}",
           f"## Title: {args.toc_title or ('|cff9146FFTwitch Emotes|r ' + args.title)}",
           f"## Notes: {args.notes or f'Custom emote pack ({len(emotes)} emotes) for TwitchEmotes'}",
           f"## IconTexture: Interface\\AddOns\\{pack}\\icon.tga",
           f"## Version: {version}"]
    if args.author:
        toc.append(f"## Author: {args.author}")
    toc += ["## Dependencies: TwitchEmotes", "emotes.lua", "main.lua"]
    (out / f"{pack}.toc").write_text("\n".join(toc) + "\n", "utf-8")
    print(f"Built {out} ({len(emotes)} emotes, {len(anim)} animated, version {version}, icon {icon_src.name})")
    print(f"Preview: {out.parent / 'preview.png'}")
    return out


def main():
    # pack.json holds the pack's identity; command-line flags override it.
    cfg = {}
    cfg_path = HERE / "pack.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text("utf-8"))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=cfg.get("name", "TwitchEmotes_Custom"),
                    help="addon folder name; must start with TwitchEmotes_ (default %(default)s)")
    ap.add_argument("--title", default=cfg.get("title", "Custom"), help="emote menu category name")
    ap.add_argument("--slash", default=cfg.get("slash"), help="extra short slash command that lists the emotes")
    ap.add_argument("--toc-title", default=cfg.get("tocTitle"), help="addon list title (default 'Twitch Emotes <title>')")
    ap.add_argument("--notes", default=cfg.get("notes"), help="addon list tooltip text")
    ap.add_argument("--author", default=cfg.get("author"))
    ap.add_argument("--version", default=cfg.get("version"), help="default: build date and time")
    ap.add_argument("--icon", default=cfg.get("icon"), help="emote name for the addon-list icon (or put icon.png next to build.py)")
    ap.add_argument("--source", default=str(HERE / "source"), help="image folder (default source/)")
    ap.add_argument("--incoming", default=str(HERE / "incoming"), help="staging folder moved into source/ first")
    ap.add_argument("--static-size", type=int, default=64, choices=[32, 64, 128],
                    help="static emote texture height; 64 stays sharp at high resolutions (default %(default)s)")
    ap.add_argument("--size", type=int, default=32, choices=[32, 64],
                    help="texture size; 64 is sharper on high UI scale but max 128 frames")
    ap.add_argument("--wow", default=str(DEFAULT_WOW), help="WoW flavor folder (default _forever_)")
    ap.add_argument("--install", action="store_true", help="copy into <wow>/Interface/AddOns")
    ap.add_argument("--zip", action="store_true", help="write build/<name>.zip to send to friends")
    args = ap.parse_args()

    # Hardcore, if installed, replaces the animator with one that only matches
    # Interface\AddOns\TwitchEmotes*, so keep that prefix.
    if not re.match(r"^TwitchEmotes_[A-Za-z0-9_]+$", args.name):
        sys.exit("--name must look like TwitchEmotes_Something")

    Path(args.source).mkdir(exist_ok=True)
    ingest(Path(args.incoming), Path(args.source))
    args.version = args.version or datetime.datetime.now().strftime("%Y.%m.%d.%H%M")
    out = build(args)
    if args.zip:
        for old in out.parent.glob(f"{args.name}*.zip"):
            old.unlink()
        zname = f"{args.name}-{args.version}.zip"
        z = out.parent / zname
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(out.rglob("*")):
                zf.write(f, Path(args.name) / f.relative_to(out))
        # BigWigs-packager style metadata: WowUp reads it to pick the zip per
        # game flavor (it maps the Forever client to "forever").
        flavors = []
        for num in [n.strip() for n in INTERFACE.split(",")]:
            flavor = "forever" if num.startswith("16") else "mainline"
            if flavor not in [f["flavor"] for f in flavors]:
                flavors.append({"flavor": flavor, "interface": int(num)})
        (out.parent / "release.json").write_text(json.dumps(
            {"releases": [{"name": args.toc_title or args.name, "version": args.version,
                           "filename": zname, "nolib": False, "metadata": flavors}]}, indent=2), "utf-8")
        print(f"Zip: {z}  (friends unzip it into Interface/AddOns)")
    if args.install:
        dest = Path(args.wow) / "Interface" / "AddOns" / args.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(out, dest)
        print(f"Installed: {dest}  (/reload in game; a brand-new folder loads on /reload too)")


if __name__ == "__main__":
    main()
