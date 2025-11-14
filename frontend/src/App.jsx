import { useState, useEffect, useRef } from 'react'
import './App.css'

const SAMPLE_RATE = 24000
const CHANNELS = 1

function App() {
  const [isConnected, setIsConnected] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const [isRecording, setIsRecording] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState('')
  
  const wsRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const audioContextRef = useRef(null)
  const clientIdRef = useRef(`client_${Date.now()}`)
  const isPlayingRef = useRef(false)
  const currentItemIdRef = useRef(null)
  const pendingChunksRef = useRef(new Map()) // Map<itemId, Uint8Array[]>
  const playTimeoutRef = useRef(null)
  const currentSourceRef = useRef(null)
  const isInterruptedRef = useRef(false)
  const gainNodeRef = useRef(null)
  const canSendAudioRef = useRef(false)
  const audioGenerationRef = useRef(0) // Track audio generation - increments on each interrupt
  const validItemIdsRef = useRef(new Set()) // Track valid item IDs for current generation

  useEffect(() => {
    // Generate unique client ID
    clientIdRef.current = `client_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    
    // Connect to WebSocket
    const ws = new WebSocket(`ws://localhost:8000/ws/${clientIdRef.current}`)
    wsRef.current = ws

    ws.onopen = () => {
      console.log('WebSocket connected')
      setIsConnected(true)
      setError('')
    }

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleWebSocketMessage(data)
    }

    ws.onerror = (error) => {
      console.error('WebSocket error:', error)
      setError('Connection error. Make sure the backend server is running.')
    }

    ws.onclose = () => {
      console.log('WebSocket disconnected')
      setIsConnected(false)
    }

    // Initialize audio context for playback
    audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: SAMPLE_RATE
    })
    
    // Create a gain node for volume control and quick muting
    gainNodeRef.current = audioContextRef.current.createGain()
    gainNodeRef.current.connect(audioContextRef.current.destination)

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close()
      }
      if (playTimeoutRef.current) {
        clearTimeout(playTimeoutRef.current)
      }
      if (currentSourceRef.current) {
        try {
          currentSourceRef.current.stop()
        } catch (e) {
          // Source might already be stopped
        }
      }
      if (audioContextRef.current) {
        audioContextRef.current.close()
      }
      // Clear all pending chunks
      pendingChunksRef.current.clear()
    }
  }, [])

  const handleWebSocketMessage = (data) => {
    switch (data.type) {
      case 'connection_ready':
        console.log('Realtime API connected')
        break
      
      case 'session_created':
        setSessionId(data.session_id)
        break
      
      case 'recording_started':
        setIsRecording(true)
        // Reset interrupt flag when new recording starts
        isInterruptedRef.current = false
        // Increment generation - this invalidates all previous audio
        audioGenerationRef.current += 1
        // Clear valid item IDs - we'll only accept new ones for this generation
        validItemIdsRef.current.clear()
        // Allow sending audio now that buffer is cleared and recording confirmed
        canSendAudioRef.current = true
        // Restore audio volume
        if (gainNodeRef.current) {
          gainNodeRef.current.gain.setValueAtTime(1, audioContextRef.current.currentTime)
        }
        break
      
      case 'recording_stopped':
        setIsRecording(false)
        // Stop sending audio when recording stops
        canSendAudioRef.current = false
        break
      
      case 'hard_stopped':
        // Backend confirmed hard stop - ensure everything is stopped
        interruptAudio()
        if (isRecording) {
          setIsRecording(false)
        }
        break
      
      case 'transcript_delta':
        setTranscript(data.text)
        break
      
      case 'audio_delta':
        // Only queue audio if we're not interrupted
        if (!isInterruptedRef.current) {
          // Track this item_id as valid for current generation
          // This ensures we only accept audio from responses that started after the last interrupt
          validItemIdsRef.current.add(data.item_id)
          queueAudioChunk(data.item_id, data.delta)
        }
        // If interrupted, ignore all audio until recording_started resets the flag
        break
      
      default:
        console.log('Received message:', data)
    }
  }

  const queueAudioChunk = (itemId, base64Audio) => {
    try {
      // Decode base64 to get PCM16 audio data
      const binaryString = atob(base64Audio)
      const audioData = new Uint8Array(binaryString.length)
      for (let i = 0; i < binaryString.length; i++) {
        audioData[i] = binaryString.charCodeAt(i)
      }

      // Store chunks by item_id
      if (!pendingChunksRef.current.has(itemId)) {
        pendingChunksRef.current.set(itemId, [])
      }
      pendingChunksRef.current.get(itemId).push(audioData)

      // If this is a new item and we're currently playing, mark the transition
      if (itemId !== currentItemIdRef.current && isPlayingRef.current) {
        // Current item will finish playing, then we'll start the new one
        currentItemIdRef.current = itemId
      } else if (itemId !== currentItemIdRef.current && !isPlayingRef.current) {
        // Not playing, so we can start immediately with new item
        currentItemIdRef.current = itemId
        schedulePlayback()
      } else if (itemId === currentItemIdRef.current && !isPlayingRef.current) {
        // Same item, not playing - schedule playback
        schedulePlayback()
      }
      // If same item and playing, chunks will be picked up when current playback finishes
    } catch (error) {
      console.error('Error queuing audio chunk:', error)
    }
  }

  const schedulePlayback = () => {
    // Clear any existing timeout
    if (playTimeoutRef.current) {
      clearTimeout(playTimeoutRef.current)
      playTimeoutRef.current = null
    }

    // If already playing, don't schedule
    if (isPlayingRef.current) {
      return
    }

    // Ensure we have a current item
    if (!currentItemIdRef.current) {
      // Find any item with chunks
      const itemIds = Array.from(pendingChunksRef.current.keys())
      const nextItemId = itemIds.find(id => {
        const itemChunks = pendingChunksRef.current.get(id)
        return itemChunks && itemChunks.length > 0
      })
      if (nextItemId) {
        currentItemIdRef.current = nextItemId
      } else {
        return
      }
    }

    // Get chunks for current item
    const chunks = pendingChunksRef.current.get(currentItemIdRef.current)
    if (!chunks || chunks.length === 0) {
      return
    }

    // If we have enough chunks (or wait a bit for more), start playing
    // Play immediately if we have multiple chunks, or wait a short time for more
    if (chunks.length >= 3) {
      // We have enough chunks, play immediately
      playBufferedAudio()
    } else {
      // Wait a bit to accumulate more chunks for smoother playback
      playTimeoutRef.current = setTimeout(() => {
        playTimeoutRef.current = null
        if (!isPlayingRef.current) {
          playBufferedAudio()
        }
      }, 50) // 50ms delay to accumulate chunks
    }
  }

  const playBufferedAudio = async () => {
    // Prevent concurrent playback
    if (isPlayingRef.current) {
      return
    }

    // Clear any pending timeout
    if (playTimeoutRef.current) {
      clearTimeout(playTimeoutRef.current)
      playTimeoutRef.current = null
    }

    // Get chunks for current item
    const chunks = pendingChunksRef.current.get(currentItemIdRef.current)
    if (!chunks || chunks.length === 0) {
      return
    }

    // Remove chunks from pending (we'll play them now)
    const chunksToPlay = chunks.splice(0, chunks.length)
    
    if (chunksToPlay.length === 0) {
      return
    }

    isPlayingRef.current = true

    try {
      // Combine all chunks into one continuous buffer
      const totalLength = chunksToPlay.reduce((sum, arr) => sum + arr.length, 0)
      if (totalLength === 0) {
        isPlayingRef.current = false
        checkAndContinuePlayback()
        return
      }

      const combinedData = new Uint8Array(totalLength)
      let offset = 0
      for (const arr of chunksToPlay) {
        combinedData.set(arr, offset)
        offset += arr.length
      }

      const frameCount = combinedData.length / 2
      if (frameCount === 0) {
        isPlayingRef.current = false
        checkAndContinuePlayback()
        return
      }

      // Ensure audio context is running (required by some browsers)
      if (audioContextRef.current.state === 'suspended') {
        await audioContextRef.current.resume()
      }

      const audioBuffer = audioContextRef.current.createBuffer(
        CHANNELS,
        frameCount,
        SAMPLE_RATE
      )
      
      const channelData = audioBuffer.getChannelData(0)
      const dataView = new DataView(combinedData.buffer)
      for (let i = 0; i < frameCount; i++) {
        const sample = dataView.getInt16(i * 2, true)
        channelData[i] = sample / 32768.0
      }
      
      const source = audioContextRef.current.createBufferSource()
      currentSourceRef.current = source
      source.buffer = audioBuffer
      // Connect through gain node for volume control
      source.connect(gainNodeRef.current)
      
      // Restore volume if it was muted
      if (gainNodeRef.current.gain.value === 0) {
        gainNodeRef.current.gain.setValueAtTime(1, audioContextRef.current.currentTime)
      }
      
      // When this buffer finishes, check if there's more to play
      source.onended = () => {
        currentSourceRef.current = null
        isPlayingRef.current = false
        checkAndContinuePlayback()
      }
      
      source.onerror = (error) => {
        console.error('Audio source error:', error)
        currentSourceRef.current = null
        isPlayingRef.current = false
        checkAndContinuePlayback()
      }
      
      source.start(0)
    } catch (error) {
      console.error('Error playing audio:', error)
      currentSourceRef.current = null
      isPlayingRef.current = false
      // Retry after a short delay
      setTimeout(() => {
        checkAndContinuePlayback()
      }, 10)
    }
  }

  const checkAndContinuePlayback = () => {
    // Clean up empty item entries
    for (const [itemId, chunks] of pendingChunksRef.current.entries()) {
      if (!chunks || chunks.length === 0) {
        pendingChunksRef.current.delete(itemId)
      }
    }

    // Check if there are more chunks for the current item
    if (currentItemIdRef.current) {
      const chunks = pendingChunksRef.current.get(currentItemIdRef.current)
      if (chunks && chunks.length > 0) {
        // More chunks available, continue playing
        schedulePlayback()
        return
      }
    }

    // Check if there's a new item waiting
    const itemIds = Array.from(pendingChunksRef.current.keys())
    if (itemIds.length > 0) {
      // Find the next item with chunks
      const nextItemId = itemIds.find(id => {
        const itemChunks = pendingChunksRef.current.get(id)
        return itemChunks && itemChunks.length > 0
      })
      
      if (nextItemId) {
        currentItemIdRef.current = nextItemId
        schedulePlayback()
      }
    }
  }

  const interruptAudio = () => {
    // Set interrupt flag immediately to prevent new chunks from being queued
    isInterruptedRef.current = true
    // Stop sending audio chunks immediately
    canSendAudioRef.current = false
    // Increment generation to invalidate all current audio
    audioGenerationRef.current += 1
    // Clear valid item IDs - they're from the old generation
    validItemIdsRef.current.clear()

    // Mute audio output immediately using gain node
    if (gainNodeRef.current) {
      try {
        gainNodeRef.current.gain.setValueAtTime(0, audioContextRef.current.currentTime)
      } catch (e) {
        console.error('Error muting audio:', e)
      }
    }

    // Stop current audio playback immediately
    if (currentSourceRef.current) {
      try {
        // Stop immediately (no fade out)
        currentSourceRef.current.stop(0)
        currentSourceRef.current.disconnect()
      } catch (e) {
        // Source might already be stopped or not started
      }
      currentSourceRef.current = null
    }

    // Clear any pending timeouts
    if (playTimeoutRef.current) {
      clearTimeout(playTimeoutRef.current)
      playTimeoutRef.current = null
    }

    // Stop playback flag
    isPlayingRef.current = false

    // Clear all pending audio chunks
    pendingChunksRef.current.clear()
    currentItemIdRef.current = null

    // Clear transcript when interrupting
    setTranscript('')
  }

  const startRecording = async () => {
    try {
      // Interrupt any ongoing audio playback immediately
      interruptAudio()

      // Stop sending audio until backend confirms buffer is cleared
      canSendAudioRef.current = false

      // Notify backend to cancel response, clear buffer, and enable recording
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'start_recording' }))
      }

      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          channelCount: CHANNELS,
          sampleRate: SAMPLE_RATE,
          echoCancellation: true,
          noiseSuppression: true,
        } 
      })

      // Use AudioContext to capture and convert audio to PCM16
      const audioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: SAMPLE_RATE
      })
      const source = audioContext.createMediaStreamSource(stream)
      const processor = audioContext.createScriptProcessor(4096, CHANNELS, CHANNELS)

      let recordingActive = true
      processor.onaudioprocess = (e) => {
        if (!recordingActive) return

        const inputData = e.inputBuffer.getChannelData(0)
        const pcm16 = new Int16Array(inputData.length)
        
        // Convert Float32 to PCM16
        for (let i = 0; i < inputData.length; i++) {
          const s = Math.max(-1, Math.min(1, inputData[i]))
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF
        }

        // Convert to base64
        const base64Audio = btoa(
          String.fromCharCode.apply(null, new Uint8Array(pcm16.buffer))
        )

        // Only send audio if we're allowed to (recording confirmed started)
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && canSendAudioRef.current) {
          wsRef.current.send(JSON.stringify({
            type: 'audio_chunk',
            audio: base64Audio
          }))
        }
      }

      // Store stop function
      const stopFn = () => {
        recordingActive = false
      }

      source.connect(processor)
      processor.connect(audioContext.destination)

      // Store references for cleanup
      mediaRecorderRef.current = {
        stream,
        audioContext,
        processor,
        source,
        stopFn
      }

      // Send start recording message and update state
      setIsRecording(true)
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'start_recording' }))
      }
    } catch (err) {
      console.error('Error starting recording:', err)
      setError('Failed to access microphone. Please check permissions.')
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current) {
      const { stream, audioContext, processor, source, stopFn } = mediaRecorderRef.current
      
      // Stop recording flag
      if (stopFn) stopFn()
      
      // Disconnect and cleanup
      if (processor) processor.disconnect()
      if (source) source.disconnect()
      if (audioContext) audioContext.close()
      if (stream) {
        stream.getTracks().forEach(track => track.stop())
      }
      
      mediaRecorderRef.current = null
      setIsRecording(false)
      
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'stop_recording' }))
      }
    }
  }

  const toggleRecording = () => {
    if (isRecording) {
      stopRecording()
    } else {
      startRecording()
    }
  }

  const hardStop = () => {
    // Stop recording if active
    if (isRecording && mediaRecorderRef.current) {
      stopRecording()
    }

    // Interrupt audio playback
    interruptAudio()

    // Keep interrupt flag set for hard stop (don't auto-reset)
    isInterruptedRef.current = true

    // Send hard stop message to backend
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'hard_stop' }))
    }

    // Clear transcript
    setTranscript('')
    
    // Clear any errors
    setError('')
  }

  return (
    <div className="app">
      <div className="container">
        <header className="header">
          <h1>Medi-Minds Realtime</h1>
        </header>

        <div className="status-section">
          <div className={`status-indicator ${isConnected ? 'connected' : 'disconnected'}`}>
            <span className="status-dot"></span>
            {isConnected ? 'Connected' : 'Disconnected'}
          </div>
          
          {sessionId && (
            <div className="session-display">
              Session ID: {sessionId}
            </div>
          )}
        </div>

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        <div className="controls">
          <button
            className={`record-button ${isRecording ? 'recording' : ''}`}
            onClick={toggleRecording}
            disabled={!isConnected}
            title={isRecording ? 'Stop recording' : 'Start recording (will interrupt GPT if speaking)'}
          >
            {isRecording ? (
              <>
                <span className="recording-dot"></span>
                Stop Recording
              </>
            ) : (
              'Start Recording'
            )}
          </button>
          <button
            className="stop-button"
            onClick={hardStop}
            disabled={!isConnected}
            title="Hard stop - stops everything immediately"
          >
            ⏹ Stop All
          </button>
        </div>

        <div className="transcript-section">
          <h2>Transcript</h2>
          <div className="transcript-content">
            {transcript || <span className="placeholder">Transcript will appear here...</span>}
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
