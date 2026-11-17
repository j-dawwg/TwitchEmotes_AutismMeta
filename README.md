# TwitchEmotes Autism Meta

An emote pack for **TwitchEmotes v2**, built like TwitchEmotes Hardcore. It works on WoW Forever and retail.

## Install (friends)

1. Install **TwitchEmotes** first. This pack needs it.
2. In **WowUp**, go to **Get Addons**, choose **Install from URL**, paste `https://github.com/j-dawwg/TwitchEmotes_AutismMeta` and click Install.
3. WowUp checks for new releases like any other addon, so updates arrive through it.

Without WowUp: download the zip from the newest [release](https://github.com/j-dawwg/TwitchEmotes_AutismMeta/releases) and unzip it into `Interface/AddOns/`.

In game, `/tam` lists every emote. Type an emote's name in chat, or type `:` and a few letters for autocomplete.

## Release an update (maintainer)

1. Add images (see below) and commit them.
2. Tag the commit and push the tag:
   ```bash
   git tag v1.0.1
   git push origin main v1.0.1
   ```
3. GitHub Actions (`.github/workflows/release.yml`) builds the pack and publishes a release. The release has the zip and a `release.json`, which tells WowUp that the zip is for Forever (16001) and retail.

## Add emotes

1. Dump images into `incoming/`, the staging folder. Messy names are fine. The build moves each image into `source/` under a chat-safe name, for example `pepe laugh-hard (1).png` becomes `pepeLaughHard1`. A name already in use gets a number added. Files that aren't images stay in `incoming/`, and the build lists them.
   - Or drop images straight into `source/`. There the file name is the chat word, so `source/peepoWave.gif` makes `peepoWave`. Names can use letters, digits and `_` only.
   - To rename an emote, rename its file in `source/` and rebuild.
   - Formats: PNG, JPG, WEBP, BMP and TGA for static emotes; GIF and animated WEBP for animated ones.
   - Any size works: images are scaled to fit 32×32 and centered on transparency. Square sources look best.
   - After each build, `build/preview.png` shows every emote with its name. Animated ones are marked `*`.
2. Build and install:
   ```bash
   python build.py --install --zip
   ```
3. Type `/reload` in game to test locally before tagging a release.

Needs Pillow once: `python -m pip install --user Pillow`.

Emotes render on each player's own client, so anyone without the pack just sees the word.

## Pack settings (`pack.json`)

| Key | Default (`null`) | |
|---|---|---|
| `name` | `TwitchEmotes_AutismMeta` | Folder name. It must start with `TwitchEmotes_` (see below). |
| `title` | `Autism Meta` | Emote-menu category name. |
| `slash` | `tam` | Short slash command that lists the emotes, in addition to the long `/teautismmeta`. |
| `tocTitle` | `Twitch Emotes <title>` (purple "Twitch Emotes") | Title in the AddOns list. |
| `notes` | `Custom emote pack (N emotes)…` | AddOns list tooltip. |
| `author` | none | Shown in the AddOns list. |
| `version` | Build date and time | Leave this `null`. Releases take their version from the git tag (`v1.0.1` becomes `1.0.1`). |
| `icon` | First emote | Emote name to use as the AddOns list icon. An `icon.png` next to `build.py` wins over this. |

The icon is written as a 64×64 `icon.tga`, using frame 1 of an animated emote. Any key can also be overridden with a flag (`--title`, `--toc-title`, `--icon`, …).

## Options

| Flag | Default | |
|---|---|---|
| `--size` | `32` | `64` is sharper on a high UI scale, but allows at most 128 frames per animation. |
| `--source` | `source/` | Image folder. |
| `--incoming` | `incoming/` | Staging folder, moved into `source/` first. |
| `--wow` | `D:\Games\World of Warcraft\_forever_` | Install target. |

The build warns when a name is already used by the base addon or another installed TwitchEmotes pack. If two packs use the same name, whichever loads last wins, and packs load alphabetically.

## How it works

- `main.lua` is a fixed template. `build.py` writes `emotes.lua` (names, files, animation data) and the `.toc` file (`## Interface: 16001` for Forever, plus retail, and `## Dependencies: TwitchEmotes`).
- Each emote is registered with `TwitchEmotes:AddEmote`, so the base addon's chat filter replaces the word.
- Static emotes are stored as `path:28:28`. Animated ones are stored as the bare path, which is the base addon's own convention. With the bare path, the base addon's autocomplete finds the frame data.
- Autocomplete: the chat edit boxes hold a reference to the base addon's `AllTwitchEmoteNames` table, so the pack adds its names to that table. Hardcore instead calls `SetupAutoComplete` a second time, which stacks a second set of hooks.
- Animated emotes are vertical strips 32 px wide, padded to a power-of-two height. GIF frames with uneven delays are resampled to a fixed rate of up to 30 fps.
- The base animator only matches `Interface\AddOns\TwitchEmotes\Emotes\…`. The pack wraps that function and animates its own paths after the original runs. Hardcore replaces the function outright, with a pattern that matches any `TwitchEmotes*` folder. If Hardcore loads after this pack, its version takes over and still covers this pack, which is why the name has to start with `TwitchEmotes_`.
- Adds one category to the emote menu on the minimap button, split into chunks of 40.

## Known limits

- In the emote menu, animated emotes show the whole strip squished. The base addon's own animated emotes do the same. In chat and autocomplete they render correctly.
- Not yet checked in game. The logic was tested under a Lua VM against the base addon's real `AddEmote`, `AddCategory`, autocomplete renderer and animator, on Nov 16, 2026.
