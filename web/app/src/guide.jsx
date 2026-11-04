import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AnimatePresence, MotionConfig, motion } from 'framer-motion'
import './index.css'
import { drawCrop, refUrl, useProcedure, useSocket } from './shared.js'
import { Brackets, Glass, LiveDot, Segments, Waves } from './hud.jsx'
import { ReportCard, summarize } from './report.jsx'

// The novice's phone, propped beside the work. It shows the expert only: the step, what done
// looks like, and the expert's words. The novice's own view appears when there is a mistake.
const WARN_MS = 15000, PASSED_MS = 1400, LOST_TICKS = 3, SAID_MS = 6000
const spring = { type: 'spring', stiffness: 380, damping: 32 }
const pad = (n) => String(n).padStart(2, '0')

function Live({ frame, box, frameSize }) {
  const canvas = useRef(null)
  useEffect(() => drawCrop(canvas.current, frame, box, frameSize), [frame, box, frameSize])
  return <canvas ref={canvas} className="h-full w-full object-cover" />
}

function Guide() {
  const proc = useProcedure()
  const [step, setStep] = useState(null)
  const [frame, setFrame] = useState(null)
  const [box, setBox] = useState(null)
  const [frameSize, setFrameSize] = useState(null)
  const [lost, setLost] = useState(0)
  const [wait, setWait] = useState('Waiting for the glasses')
  const [said, setSaid] = useState(null)
  const [passed, setPassed] = useState(null)
  const [warn, setWarn] = useState(null)
  const [finished, setFinished] = useState(false)
  const [verdict, setVerdict] = useState(null)   // answer to a check the novice asked for
  const [picking, setPicking] = useState(false)  // the step list is open
  const [checking, setChecking] = useState(false)
  const [doneAt, setDoneAt] = useState({})
  const [checks, setChecks] = useState([])
  const [warns, setWarns] = useState([])
  const [cost, setCost] = useState(0)
  const timers = useRef({})
  const stepRef = useRef(null)
  const checkingRef = useRef(false)
  const pick = k => { setPicking(false); setVerdict(null); fetch(`/api/goto?step=${k}`, { method: 'POST' }).catch(() => {}) }
  const askCheck = () => {
    if (checkingRef.current) return
    checkingRef.current = true; setChecking(true); setVerdict(null)
    fetch('/api/check', { method: 'POST' }).catch(() => { checkingRef.current = false; setChecking(false) })
    later('checking', 20000, () => { checkingRef.current = false; setChecking(false) })
  }
  const later = (key, ms, fn) => { clearTimeout(timers.current[key]); timers.current[key] = setTimeout(fn, ms) }

  useSocket('/ws/guide', m => {
    if (m.type === 'settled') { checkingRef.current = true; setChecking(true); setVerdict(null); later('checking', 20000, () => { checkingRef.current = false; setChecking(false) }) }
    else if (m.type === 'ask') { setSaid({ text: m.answer, q: m.question, live: true }); later('said', 12000, () => setSaid(s => s && { ...s, live: false })) }
    else if (m.type === 'capped') setWait('Guidance paused: spending limit reached')
    else if (m.type === 'frame') {
      const img = new Image()
      img.onload = () => setFrame(img)
      img.src = 'data:image/jpeg;base64,' + m.jpeg
    } else if (m.type === 'session') {
      if (m.status === 'started') {
        stepRef.current = null; setStep(null); setLost(0); setDoneAt({}); setChecks([]); setWarns([]); setCost(0); setWarn(null); setSaid(null); setFinished(false); setWait('Starting')
      } else setWait('Session ended')
    } else if (m.type === 'state') {
      setWait(null)
      if (m.box) setBox(m.box)
      setFrameSize(m.frame_size)
      setLost(l => (m.box ? 0 : l + 1))
      if (stepRef.current !== m.step) { setSaid(null); setWarn(null) }
      stepRef.current = m.step
      setStep(m.step)
      if (m.cost_usd != null) setCost(m.cost_usd)
      for (const ev of m.events) {
        if (ev.kind === 'check') setChecks(c => [...c, ev])
        if (ev.kind === 'warn') setWarns(w => [...w, ev])
        if (ev.kind === 'step_done') { setDoneAt(d => ({ ...d, [ev.step]: ev.t })); setPassed(ev.step); later('passed', PASSED_MS, () => setPassed(null)) }
        if (ev.kind === 'instruct' && ev.play) { setSaid({ text: ev.play.text, live: true }); later('said', SAID_MS, () => setSaid(s => s && { ...s, live: false })) }
        if (ev.kind === 'warn') { setWarn(ev); later('warn', WARN_MS, () => setWarn(null)) }
        if (ev.kind === 'complete') { setWarn(null); setDoneAt(d => ({ ...d, [ev.step]: ev.t })); setFinished(true) }
        if (ev.kind === 'check' && ev.check === 'done' && (checkingRef.current || ev.trigger)) {
          checkingRef.current = false; setChecking(false)
          const ahead = ev.completed != null && ev.completed > ev.step
          setVerdict(ahead ? { text: 'Looks done. Confirming.', conf: ev.conf, ok: true } : { text: ev.unclear ? 'Could not see the work. Look down and try again.' : ev.reason || 'Not finished yet', conf: ev.conf })
          later('verdict', 8000, () => setVerdict(null))
        }
      }
      if (m.finished) setFinished(true)
    }
  })

  const s = proc && step !== null ? proc.steps[step] : null
  const seen = frame && lost < LOST_TICKS

  return (
    <MotionConfig reducedMotion="user">
      <main className="relative h-full overflow-hidden bg-[#030507]">
        {/* The phone shows only the expert: what to do and what done looks like. The novice's
            own view appears only when there is a mistake to show. */}
        {s && <img src={refUrl(proc, step)} alt="" className="absolute inset-0 h-full w-full scale-125 object-cover opacity-30 blur-3xl" />}
        <div className="scan absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_30%,rgba(3,5,7,.85)_100%)]" />
        <motion.div className="pointer-events-none absolute inset-0 z-10"
          animate={{ boxShadow: warn ? ['inset 0 0 0 3px rgba(251,191,36,.9), inset 0 0 80px rgba(251,191,36,.45)', 'inset 0 0 0 3px rgba(251,191,36,.4), inset 0 0 30px rgba(251,191,36,.15)'] : 'inset 0 0 0 0 rgba(0,0,0,0)' }}
          transition={warn ? { duration: 0.8, repeat: Infinity, repeatType: 'mirror' } : { duration: 0.3 }} />

        {s && (
          <div className="absolute inset-0 flex flex-col gap-3 p-3 pt-[max(.75rem,env(safe-area-inset-top))]">
            <Glass className="p-4" onClick={() => setPicking(true)} role="button" aria-label="Choose a step">
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs tracking-wider text-cyan-200">STEP {pad(step + 1)}<span className="text-white/40">/{pad(proc.steps.length)}</span>{proc.language && <span className="ml-2 text-white/40">· {proc.language.toUpperCase()}</span>}</span>
                <Segments n={proc.steps.length} step={step} finished={finished} className="flex-1" />
                <span className="font-mono text-[11px] tracking-wider text-white/45">STEPS ▾</span>
              </div>
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div key={step} initial={{ y: 18, opacity: 0, filter: 'blur(6px)' }} animate={{ y: 0, opacity: 1, filter: 'blur(0px)' }}
                  exit={{ y: -18, opacity: 0, filter: 'blur(6px)' }} transition={spring}>
                  <h1 className="mt-3 text-[34px] font-semibold leading-[1.05] tracking-tight">{s.title}</h1>
                  <p className="mt-1.5 text-[18px] leading-snug text-white/70">{s.instruction}</p>
                </motion.div>
              </AnimatePresence>
            </Glass>

            <div className="relative min-h-0 flex-1 px-2 pb-2 pt-8">
              <AnimatePresence mode="wait" initial={false}>
                <motion.img key={step} src={refUrl(proc, step)} alt="The expert's work at the end of this step"
                  className="h-full w-full rounded-2xl object-cover"
                  initial={{ opacity: 0, scale: 1.03, filter: 'blur(8px)' }} animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }} exit={{ opacity: 0 }} transition={{ duration: 0.4 }} />
              </AnimatePresence>
              <Brackets label="TARGET" sub={`expert, step ${pad(step + 1)} done`} className="inset-0" />
            </div>

            <motion.div layout className={`flex items-center justify-center gap-2 self-center rounded-full border px-4 py-2 font-mono text-[13px] tracking-wider backdrop-blur-xl ${seen ? 'border-cyan-300/30 bg-cyan-300/10 text-cyan-100' : 'border-amber-400/50 bg-amber-400/15 text-amber-200'}`}>
              {seen ? <><LiveDot tone="bg-cyan-300" />{checking ? 'HOLD STILL · CHECKING' : 'DONE? HANDS OFF FOR 2 S'}</> : frame ? <><LiveDot tone="bg-amber-400" />LOOK DOWN AT YOUR WORK</> : <><span className="h-2 w-2 rounded-full bg-white/30" />NO VIDEO FROM THE GLASSES</>}
            </motion.div>

            <AnimatePresence>
              {verdict && (
                <motion.p key="verdict" initial={{ y: 12, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ opacity: 0 }}
                  className="rounded-2xl border border-cyan-300/30 bg-cyan-300/10 px-4 py-3 text-[17px] leading-snug text-cyan-50 backdrop-blur-xl">
                  <span className="mb-0.5 block font-mono text-[11px] tracking-wider text-cyan-200">{verdict.ok ? 'LOOKS DONE' : 'NOT YET'}{verdict.conf != null ? ` · ${Math.round(verdict.conf * 100)}%` : ''}</span>
                  {verdict.text}
                </motion.p>
              )}
            </AnimatePresence>

            <motion.button whileTap={{ scale: 0.97 }} onClick={askCheck} disabled={checking || finished}
              className={`rounded-2xl py-4 text-xl font-semibold transition ${checking ? 'bg-white/10 text-white/50' : 'bg-cyan-300 text-black'} focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-200`}>
              {checking ? 'Checking your work…' : 'Check my step'}
            </motion.button>

            <AnimatePresence mode="wait">
              {said && (
                <Glass key={said.text} initial={{ y: 16, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ opacity: 0 }} transition={spring}
                  className="flex gap-3 px-4 py-3">
                  <div className="pt-1">{said.live ? <Waves /> : <span className="block h-4 w-[2px] rounded-full bg-white/30" />}</div>
                  <p className="text-[18px] leading-snug">
                    {said.q && <span className="mb-1 block text-[15px] text-white/60">You asked: “{said.q}”</span>}
                    <span className="mb-0.5 block font-mono text-[11px] tracking-wider text-cyan-200">EXPERT</span>
                    “{said.text}”
                  </p>
                </Glass>
              )}
            </AnimatePresence>
          </div>
        )}

        <AnimatePresence>
          {picking && proc && (
            <motion.div key="picker" className="absolute inset-0 z-30 flex flex-col justify-end bg-black/60 p-3 backdrop-blur-sm"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setPicking(false)}>
              <Glass className="p-2" initial={{ y: 40 }} animate={{ y: 0 }} onClick={e => e.stopPropagation()}>
                <p className="px-3 pb-2 pt-2 font-mono text-[11px] tracking-[0.2em] text-white/50">GO TO STEP</p>
                {proc.steps.map((st, i) => (
                  <button key={st.id} onClick={() => pick(i)}
                    className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left ${i === step ? 'bg-cyan-300/15' : 'hover:bg-white/5'}`}>
                    <span className={`font-mono text-sm ${i < step ? 'text-emerald-300' : i === step ? 'text-cyan-200' : 'text-white/40'}`}>{pad(i + 1)}</span>
                    <span className="text-[17px]">{st.title}</span>
                  </button>
                ))}
                <button onClick={() => setPicking(false)} className="mt-1 w-full rounded-xl py-3 text-white/60">Cancel</button>
              </Glass>
            </motion.div>
          )}

          {wait && (
            <motion.div key="wait" className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-6 bg-[#030507] px-8 text-center"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <div className="relative h-28 w-28">
                <motion.div className="absolute inset-0 rounded-full border border-cyan-300/30 border-t-cyan-300" animate={{ rotate: 360 }} transition={{ duration: 1.6, repeat: Infinity, ease: 'linear' }} />
                <motion.div className="absolute inset-4 rounded-full border border-cyan-300/20 border-b-cyan-200" animate={{ rotate: -360 }} transition={{ duration: 2.6, repeat: Infinity, ease: 'linear' }} />
                <span className="absolute inset-[46%] rounded-full bg-cyan-300 glow-cyan" />
              </div>
              <p className="font-mono text-sm tracking-[0.2em] text-cyan-200">{wait.toUpperCase()}</p>
              {proc && <p className="text-lg text-white/60">{proc.task}</p>}
            </motion.div>
          )}

          {passed !== null && proc && !warn && (
            <motion.div key={`passed-${passed}`} className="pointer-events-none absolute inset-0 z-20 flex flex-col items-center justify-center gap-5 bg-emerald-500/15 backdrop-blur-md"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <motion.div className="relative h-36 w-36 glow-cyan" initial={{ scale: 1.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', stiffness: 300, damping: 20 }}>
                <svg viewBox="0 0 100 100" className="h-full w-full">
                  <motion.path d="M28 52 L44 68 L74 36" fill="none" stroke="#34d399" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"
                    initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.15, duration: 0.35 }} />
                  {['M4 20 V4 H20', 'M80 4 H96 V20', 'M96 80 V96 H80', 'M20 96 H4 V80'].map(d => <path key={d} d={d} fill="none" stroke="#34d399" strokeWidth="3" />)}
                </svg>
              </motion.div>
              <p className="font-mono text-sm tracking-[0.2em] text-emerald-300">STEP {pad(passed + 1)} COMPLETE</p>
              <p className="text-3xl font-semibold">{proc.steps[passed].title}</p>
            </motion.div>
          )}

          {warn && s && (
            <motion.div key="warn" role="alert" className="absolute inset-0 z-20 flex flex-col justify-end gap-3 bg-amber-500/10 p-3 backdrop-blur-[2px]"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <Glass className="border-amber-400/60 bg-zinc-950/80 p-4 shadow-[0_0_60px_rgba(251,191,36,.25)]"
                initial={{ y: 40, scale: 0.96 }} animate={{ y: 0, scale: 1 }} transition={{ type: 'spring', stiffness: 420, damping: 24 }}>
                <div className="flex items-center gap-2 font-mono text-xs tracking-[0.2em] text-amber-300"><LiveDot tone="bg-amber-400" />MISTAKE DETECTED · STEP {pad(step + 1)}</div>
                <motion.h1 className="mt-2 text-7xl font-bold tracking-tight text-amber-400 glow-amber"
                  animate={{ x: [0, -6, 6, -3, 3, 0], opacity: [1, 0.6, 1, 0.8, 1] }} transition={{ delay: 0.1, duration: 0.4 }}>STOP</motion.h1>
                <p className="mt-1 text-xl font-medium leading-snug">{warn.what || "Check this against the expert's work"}</p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <figure className="relative h-40 overflow-hidden rounded-xl ring-1 ring-white/15">
                    <img src={refUrl(proc, step)} alt="" className="h-full w-full object-cover" />
                    <figcaption className="absolute left-2 top-2 font-mono text-[10px] tracking-wider text-cyan-200">TARGET</figcaption>
                  </figure>
                  <figure className="relative h-40 overflow-hidden rounded-xl ring-2 ring-amber-400">
                    {frame && <Live frame={frame} box={box} frameSize={frameSize} />}
                    <figcaption className="absolute left-2 top-2 font-mono text-[10px] tracking-wider text-amber-300">YOURS</figcaption>
                  </figure>
                </div>
                {warn.play && <p className="mt-3 border-l-2 border-amber-400 pl-3 text-[17px] leading-snug text-white/85">“{warn.play.text}”</p>}
                <motion.button whileTap={{ scale: 0.97 }} onClick={() => setWarn(null)}
                  className="mt-4 w-full rounded-xl bg-amber-400 py-4 text-xl font-semibold text-black focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-200">
                  Got it
                </motion.button>
              </Glass>
            </motion.div>
          )}

          {finished && proc && (
            <motion.div key="finished" className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 overflow-auto bg-[#030507]/85 px-4 py-8 text-center backdrop-blur-xl"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <p className="font-mono text-sm tracking-[0.2em] text-emerald-300">PROCEDURE COMPLETE</p>
              <h1 className="text-4xl font-semibold tracking-tight">All {proc.steps.length} steps done</h1>
              <ReportCard report={summarize(proc, doneAt, checks, warns, cost)} compact />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </MotionConfig>
  )
}

createRoot(document.getElementById('root')).render(<StrictMode><Guide /></StrictMode>)
