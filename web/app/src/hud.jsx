import { motion } from 'framer-motion'

// Pieces shared by the phone guide and the dashboard.

/** Corner brackets around a region, like a tracking reticle. */
export function Brackets({ tone = 'cyan', label, sub, className = '', style }) {
  const c = tone === 'amber' ? 'border-amber-400' : 'border-cyan-300'
  const glow = tone === 'amber' ? 'glow-amber' : 'glow-cyan'
  const corner = `absolute h-5 w-5 ${c}`
  return (
    <motion.div className={`pointer-events-none absolute ${glow} ${className}`} style={style}>
      <i className={`${corner} left-0 top-0 rounded-tl-md border-l-2 border-t-2`} />
      <i className={`${corner} right-0 top-0 rounded-tr-md border-r-2 border-t-2`} />
      <i className={`${corner} bottom-0 left-0 rounded-bl-md border-b-2 border-l-2`} />
      <i className={`${corner} bottom-0 right-0 rounded-br-md border-b-2 border-r-2`} />
      {label && (
        <span className={`absolute -top-6 left-0 whitespace-nowrap font-mono text-[11px] tracking-wide ${tone === 'amber' ? 'text-amber-300' : 'text-cyan-200'}`}>
          {label}{sub && <span className="text-white/50"> {sub}</span>}
        </span>
      )}
    </motion.div>
  )
}

export function Glass({ className = '', children, ...rest }) {
  return (
    <motion.div className={`rounded-2xl border border-white/10 bg-zinc-950/55 shadow-[0_8px_40px_rgba(0,0,0,.45)] backdrop-blur-xl ${className}`} {...rest}>
      {children}
    </motion.div>
  )
}

/** Thin segmented step bar. */
export function Segments({ n, step, finished, className = '' }) {
  return (
    <div className={`flex gap-1 ${className}`} aria-hidden>
      {Array.from({ length: n }, (_, i) => {
        const done = finished || i < step, now = !finished && i === step
        return (
          <div key={i} className="relative h-1 flex-1 overflow-hidden rounded-full bg-white/10">
            <motion.div className={`absolute inset-0 rounded-full ${done ? 'bg-emerald-400' : 'bg-cyan-300'}`} style={{ originX: 0 }}
              initial={false} animate={{ scaleX: done ? 1 : now ? 0.45 : 0 }} transition={{ type: 'spring', stiffness: 260, damping: 30 }} />
            {now && <motion.div className="absolute inset-y-0 w-8 bg-gradient-to-r from-transparent via-white/70 to-transparent"
              animate={{ x: ['-2rem', '6rem'] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }} />}
          </div>
        )
      })}
    </div>
  )
}

export function LiveDot({ tone = 'bg-rose-500' }) {
  return (
    <span className="relative flex h-2 w-2">
      <motion.span className={`absolute inline-flex h-full w-full rounded-full ${tone}`} animate={{ scale: [1, 2.2], opacity: [0.7, 0] }} transition={{ duration: 1.4, repeat: Infinity }} />
      <span className={`relative inline-flex h-2 w-2 rounded-full ${tone}`} />
    </span>
  )
}

export function Waves() {
  return (
    <span className="flex h-4 items-end gap-[2px]" aria-hidden>
      {[0.5, 1, 0.65, 0.9, 0.4].map((h, i) => (
        <motion.i key={i} className="w-[2px] rounded-full bg-cyan-300"
          animate={{ height: ['25%', `${h * 100}%`, '35%'] }} transition={{ duration: 0.6 + i * 0.11, repeat: Infinity, repeatType: 'mirror' }} />
      ))}
    </span>
  )
}

/** Where SAM's box lands on an image drawn with object-fit (cover or contain) inside a W x H element. */
export function placeBox(box, frameSize, W, H, fit = 'cover') {
  if (!box || !frameSize || !W) return null
  const [fw, fh] = frameSize, s = fit === 'cover' ? Math.max(W / fw, H / fh) : Math.min(W / fw, H / fh)
  const ox = (W - fw * s) / 2, oy = (H - fh * s) / 2
  return { left: ox + box[0] * s, top: oy + box[1] * s, width: (box[2] - box[0]) * s, height: (box[3] - box[1]) * s }
}
