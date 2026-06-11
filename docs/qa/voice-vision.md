# QA Report — Voice (STT/TTS) + Vision

Scope: `src/tokoboros/core/voice.py`, `src/tokoboros/tools/voice_tools.py`, `src/tokoboros/tools/vision_tools.py`
Environment: Windows 11, Python 3.14.3, `./.venv/Scripts/python.exe` with `PYTHONPATH=src`, tier `unrestricted`.
Live creds: `OPENAI_API_KEY` (+ `TOKOBOROS_STT_API_KEY` / `TOKOBOROS_TTS_API_KEY`, same key) and `ANTHROPIC_API_KEY`, all sourced from repo-root `.env`.

## Summary

The voice and vision stacks are largely solid and were verified end-to-end against live APIs:

- TTS round-trip works (OpenAI `tts-1` / voice `alloy`, mp3 output, 26,880 bytes), STT (`whisper-1`) transcribes it back faithfully.
- All five env overrides (`TOKOBOROS_TTS_MODEL` / `_VOICE` / `_BASE_URL`, `TOKOBOROS_STT_MODEL` / `_BASE_URL`) are read and honored; one was live-verified to flow through to the API.
- Tier gating is correct: every voice tool and `view_image` is refused in `sandbox`, allowed in `unrestricted`.
- Vision works live: `view_image` packages `image.png` into Anthropic image blocks, and a real `claude-haiku-4-5` call described it correctly. SSRF validation is robust — public HTTPS passes; localhost, `127.0.0.1`, `169.254.169.254`, RFC1918, IPv6 loopback/ULA, DNS-rebinding hostnames (`localtest.me`, `*.nip.io`), `0.0.0.0`, `file://`, and redirect-based bypass attempts are all blocked.

One real functional bug was found (Medium): `get_audio_info` / `probe_audio_info` reports a grossly wrong duration (~24.8 hours for a ~1.3-second clip) for WAV files produced by OpenAI TTS, because it trusts a streaming-header sentinel. One minor interop gap (Low) was also found in the TTS↔STT supported-format sets.

Counts: Medium 1, Low 1. (No Critical/High.)

## Findings

### [Medium] `get_audio_info` reports absurd duration (~24.8h) for OpenAI-TTS WAV files due to streaming-header sentinel

- **Where:** `src/tokoboros/core/voice.py` — `probe_audio_info`, lines 189–199 (specifically `n_frames = wf.getnframes()` → `duration = n_frames / sample_rate`). Surfaced to the agent/user via `get_audio_info` in `src/tokoboros/tools/voice_tools.py` (lines 198–203).
- **Repro (live-verified):**
  1. `TextToSpeech.from_env().synthesize("Wave format round trip", Path("test.wav"))` (OpenAI `tts-1`, `response_format=wav`). File written = 64,844 bytes.
  2. `probe_audio_info("test.wav")` → `duration_seconds = 89478.485`, `sample_rate = 24000`, `channels = 1`.
  3. `get_audio_info` tool output reads: `Duration: 89478.485s | Sample rate: 24,000 Hz | mono`.
  - Real duration = 64,800 data bytes ÷ 48,000 byte_rate = **1.35 s**.
- **Root cause (confirmed by parsing the file header):** OpenAI's `tts-1` WAV stream writes a placeholder header where both the RIFF chunk size and the `data` chunk size are `0xFFFFFFFF` (4294967295), the standard "unknown length" sentinel. Python's stdlib `wave` reads the bogus `data` size and reports `nframes = 2147483647` (0x7FFFFFFF). `89478 s ≈ 0x7FFFFFFF / 24000`. Header bytes observed: `52494646 ffffffff 57415645 ... 64617461 ffffffff` (`RIFF`, size `ffffffff`, `WAVE` … `data`, size `ffffffff`). `fmt ` chunk is valid: PCM, 1 ch, 24000 Hz, byte_rate 48000, 16-bit.
- **Expected:** ~1.35 s reported.
- **Actual:** 89478.485 s (≈24.8 hours).
- **Impact:** `get_audio_info` is keyless and the schema explicitly markets it as "Most useful after speak_text … to check if a file is the right length." Its single headline field (duration) is off by ~66,000×. An agent reasoning about whether a clip is too long, or reporting length to a user, is badly misled. Size/sample-rate/channels remain correct. The existing test (`tests/test_voice_tools.py:309`) only builds WAVs via stdlib `wave.open(..., "wb")`, which always writes correct chunk sizes, so CI never exercises a streaming-header WAV and the bug slips through.
- **Suggested fix:** Don't trust `getnframes()` blindly for duration. Detect the sentinel / impossible frame count and fall back to size-based duration: compute `data_bytes = file_size − header_offset` (or read the `fmt ` `byte_rate`) and `duration = data_bytes / byte_rate`. Concretely, clamp: `max_frames = (file_size − 44) // (sampwidth * channels)`; if `n_frames > max_frames` (or `n_frames == 0x7FFFFFFF`), use `max_frames`. Verified that a correct-header WAV (built via stdlib) probes to exactly 1.0 s, and that `actual_data_bytes / byte_rate` yields the correct 1.35 s for the OpenAI file — so the byte_rate fallback is accurate.

### [Low] TTS can write `.opus` / `.aac` / `.pcm`, but STT rejects those same extensions — `speak_text` → `transcribe_audio`/`play_audio` round-trip breaks for advertised formats

- **Where:** `src/tokoboros/core/voice.py` — `_TTS_FORMATS` (line 59) = `{mp3, opus, aac, flac, wav, pcm}` vs `_SUPPORTED_EXTENSIONS` (lines 63–65) = `{.mp3, .mp4, .mpeg, .mpga, .m4a, .wav, .webm, .flac, .ogg}`. The `speak_text` schema (`voice_tools.py` lines 102–106) explicitly advertises `.opus` and `.aac` as valid output paths.
- **Repro (live-verified):**
  - `TextToSpeech.from_env().synthesize("Round trip opus test", Path("test.opus"))` → succeeds, 7,061 bytes.
  - `SpeechToText.from_env().transcribe("test.opus")` → `VoiceError: unsupported audio format '.opus'. Supported: .flac, .m4a, .mp3, .mp4, .mpeg, .mpga, .ogg, .wav, .webm`.
  - Same for `.aac` (8,775 bytes written, then rejected). `.pcm` is also writable but unsupported by STT.
- **Expected:** A format the agent was told it can synthesize to should be readable by the sibling STT/playback tools, so a "speak it, then transcribe/play it back" flow works for any advertised format.
- **Actual:** `.opus`, `.aac`, `.pcm` are write-only dead ends. Note the format-name vs extension mismatch is the trap: TTS `opus` maps to a `.opus` file, but Whisper's accepted container is `.ogg` (Opus-in-Ogg), which STT lists — yet `.opus` is not listed. So even the conceptually-equivalent format is rejected on the extension string.
- **Impact:** Low — mp3/wav/flac (the common cases) all round-trip fine; this only bites if the agent picks `.opus`/`.aac`/`.pcm`. But because `speak_text`'s own description suggests `.opus` / `.aac`, an agent can reasonably produce a file it then cannot transcribe or (for non-WAV) play.
- **Suggested fix:** Reconcile the two sets. Minimal: add `.opus` and `.aac` to `_SUPPORTED_EXTENSIONS` (Whisper accepts both). Optionally drop/flag `pcm` in the `speak_text` schema since a raw `.pcm` has no decodable container for STT or `winsound` playback. Alternatively, narrow the `speak_text` schema's advertised formats to those that round-trip.

## What passed

- **TTS synthesize (live):** `TextToSpeech.from_env()` → model `tts-1`, voice `alloy`, base_url `https://api.openai.com/v1`. `synthesize("Tokoboros quality test", test.mp3)` wrote 26,880 non-empty bytes; first bytes `fff3e4c4` = valid MP3 frame header. No error.
- **STT transcribe / round-trip (live):** `SpeechToText.from_env()` → model `whisper-1`. Transcribed the generated mp3 back as `"Tocoboro's quality test."` — a faithful phonetic match (Whisper spelled the coined word "Tokoboros" reasonably). mp3/wav both round-trip; WAV transcript `"Wave Format Round Trip."`. Not a bug.
- **Overrides (code + live):** `TextToSpeech.from_env` / `SpeechToText.from_env` correctly read `TOKOBOROS_TTS_MODEL`, `TOKOBOROS_TTS_VOICE`, `TOKOBOROS_TTS_BASE_URL`, `TOKOBOROS_STT_MODEL`, `TOKOBOROS_STT_BASE_URL` (all five honored when set; defaults used otherwise). Live-verified `TOKOBOROS_TTS_VOICE=echo` flowed to the API and produced 24,480 bytes. STT/TTS also honor `OPENAI_API_KEY` / `OPENAI_BASE_URL` and the `TOKOBOROS_REST_ALLOW_PRIVATE` flag.
- **Tier gating (live):** With a `sandbox`-tier gate, `speak_text`, `transcribe_audio`, `transcribe_and_summarize`, `play_audio`, `get_audio_info`, and `view_image` all returned `Permission Denied: … disabled in 'sandbox' tier.` With `unrestricted`, the keyless `get_audio_info` (and `view_image`) proceeded past the tier gate.
- **Vision local file (live):** `view_image("image.png")` with the Anthropic provider returned a `ToolResultBlocks` (`image` + `text` blocks, `media_type=image/png`, 57 KB, base64 len 78,696). Feeding those blocks to a live `claude-haiku-4-5` call yielded an accurate description ("a terminal output displaying an error message from an AI agent system called 'Tokoboros' … `ANTHROPIC_API_KEY` … not configured").
- **Vision non-Anthropic provider:** with `provider_kind` in {openai, ollama} (case-insensitive), `view_image` correctly returned a plain-text "requires the Anthropic provider" notice instead of an Anthropic-shaped block; `anthropic`/`ANTHROPIC` returned image blocks.
- **Vision SSRF (live):** `view_image` / `validate_outbound_url` blocked `http://127.0.0.1/x.png`, `http://169.254.169.254/...`, `http://localhost/x.png`, `http://10.0.0.1/...`, IPv6 `[::1]` and `[fd00::1]`, `http://0.0.0.0/...`, DNS-rebinding hosts `localtest.me` and `127.0.0.1.nip.io` (both resolve to 127.0.0.1), and `file:///etc/passwd` (scheme). A real public image `https://httpbin.org/image/png` passed (8,090 bytes, image/png). A 302 redirect (`follow_redirects=False`) surfaced as "HTTP 302" rather than following it (redirect-SSRF closed); a non-image public URL gave a clean content-type error. No crashes on any path.
- **Test suite:** `pytest -k "voice or vision or net_guard"` → 75 passed, 0 failed.
