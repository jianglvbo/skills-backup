---
name: douyin-video-summary
description: >
  Summarize Douyin (TikTok China) videos by extracting audio, transcribing with whisper.cpp, and generating structured summaries.
  Use when a user shares a Douyin link and wants a text summary of the video content. Supports optional sync to Feishu (Lark) docs.
  Triggers on Douyin URLs (v.douyin.com, douyin.com/video/).
  Exclusions: non-Douyin sources go to their dedicated skills; if the summarized content needs knowledge-base refinement, hand off to the investment-framework skill (refine stage).
agent_created: true
---

# Douyin Video Summary

Summarize Douyin videos: extract audio → transcribe locally → AI summary.

## Default Stance

### Core Principles

- **Browser interception for audio**: Douyin blocks direct downloads (yt-dlp/aria2c → 403); use browser network interception to capture the audio URL, then curl with Referer header
- **Local transcription**: whisper.cpp with `ggml-small.bin` (best speed/quality tradeoff; `medium` may OOM on 8GB)
- **Structured summary output**: title/duration/publish date + core message + numbered points + one-line takeaway
- **Clean up after**: remove downloaded audio/wav files after processing

### Prohibitions

- Never use aria2c/yt-dlp on Douyin CDN URLs (guaranteed 403) — always curl with `Referer: https://www.douyin.com/`
- Never skip the setup step (whisper-cpp/ffmpeg/model must be installed)
- Never treat short-video brevity as failure (sub-1min summaries are naturally short)
- Never upload/redistribute the audio or transcription without user intent

---

## Workflow

When a Douyin link is received:

### Step 1: Extract Video ID

Parse the Douyin URL to get the video ID. Two formats:
- Short link: `https://v.douyin.com/xxxxx/` → follow redirect to get video ID
- Direct link: `https://www.douyin.com/video/7604713801732365681`

```bash
curl -sL -o /dev/null -w '%{url_effective}' 'https://v.douyin.com/xxxxx/' | grep -oE '[0-9]{15,}'
```

### Step 2: Get Audio via Browser

1. Open the Douyin video page in the browser
2. Inject JS to intercept network requests before navigation:

```javascript
window.__audioUrls = [];
const origOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url) {
  if (url && (url.includes('.mp3') || url.includes('.m4a') || url.includes('mime_type=audio'))) {
    window.__audioUrls.push(url);
  }
  return origOpen.apply(this, arguments);
};
```

3. Navigate to the video page, click play to trigger audio loading
4. Retrieve intercepted URLs: `window.__audioUrls`
5. Download with curl (Referer required):

```bash
curl -H "Referer: https://www.douyin.com/" -o audio.mp4 "<audio_url>"
```

### Step 3: Convert to WAV

```bash
ffmpeg -i audio.mp4 -ar 16000 -ac 1 -c:a pcm_s16le audio.wav
```

### Step 4: Transcribe with whisper.cpp

```bash
whisper-cli -m ~/.skills-manager/skills/douyin-video-summary/models/ggml-small.bin -l zh -f audio.wav -otxt -of output
```

- `-l zh` for Chinese content (auto-detect if unsure)
- Apple Silicon GPU acceleration is automatic (Metal)
- Performance: ~20s for 5min audio on M4

### Step 5: Generate Summary

Read the transcription and produce a structured summary:

```
📹 **[Video Title] | [Author]**
时长：X分X秒 | 发布：YYYY-MM-DD

🎯 **核心观点：[one-line core message]**

**1. [Point 1 title]**
• [detail]
• [detail]

**2. [Point 2 title]**
• [detail]

💬 **一句话总结：[concise takeaway]**
```

### Step 6 (Optional): Sync to Feishu Doc

If Feishu integration is configured, append the summary to a Feishu document using the Feishu Open API. See `references/feishu-sync.md` for API details.

---

## Output Format

| Field | Type | Description |
|:---|:---|:---|
| video_title | string | Video title |
| author | string | Video author |
| duration | string | Length (X分X秒) |
| publish_date | string | YYYY-MM-DD |
| core_message | string | One-line core message |
| points | list[string] | Numbered key points |
| takeaway | string | One-line summary |
| feishu_synced | boolean | Whether synced to Feishu (optional) |

---

## Relative Files

| Scenario | Load | Content | Mode |
|:---|:---|:---|:---|
| Setup | scripts/setup.sh | Install whisper-cpp/ffmpeg + download model | **Execute** |
| Feishu sync | references/feishu-sync.md | Feishu Open API details | Read |

---

## Source Hierarchy

| Priority | Source |
|:---|:---|
| 1 | Browser-intercepted audio URL (actual video source) |
| 2 | whisper.cpp transcription output |
| 3 | references/feishu-sync.md (optional sync config) |

---

## Self-Check

- [ ] Video ID extracted correctly (short link followed redirect)?
- [ ] Audio downloaded via curl with Referer (not aria2c/yt-dlp)?
- [ ] Transcription produced (whisper-cli ran successfully)?
- [ ] Summary follows the structured format (core message + points + takeaway)?
- [ ] Temporary audio/wav files cleaned up?
- [ ] Not treating sub-1min brevity as failure?

---

## Tips

- Short videos (<1min): summary may be very brief — that's fine
- If browser interception fails, retry once (Douyin pages sometimes need a second load)
- Clean up downloaded audio/wav files after processing to save disk space
- `small` model is the best speed/quality tradeoff; `medium` may OOM on 8GB machines
