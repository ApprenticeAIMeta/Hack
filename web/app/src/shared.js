import { useEffect, useRef, useState } from 'react'

export const PROCEDURE = new URLSearchParams(location.search).get('procedure') || 'airplane'

/** The compiled procedure: steps, reference frames and the expert's voice clips. */
export function useProcedure(name = PROCEDURE) {
  const [proc, setProc] = useState(null)
  useEffect(() => {
    fetch(`/api/procedure/${name}`).then(r => r.json()).then(setProc).catch(() => {})
  }, [name])
  return proc
}

/** One server socket (/ws/guide or /ws/dashboard), reconnecting if the server restarts. */
export function useSocket(path, onMessage) {
  const handler = useRef(onMessage)
  handler.current = onMessage
  useEffect(() => {
    let ws, retry, closed = false
    const connect = () => {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`)
      ws.onmessage = e => handler.current(JSON.parse(e.data))
      ws.onclose = () => { if (!closed) retry = setTimeout(connect, 1000) }
    }
    connect()
    return () => { closed = true; clearTimeout(retry); ws?.close() }
  }, [path])
}

export const refUrl = (proc, k, which = 'crop') => {
  const rf = proc?.steps[k]?.reference_frame
  return rf?.[which] ? `/procedures/${PROCEDURE}/${rf[which]}` : ''
}

export const fmt = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`

/** Draws the novice's frame cropped around SAM's workpiece box, the same way the expert's picture is cropped. */
export function drawCrop(canvas, img, box, frameSize) {
  if (!canvas || !img?.naturalWidth) return
  const W = img.naturalWidth, H = img.naturalHeight
  let [x, y, w, h] = [0, 0, W, H]
  if (box && frameSize) {
    const k = W / frameSize[0], side = Math.max(box[2] - box[0], box[3] - box[1]) * 1.3 * k
    x = Math.max(0, (box[0] + box[2]) / 2 * k - side / 2)
    y = Math.max(0, (box[1] + box[3]) / 2 * k - side / 2)
    w = Math.min(W - x, side); h = Math.min(H - y, side)
  }
  canvas.width = w; canvas.height = h
  canvas.getContext('2d').drawImage(img, x, y, w, h, 0, 0, w, h)
}
