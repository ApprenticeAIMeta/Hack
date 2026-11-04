import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AnimatePresence, MotionConfig, motion } from 'framer-motion'
import './index.css'
import { drawCrop, refUrl, useSessionProcedure, useSocket } from './shared.js'
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
  const [proc, procName, followSession] = useSessionProcedure()
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
  const [replaying, setReplaying] = useState(false)  // a recorded run: checks and step picks would be refused
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
      followSession(m)
      setReplaying(m.status === 'started' && m.mode === 'replay')
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
  const hi = t => (/[ऀ-ॿ]/.test(t || '') ? 'hi' : undefined)

  return (
    <MotionConfig reducedMotion="user">
      <main className="dash relative h-full overflow-hidden">
        {/* The phone shows only the expert: what to do and what done looks like. The novice's
            own view appears only when there is a mistake to show. */}
        {s && (
          <div className="absolute inset-0 flex flex-col gap-3 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))]">
            <button type="button" onClick={() => !replaying && setPicking(true)} aria-label="Choose a step"
              className="rounded-[20px] border border-white/6 bg-[#131417] px-[18px] pb-4 pt-4 text-left focus-visible:outline-2 focus-visible:outline-[#7DD3F5]">
              <div className="mono flex items-center gap-3 text-xs tracking-[0.08em]">
                <span className="text-[#7DD3F5]">STEP {pad(step + 1)}<span className="text-[#5C6068]"> / {pad(proc.steps.length)}</span></span>
                {proc.language && <span className="text-[#8C9099]">{proc.language.toUpperCase()}</span>}
                <span className="flex-1" />
                {!replaying && <span className="text-[#8C9099]">ALL STEPS</span>}
              </div>
              <div className="mt-3 grid gap-1.5" style={{ gridTemplateColumns: `repeat(${proc.steps.length}, minmax(0, 1fr))` }} aria-hidden>
                {proc.steps.map((_, i) => {
                  const d = finished || i < step, n = !finished && i === step
                  return <div key={i} className="h-1 rounded" style={{ background: d ? '#6EE7B7' : n ? 'linear-gradient(90deg, #7DD3F5 45%, rgba(255,255,255,0.1) 45%)' : 'rgba(255,255,255,0.1)' }} />
                })}
              </div>
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div key={step} initial={{ y: 14, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -14, opacity: 0 }} transition={spring}>
                  <h1 className="mt-4 text-[30px] font-semibold leading-[1.1] tracking-[-0.02em]" lang={hi(s.title)}>{s.title}</h1>
                  <p className="mt-2 text-[17px] leading-[1.45] text-[#A0A4AC]" lang={hi(s.instruction)}>{s.instruction}</p>
                </motion.div>
              </AnimatePresence>
            </button>

            <figure className="relative m-0 min-h-0 flex-1 overflow-hidden rounded-[20px] border border-white/10 bg-black">
              <AnimatePresence mode="wait" initial={false}>
                <motion.img key={step} src={refUrl(proc, step)} alt="The expert's work at the end of this step"
                  className="absolute inset-0 h-full w-full object-cover"
                  initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.3 }} />
              </AnimatePresence>
              <Pill><span className="h-[9px] w-[9px] rounded-full bg-[#A0A4AC]" />EXPERT · STEP {pad(step + 1)} DONE</Pill>
              <div className={`mono absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-2.5 whitespace-nowrap rounded-full px-4 py-2 text-xs tracking-[0.08em] backdrop-blur-[10px] ${seen ? 'bg-[#0C0D0F]/80 text-[#F2F3F5]' : 'border border-amber-400/50 bg-[#0C0D0F]/85 text-amber-200'}`}>
                {seen ? <><span className="ripple" aria-hidden />{checking ? 'HOLD STILL · CHECKING' : 'DONE? HANDS OFF FOR 2 S'}</>
                  : frame ? <><span className="h-2 w-2 rounded-full bg-amber-400" />LOOK DOWN AT YOUR WORK</>
                    : <><span className="h-2 w-2 rounded-full bg-[#7C8089]" />NO VIDEO FROM THE GLASSES</>}
              </div>
            </figure>

            <AnimatePresence>
              {verdict && (
                <motion.p key="verdict" initial={{ y: 10, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ opacity: 0 }}
                  className="m-0 rounded-2xl bg-[#172630] px-4 py-3 text-[16px] leading-snug">
                  <span className={`mono mb-1 block text-[11px] tracking-[0.1em] ${verdict.ok ? 'text-[#6EE7B7]' : 'text-[#7DD3F5]'}`}>{verdict.ok ? 'LOOKS DONE' : 'NOT YET'}{verdict.conf != null ? ` · ${Math.round(verdict.conf * 100)}%` : ''}</span>
                  {verdict.text}
                </motion.p>
              )}
            </AnimatePresence>

            <AnimatePresence mode="wait">
              {said && (
                <motion.div key={said.text} initial={{ y: 12, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ opacity: 0 }} transition={spring}
                  className="flex items-start gap-3 rounded-2xl border border-white/8 bg-[#131417] px-4 py-3">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={said.live ? '#7DD3F5' : '#5C6068'} strokeWidth="2.4" strokeLinecap="round" aria-hidden="true" className="mt-1 shrink-0"><path d="M4 10v4" /><path d="M8 7v10" /><path d="M12 4v16" /><path d="M16 8v8" /><path d="M20 11v2" /></svg>
                  <div className="min-w-0">
                    {said.q && <p className="mb-1 text-[14px] text-[#A0A4AC]" lang={hi(said.q)}>You asked: “{said.q}”</p>}
                    <p className="text-[17px] font-medium leading-[1.45]" lang={hi(said.text)}>“{said.text}”</p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {replaying ? (
              <p className="mono m-0 rounded-2xl border border-white/10 py-4 text-center text-xs tracking-[0.1em] text-[#8C9099]">REPLAY OF A RECORDED RUN</p>
            ) : (
              <motion.button type="button" whileTap={{ scale: 0.98 }} onClick={askCheck} disabled={checking || finished}
                className={`rounded-2xl py-4 text-lg font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#B5E6FA] ${checking ? 'bg-[#172630] text-[#7DD3F5]' : 'bg-[#7DD3F5] text-[#0C0D0F]'}`}>
                {checking ? 'Checking your work…' : 'Check my step'}
              </motion.button>
            )}
          </div>
        )}

        <AnimatePresence>
          {picking && proc && (
            <motion.div key="picker" className="absolute inset-0 z-30 flex flex-col justify-end bg-black/60 p-4 backdrop-blur-sm"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setPicking(false)}>
              <motion.div className="rounded-[20px] border border-white/6 bg-[#131417] p-2" initial={{ y: 40 }} animate={{ y: 0 }} onClick={e => e.stopPropagation()}>
                <p className="mono px-3 pb-2 pt-3 text-xs tracking-[0.2em] text-[#8C9099]">GO TO STEP</p>
                {proc.steps.map((st, i) => (
                  <button type="button" key={st.id} onClick={() => pick(i)}
                    className={`flex w-full items-center gap-3.5 rounded-[14px] px-3 py-3 text-left ${i === step ? 'bg-[#172630]' : 'hover:bg-white/5'}`}>
                    {i < step ? <Tick /> : <span className={`mono w-[13px] text-center text-[13px] ${i === step ? 'text-[#7DD3F5]' : 'text-[#5C6068]'}`}>{i + 1}</span>}
                    <span className={`flex-1 text-[16px] ${i === step ? 'font-semibold' : i < step ? 'text-[#A0A4AC]' : ''}`} lang={hi(st.title)}>{st.title}</span>
                    {i === step && <span className="ripple" aria-hidden />}
                  </button>
                ))}
                <button type="button" onClick={() => setPicking(false)} className="mt-1 w-full rounded-[14px] py-3 text-[#A0A4AC]">Cancel</button>
              </motion.div>
            </motion.div>
          )}

          {wait && (
            <motion.div key="wait" className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-5 bg-[#0C0D0F] px-8 text-center"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <span className="grid h-14 w-14 place-items-center rounded-2xl border border-[#7DD3F5]/35 bg-[#14262D]"><span className="ripple" /></span>
              <p className="mono text-xs tracking-[0.2em] text-[#7DD3F5]">{wait.toUpperCase()}</p>
              {proc && <p className="text-lg text-[#A0A4AC]">{proc.task}</p>}
            </motion.div>
          )}

          {passed !== null && proc && !warn && (
            <motion.div key={`passed-${passed}`} className="pointer-events-none absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 bg-[#0C0D0F]/90 px-8 text-center"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <motion.div className="grid h-24 w-24 place-items-center rounded-full border-2 border-[#6EE7B7]/60 bg-[#6EE7B7]/10" initial={{ scale: 1.3, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', stiffness: 300, damping: 20 }}>
                <svg viewBox="0 0 24 24" className="h-11 w-11" fill="none" stroke="#6EE7B7" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <motion.path d="M5 12.5 10 17 19 7" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.1, duration: 0.3 }} />
                </svg>
              </motion.div>
              <p className="mono text-xs tracking-[0.2em] text-[#6EE7B7]">STEP {pad(passed + 1)} DONE</p>
              <p className="text-[28px] font-semibold tracking-[-0.02em]" lang={hi(proc.steps[passed].title)}>{proc.steps[passed].title}</p>
            </motion.div>
          )}

          {warn && s && (
            <motion.div key="warn" role="alert" className="absolute inset-0 z-20 flex flex-col justify-end bg-black/50 p-4"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <motion.div className="rounded-[20px] border border-amber-400/50 bg-[#131417] p-[18px]"
                initial={{ y: 40 }} animate={{ y: 0 }} transition={{ type: 'spring', stiffness: 420, damping: 30 }}>
                <p className="mono text-xs tracking-[0.2em] text-amber-300">CHECK THIS · STEP {pad(step + 1)}</p>
                <h1 className="mt-2 text-5xl font-bold tracking-tight text-amber-400">STOP</h1>
                <p className="mt-2 text-lg font-medium leading-snug" lang={hi(warn.what)}>{warn.what || "Check this against the expert's work."}</p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <figure className="relative m-0 h-40 overflow-hidden rounded-[14px] border border-white/10">
                    <img src={refUrl(proc, step)} alt="" className="h-full w-full object-cover" />
                    <figcaption className="mono absolute left-2 top-2 rounded-full bg-[#0C0D0F]/80 px-2 py-0.5 text-[10px] tracking-[0.1em]">EXPERT</figcaption>
                  </figure>
                  <figure className="relative m-0 h-40 overflow-hidden rounded-[14px] border-2 border-amber-400">
                    {frame && <Live frame={frame} box={box} frameSize={frameSize} />}
                    <figcaption className="mono absolute left-2 top-2 rounded-full bg-[#0C0D0F]/80 px-2 py-0.5 text-[10px] tracking-[0.1em] text-amber-300">YOURS</figcaption>
                  </figure>
                </div>
                {warn.play && <p className="mt-3 border-l-2 border-amber-400 pl-3 text-[16px] leading-snug text-[#D4D6DA]" lang={hi(warn.play.text)}>“{warn.play.text}”</p>}
                <motion.button type="button" whileTap={{ scale: 0.98 }} onClick={() => setWarn(null)}
                  className="mt-4 w-full rounded-2xl bg-amber-400 py-4 text-lg font-semibold text-[#0C0D0F] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-200">
                  Got it
                </motion.button>
              </motion.div>
            </motion.div>
          )}

          {finished && proc && (
            <motion.div key="finished" className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 overflow-auto bg-[#0C0D0F] px-4 py-8 text-center"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <p className="mono text-xs tracking-[0.2em] text-[#6EE7B7]">PROCEDURE COMPLETE</p>
              <h1 className="text-[32px] font-semibold tracking-[-0.02em]">All {proc.steps.length} steps done</h1>
              <ReportCard report={summarize(proc, doneAt, checks, warns, cost)} compact />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </MotionConfig>
  )
}

function Pill({ children }) {
  return (
    <div className="mono absolute left-1/2 top-3 flex max-w-[calc(100%-24px)] -translate-x-1/2 items-center gap-2.5 whitespace-nowrap rounded-full bg-[#0C0D0F]/80 px-4 py-2 text-xs tracking-[0.1em] backdrop-blur-[10px]">
      {children}
    </div>
  )
}

function Tick() {
  return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#6EE7B7" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5 10 17 19 7" /></svg>
}

createRoot(document.getElementById('root')).render(<StrictMode><Guide /></StrictMode>)
