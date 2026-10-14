# 🎬 AI YouTube Shorts Generator

Automatically turn a 40–60 second script into a polished, vertical YouTube Short (1080×1920) with AI narration, licensed stock visuals, word-highlight captions, background music, and fact-checking — no manual filming or editing required.

---

## ✨ Features

| Feature | Details |
|---|---|
| 📄 Script input | `.txt` file or stdin, 40–60s validated |
| 🔍 Fact-checking | Gemini AI + DuckDuckGo web search |
| 🖼️ Licensed visuals | Pexels Videos, Pixabay Videos, Wikimedia Commons |
| 🎨 Fallback visuals | Gradient cards with Ken Burns zoom (Pillow + FFmpeg) |
| 🎙️ Narration | Microsoft Edge TTS (en-US-AriaNeural) + gTTS fallback |
| 💬 Captions | Word-by-word yellow highlight (TikTok style) + SRT file |
| 🎵 Background music | Pixabay Music (free, commercial use with attribution) |
| 🎬 Video output | 1080×1920 MP4, H.264 + AAC, ~30–60 MB |
| ✅ Quality check | Dimensions, duration, audio, FPS verified by FFprobe |
| 📝 Metadata | Gemini-generated YouTube title, description, hashtags |

---

## 🖥️ System Requirements

- Windows 10/11
- Python 3.10+
- 8 GB RAM (minimum)
- Internet connection (for TTS, stock media, fact-checking)
- FFmpeg (see installation below)

---

## 🚀 Setup (Windows)

### Step 1 — Install FFmpeg

**Option A (recommended — winget):**
```powershell
winget install Gyan.FFmpeg
# Restart your terminal after installation
```

**Option B (manual):**
1. Download from https://www.gyan.dev/ffmpeg/builds/ (ffmpeg-release-essentials.zip)
2. Extract to `C:\ffmpeg\`
3. Add `C:\ffmpeg\bin` to your PATH environment variable
4. Restart your terminal
5. Verify: `ffmpeg -version`

### Step 2 — Install Python Dependencies

```powershell
cd e:\AI-Shorts-Generator
pip install -r requirements.txt
```

### Step 3 — Get Free API Keys

You need these (all **free**, no credit card required):

| Key | Where to get | Time needed |
|---|---|---|
| **Pexels API** | https://www.pexels.com/api/ | ~2 min |
| **Pixabay API** | https://pixabay.com/api/docs/ | ~2 min |
| **Gemini API** | https://aistudio.google.com/apikey | ~1 min (you already have Pro) |

### Step 4 — Configure API Keys

```powershell
copy .env.example .env
notepad .env
```

Fill in your keys:
```
PEXELS_API_KEY=your_pexels_key_here
PIXABAY_API_KEY=your_pixabay_key_here
GEMINI_API_KEY=your_gemini_key_here
```

### Step 5 — (Optional) Download Font

For best caption quality, download **Montserrat Bold**:
1. Go to https://fonts.google.com/specimen/Montserrat
2. Download and extract
3. Copy `Montserrat-Bold.ttf` to `assets/fonts/Montserrat-Bold.ttf`

*(If skipped, Arial Bold or system default will be used)*

---

## 🎬 Generate Your First Short

```powershell
cd e:\AI-Shorts-Generator
python generate_short.py --script inputs/sample_script.txt --topic "Space Telescopes"
```

### With your own script:
```powershell
python generate_short.py --script inputs/my_script.txt --topic "Your Topic" --title "Your Title"
```

### Options:
```
--script PATH        Path to script .txt file (required)
--topic TEXT         Topic for visual search and metadata
--title TEXT         Suggested video title
--config PATH        Config file (default: config.yaml)
--verbose, -v        Show debug logs
--skip-fact-check    Skip fact-checking (faster testing)
--keep-temp          Keep temp files after render
```

---

## 📁 Output Structure

Each run creates a timestamped folder:

```
outputs/
└── Space_Telescopes_20241014_143022/
    ├── final_short.mp4         ← Your finished video ✅
    ├── script.txt              ← Original script
    ├── captions.srt            ← Subtitle file
    ├── sources_and_licenses.csv← Asset manifest with full attribution
    ├── fact_check_report.md    ← Fact-checking results
    ├── video_metadata.txt      ← YouTube title/description/hashtags
    └── production_report.md    ← QC results
```

---

## ✍️ Writing a Good Script

- **Length:** 120–160 words → ~45–55 seconds at 150 wpm
- **Structure:** Hook → Key facts → Call to action
- **Paragraphs:** Separate scenes with a blank line between paragraphs
- **Style:** Conversational, present tense, short sentences
- **Claims:** Use specific facts (names, numbers, dates) — they'll be fact-checked

**Example (correct length):**
```
Webb shocked us again this year.

The James Webb Space Telescope delivered its most detailed image yet...

Scientists confirmed that NIRCam captured light from the earliest galaxies...

These discoveries are reshaping our understanding — and we're just getting started.
```

---

## ⚙️ Configuration

Edit `config.yaml` to customise:

```yaml
narration:
  voice: en-US-AriaNeural   # Change TTS voice
  words_per_minute: 150

captions:
  font_size: 72             # Caption size
  highlight_color: "yellow" # Active word color

music:
  enabled: true
  volume_during_narration: 0.08  # Music volume (8%)

video:
  fps: 30
  bitrate: "8000k"
```

**Available Edge TTS voices (American English):**
- `en-US-AriaNeural` — Warm female (default)
- `en-US-GuyNeural` — Clear male
- `en-US-JennyNeural` — Conversational female
- `en-US-DavisNeural` — Expressive male

---

## 🔑 API Keys — How to Get Them

### Pexels (free, no credit card)
1. Go to https://www.pexels.com/api/
2. Click "Get Started for Free"
3. Create account → Go to API section → Copy your key

### Pixabay (free, no credit card)
1. Go to https://pixabay.com/api/docs/
2. Create free account
3. Your API key appears at the top of the API docs page

### Google Gemini (you already have Pro)
1. Go to https://aistudio.google.com/apikey
2. Click "Create API key"
3. Copy the key

---

## ⚠️ Limitations & Known Issues

| Limitation | Details |
|---|---|
| Internet required | Edge TTS, stock media, and fact-checking need internet |
| Stock video availability | Depends on keyword quality; some scenes may use generated cards |
| Fact-checking is best-effort | Automated only — always review before publishing |
| Captions use center-crop | May cut edges on very wide stock video |
| Music may not match mood | Pixabay results are keyword-limited |
| No auto-upload | Human review and manual YouTube upload required |
| No offline TTS | Edge TTS uses Microsoft cloud (free but needs internet) |

---

## 📜 License & Attribution

This tool generates content using:
- **Pexels License** — free for commercial use, attribution encouraged
- **Pixabay Content License** — free for commercial use, attribution required
- **Wikimedia Commons** — CC licenses vary; attribution always included
- **Microsoft Edge TTS** — free for general/commercial use
- **FFmpeg** — LGPL 2.1+

All generated `sources_and_licenses.csv` files include full attribution information.

---

## 🐛 Troubleshooting

**`FFmpeg not found`**
→ Install FFmpeg and add to PATH. See Step 1.

**`No stock asset found`**
→ Try broader keywords or check your API keys in `.env`.

**`edge-tts timeout`**
→ Check your internet connection. The tool will fall back to gTTS automatically.

**`Gemini API error`**
→ Verify your `GEMINI_API_KEY` in `.env`. Check quota at https://aistudio.google.com/

**Video is too dark/light**
→ Edit `config.yaml` → `video.bitrate` or check source clip quality.

---

## 🔄 Generate Another Video

```powershell
python generate_short.py --script inputs/my_new_script.txt --topic "New Topic"
```

Each run creates a new output folder automatically.
