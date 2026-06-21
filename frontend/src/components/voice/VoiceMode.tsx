// VoiceMode — realtime conversational voice overlay for Hachi.
//
// State machine:
//   idle → listening → transcribing → waiting → speaking → idle
//
// Features:
//   - Continuous mode: Web Audio RMS silence detection auto-ends utterances.
//   - Push-to-talk: hold the mic button to record, release to transcribe.
//   - Barge-in: starting to speak stops current assistant audio.
//   - Graceful gateway fallback: on VoiceConfigError or network failure,
//     onConfigUnavailable() is called so the parent can fall back to the
//     browser Web Speech API.
//
// Props:
//   open            — controls overlay visibility
//   onClose         — called when user dismisses
//   onTranscript    — called with the recognised text (send it as a message)
//   assistantText   — latest assistant reply; when non-empty the overlay
//                     synthesises it via the gateway TTS
//   onConfigUnavailable — optional callback when gateway voice is unconfigured

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { transcribeAudio, speakText } from '../../lib/data/domains/voice'

// ---- Types ----

type VoicePhase = 'idle' | 'listening' | 'transcribing' | 'waiting' | 'speaking'

interface VoiceModeProps {
  open: boolean
  onClose: () => void
  onTranscript: (text: string) => void
  /** When non-empty, VoiceMode will TTS this text automatically. */
  assistantText?: string
  /** Called when gateway voice is not configured so the parent can fall back. */
  onConfigUnavailable?: () => void
}

// ---- Constants ----

// RMS threshold below which we consider silence (0–255 scale of Uint8 analyser).
const SILENCE_THRESHOLD = 12
// How many consecutive silent frames before we consider the utterance done.
const SILENCE_HANGOVER_FRAMES = 40  // ~1s at 25 fps analyser poll

// ---- VoiceMode component ----

export default function VoiceMode({
  open,
  onClose,
  onTranscript,
  assistantText,
  onConfigUnavailable,
}: VoiceModeProps) {
  const [phase, setPhase] = useState<VoicePhase>('idle')
  const [statusText, setStatusText] = useState('Tap the mic to start')
  const [errorText, setErrorText] = useState<string | null>(null)
  const [pushToTalk, setPushToTalk] = useState(false)

  // Refs that survive re-renders without causing them.
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const silenceCountRef = useRef(0)
  const rafRef = useRef<number | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const currentAudioRef = useRef<HTMLAudioElement | null>(null)
  const currentObjUrlRef = useRef<string | null>(null)
  const phaseRef = useRef<VoicePhase>('idle')
  const isSpeakingByUserRef = useRef(false)  // barge-in detection

  // Keep phaseRef in sync so closures always see current phase.
  useEffect(() => { phaseRef.current = phase }, [phase])

  // ---- Close / cleanup ----

  const cleanup = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try { mediaRecorderRef.current.stop() } catch { /* ignore */ }
    }
    if (audioContextRef.current) {
      try { audioContextRef.current.close() } catch { /* ignore */ }
      audioContextRef.current = null
      analyserRef.current = null
    }
    if (currentAudioRef.current) {
      currentAudioRef.current.pause()
      currentAudioRef.current = null
    }
    if (currentObjUrlRef.current) {
      URL.revokeObjectURL(currentObjUrlRef.current)
      currentObjUrlRef.current = null
    }
  }, [])

  // Cleanup when overlay closes.
  useEffect(() => {
    if (!open) {
      cleanup()
      setPhase('idle')
      setStatusText('Tap the mic to start')
      setErrorText(null)
      chunksRef.current = []
    }
  }, [open, cleanup])

  // ---- Stop assistant speech on barge-in ----

  const stopAssistantAudio = useCallback(() => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause()
      currentAudioRef.current = null
    }
    if (currentObjUrlRef.current) {
      URL.revokeObjectURL(currentObjUrlRef.current)
      currentObjUrlRef.current = null
    }
  }, [])

  // ---- Transcription ----

  const handleRecordingStop = useCallback(async () => {
    if (phaseRef.current === 'idle') return
    const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
    chunksRef.current = []

    if (blob.size < 1000) {
      // Too short — likely just noise.
      setPhase('idle')
      setStatusText('Tap the mic to start')
      return
    }

    setPhase('transcribing')
    setStatusText('Transcribing…')
    setErrorText(null)

    try {
      const result = await transcribeAudio(blob)
      if (result.text.trim()) {
        setPhase('waiting')
        setStatusText('Waiting for Hachi…')
        onTranscript(result.text.trim())
      } else {
        setPhase('idle')
        setStatusText('Nothing heard — tap to try again')
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Transcription failed'
      // 503 = gateway voice not configured
      if (err instanceof Error && msg.includes('503')) {
        onConfigUnavailable?.()
        onClose()
        return
      }
      setErrorText(msg)
      setPhase('idle')
      setStatusText('Tap the mic to start')
    }
  }, [onTranscript, onClose, onConfigUnavailable])

  // ---- Silence detection loop ----

  const pollSilence = useCallback(() => {
    if (!analyserRef.current || phaseRef.current !== 'listening') return

    const data = new Uint8Array(analyserRef.current.frequencyBinCount)
    analyserRef.current.getByteTimeDomainData(data)

    // Compute RMS of the signal (deviation from 128 = silence baseline).
    let sum = 0
    for (let i = 0; i < data.length; i++) {
      const d = data[i] - 128
      sum += d * d
    }
    const rms = Math.sqrt(sum / data.length)

    if (rms < SILENCE_THRESHOLD) {
      silenceCountRef.current++
    } else {
      silenceCountRef.current = 0
      isSpeakingByUserRef.current = true
    }

    if (silenceCountRef.current >= SILENCE_HANGOVER_FRAMES && isSpeakingByUserRef.current) {
      // Utterance ended — stop recorder; handleRecordingStop fires via ondataavailable.
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        mediaRecorderRef.current.stop()
      }
      return
    }

    rafRef.current = requestAnimationFrame(pollSilence)
  }, [])

  // ---- Start recording ----

  const startListening = useCallback(async () => {
    stopAssistantAudio()
    setErrorText(null)
    chunksRef.current = []
    silenceCountRef.current = 0
    isSpeakingByUserRef.current = false

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setErrorText('Microphone access denied')
      return
    }

    // Set up analyser for silence detection.
    const ctx = new AudioContext()
    audioContextRef.current = ctx
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 512
    analyserRef.current = analyser
    const source = ctx.createMediaStreamSource(stream)
    source.connect(analyser)

    // Set up MediaRecorder.
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
      ? 'audio/webm'
      : 'audio/ogg'

    const recorder = new MediaRecorder(stream, { mimeType })
    mediaRecorderRef.current = recorder

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data)
    }
    recorder.onstop = () => {
      // Release mic tracks.
      stream.getTracks().forEach((t) => t.stop())
      void handleRecordingStop()
    }

    recorder.start(200)  // Collect data every 200ms.
    setPhase('listening')
    setStatusText('Listening…  (speak, then pause)')

    // Start silence poll.
    rafRef.current = requestAnimationFrame(pollSilence)
  }, [stopAssistantAudio, handleRecordingStop, pollSilence])

  // ---- Stop recording (push-to-talk release) ----

  const stopListening = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop()
    }
  }, [])

  // ---- TTS: speak assistant reply ----

  useEffect(() => {
    if (!open || !assistantText) return
    // Only auto-speak when we're in 'waiting' or 'idle' phases.
    if (phaseRef.current !== 'waiting' && phaseRef.current !== 'idle') return

    async function playAssistant() {
      setPhase('speaking')
      setStatusText('Hachi is speaking…')
      try {
        const objUrl = await speakText(assistantText!, { format: 'mp3' })
        currentObjUrlRef.current = objUrl
        const audio = new Audio(objUrl)
        currentAudioRef.current = audio

        await new Promise<void>((resolve) => {
          audio.onended = () => resolve()
          audio.onerror = () => resolve()
          audio.play().catch(() => resolve())
        })
      } catch {
        // TTS failure is non-fatal — fall through to idle.
      } finally {
        if (currentObjUrlRef.current) {
          URL.revokeObjectURL(currentObjUrlRef.current)
          currentObjUrlRef.current = null
        }
        currentAudioRef.current = null
        setPhase('idle')
        setStatusText('Tap the mic to continue')
      }
    }

    void playAssistant()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistantText, open])

  // ---- Barge-in: user taps mic while assistant is speaking ----

  function handleMicClick() {
    if (phase === 'speaking') {
      stopAssistantAudio()
      void startListening()
      return
    }
    if (phase === 'listening') {
      stopListening()
      return
    }
    if (phase === 'idle') {
      void startListening()
    }
  }

  function handleMicPointerDown() {
    if (!pushToTalk) return
    if (phase !== 'listening') void startListening()
  }

  function handleMicPointerUp() {
    if (!pushToTalk) return
    if (phase === 'listening') stopListening()
  }

  if (!open) return null

  // ---- Visual: mic ring animation based on phase ----

  const micColor =
    phase === 'listening'
      ? 'var(--hachi-danger)'
      : phase === 'speaking'
      ? 'var(--hachi-trust)'
      : phase === 'transcribing' || phase === 'waiting'
      ? 'var(--hachi-working)'
      : 'var(--hachi-brand)'

  const isPulsing = phase === 'listening' || phase === 'speaking'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Voice mode"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(28, 26, 32, 0.72)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9000,
        backdropFilter: 'blur(6px)',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        style={{
          background: 'var(--hachi-surface)',
          border: '0.5px solid var(--hachi-line)',
          borderRadius: 24,
          padding: '40px 32px 32px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 28,
          minWidth: 300,
          maxWidth: 380,
          boxShadow: '0 24px 64px rgba(28,26,32,0.18)',
        }}
      >
        {/* Close button */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close voice mode"
          style={{
            position: 'absolute',
            top: 16,
            right: 16,
            width: 32,
            height: 32,
            borderRadius: 8,
            border: 'none',
            background: 'transparent',
            color: 'var(--hachi-muted)',
            cursor: 'pointer',
            display: 'grid',
            placeItems: 'center',
            fontSize: 18,
          }}
          onMouseEnter={(e) => {
            ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)'
          }}
          onMouseLeave={(e) => {
            ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
          }}
        >
          ×
        </button>

        {/* Mic visualiser */}
        <div style={{ position: 'relative', width: 88, height: 88 }}>
          {isPulsing && (
            <div
              style={{
                position: 'absolute',
                inset: -8,
                borderRadius: '50%',
                background: micColor,
                opacity: 0.18,
                animation: 'voicePulse 1.2s ease-in-out infinite',
              }}
            />
          )}
          <button
            type="button"
            aria-label={phase === 'listening' ? 'Stop listening' : 'Start listening'}
            onClick={handleMicClick}
            onPointerDown={handleMicPointerDown}
            onPointerUp={handleMicPointerUp}
            style={{
              width: 88,
              height: 88,
              borderRadius: '50%',
              border: `2px solid ${micColor}`,
              background:
                phase === 'listening'
                  ? 'var(--hachi-danger-tint)'
                  : phase === 'speaking'
                  ? 'var(--hachi-trust-tint)'
                  : 'var(--hachi-canvas)',
              color: micColor,
              cursor:
                phase === 'transcribing' || phase === 'waiting' ? 'default' : 'pointer',
              display: 'grid',
              placeItems: 'center',
              fontSize: 30,
              transition: 'all 0.15s ease',
            }}
          >
            {phase === 'transcribing' || phase === 'waiting' ? '…' : '🎤'}
          </button>
        </div>

        {/* Status */}
        <div
          style={{
            fontSize: 15,
            color: 'var(--hachi-ink)',
            fontWeight: 500,
            textAlign: 'center',
            lineHeight: 1.4,
          }}
        >
          {statusText}
        </div>

        {/* Error */}
        {errorText && (
          <div
            style={{
              fontSize: 13,
              color: 'var(--hachi-danger)',
              textAlign: 'center',
              background: 'var(--hachi-danger-tint)',
              borderRadius: 10,
              padding: '8px 14px',
            }}
          >
            {errorText}
          </div>
        )}

        {/* Push-to-talk toggle */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            fontSize: 13,
            color: 'var(--hachi-muted)',
          }}
        >
          <label
            style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer' }}
          >
            <input
              type="checkbox"
              checked={pushToTalk}
              onChange={(e) => setPushToTalk(e.target.checked)}
              style={{ cursor: 'pointer' }}
            />
            Push-to-talk mode
          </label>
        </div>
      </div>

      {/* Inline keyframe for pulse animation — scoped to this portal */}
      <style>{`
        @keyframes voicePulse {
          0%   { transform: scale(1);   opacity: 0.18; }
          50%  { transform: scale(1.18); opacity: 0.08; }
          100% { transform: scale(1);   opacity: 0.18; }
        }
      `}</style>
    </div>
  )
}
