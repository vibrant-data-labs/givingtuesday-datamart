---
name: placeholder-walkthrough-video
description: The narrated video walkthrough of the placeholder-recovery results doc (2026-09-25) — where it lives, how it was built, how to rebuild it after the doc changes
metadata:
  type: project
---

Zein asked (2026-09-25) for an audio/visual demo of the results doc for
colleagues who struggle with long documents. Delivered as an MP4 (12 chapters,
5 min 54 s at the 1.3x pace Zein chose, 1080p, 6.9 MB) and a hosting page with chapters, captions and a
transcript: https://claude.ai/artifact/8wQYgKTZmJknTotUxAooTA (private until
Zein shares it). Build lives in the session scratchpad
(`58b5f861-…/scratchpad/demo`): `build.py` (12 HTML slides + narration text,
every number from the doc at rev 60), headless Chrome renders the slides
(`--headless=new --screenshot`, one per call; Chrome hangs after writing, so
wrap in `timeout 60`), `tts.py` (OpenAI `tts-1-hd`, voice nova, speed 1.26 (Zein found 0.97 slow),
$0.20 for 6,616 chars; OPENAI_API_KEY is in Zein's shell env), `captions.py`
(sentence cues timed by character share; chapters.json; transcript.json),
`assemble.sh` (ffmpeg, 10 fps stillimage H.264 crf 23 + mono AAC 96k, `-t`
= clip + 0.8 s per segment; `-shortest` overran by 5 s a segment), the page
template with cues inlined via `addTextTrack` (artifacts do not serve .vtt).

Zein kept nova after hearing a sampler of all 11 voices (`voice_sampler.mp3`);
he asked for a Barack Obama voice, declined (real-person imitation).

**Why:** the doc's numbers change; the video must be rebuilt, not patched.
**How to apply:** after a doc revision, edit the slide text/narration in
`build.py`, re-run build → render → tts (only changed clips; tts.py skips
existing mp3s, delete the changed ones) → captions → assemble, then republish
`page.html` with `files` walkthrough.mp4 + poster.png to the same URL. The
hosted document's widget sandbox refuses iframes and artifacts cannot embed other
sites, so the video is the shareable visual, not an embed. See
[[placeholder-frame-run-session4]] and [[placeholder-docs-layout]].
