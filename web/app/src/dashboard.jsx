import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AnimatePresence, MotionConfig, motion, useSpring, useTransform } from 'framer-motion'
import './index.css'
import { PROCEDURE, fmt, useProcedure, useSocket } from './shared.js'
import { Brackets, Glass, LiveDot, Segments, Waves, placeBox } from './hud.jsx'
import { ReportCard, summarize } from './report.jsx'

// The laptop view for the video: the glasses' feed in the middle with the tracked workpiece,
// the expert at the same point in the task, the progress model's live reading, and every
// model call lighting up with its real timing.
const spring = { type: 'spring', stiffness: 300, damping: 30 }
const pad = (n) => String(n).padStart(2, '0')

function Ring({ value }) {
  const v = useSpring(0, { stiffness: 80, damping: 18 })
  useEffect(() => { v.set(value ?? 0) }, [value, v])
  const r = 52, c = 2 * Math.PI * r
  const dash = useTransform(v, p => `${c * p} ${c}`)
  const [shown, setShown] = useState(0)
  useEffect(() => v.on('change', p => setShown(Math.round(p * 100))), [v])
  return (
    <div className="relative h-36 w-36">
      <svg viewBox="0 0 120 120" className="h-full w-full -rotate-90">
        <circle cx="60" cy="60" r={r} fill="none" stroke="rgba(255,255,255,.08)" strokeWidth="6" />
        {Array.from({ length: 40 }, (_, i) => <line key={i} x1="60" y1="2" x2="60" y2={i % 10 ? 5 : 8} stroke="rgba(255,255,255,.25)" strokeWidth="1" transform={`rotate(${i * 9} 60 60)`} />)}
        <motion.circle cx="60" cy="60" r={r} fill="none" stroke="#67e8f9" strokeWidth="6" strokeLinecap="round" style={{ strokeDasharray: dash }} className="glow-cyan" />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div><span className="font-mono text-4xl font-medium tabular-nums">{value == null ? '--' : shown}</span><span className="font-mono text-sm text-white/50">%</span></div>
      </div>
    </div>
  )
}

function Spark({ curve, warns, span }) {
  const X = t => (t / span) * 300, Y = p => 60 - p * 56
  const d = curve.map(([t, p], i) => `${i ? 'L' : 'M'}${X(t).toFixed(1)} ${Y(p).toFixed(1)}`).join(' ')
  return (
    <svg viewBox="0 0 300 62" className="h-16 w-full">
      <line x1="0" y1={Y(0)} x2={X(span)} y2={Y(1)} stroke="rgba(244,63,94,.6)" strokeDasharray="4 4" strokeWidth="1.5" />
      {warns.map(t => <line key={t} x1={X(t)} x2={X(t)} y1="0" y2="62" stroke="#fbbf24" strokeWidth="2" />)}
      <path d={d} fill="none" stroke="#67e8f9" strokeWidth="2" className="glow-cyan" />
    </svg>
  )
}

function Telemetry({ on, model, what, value }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <motion.span className="h-2 w-2 rounded-full" animate={{ backgroundColor: on ? '#67e8f9' : 'rgba(255,255,255,.2)', boxShadow: on ? '0 0 10px #67e8f9' : '0 0 0 rgba(0,0,0,0)' }} />
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-medium ${on ? 'text-white' : 'text-white/70'}`}>{model}</p>
        <p className="text-xs text-white/45">{what}</p>
      </div>
      <span className="font-mono text-xs tabular-nums text-cyan-200">{value ?? '--'}</span>
    </div>
  )
}

function Dashboard() {
  const proc = useProcedure()
  const [novice, setNovice] = useState(null)
  const [backdrop, setBackdrop] = useState(null)
  const backdropAt = useRef(0)
  const [mirrorT, setMirrorT] = useState(null)
  const [box, setBox] = useState(null)
  const [frameSize, setFrameSize] = useState(null)
  const [progress, setProgress] = useState(null)
  const [step, setStep] = useState(0)
  const [done, setDone] = useState(new Set())
  const [said, setSaid] = useState(null)
  const [warn, setWarn] = useState(null)
  const [curve, setCurve] = useState([])
  const [warns, setWarns] = useState([])
  const [now, setNow] = useState(0)
  const [cost, setCost] = useState(0)
  const [status, setStatus] = useState({ text: 'Waiting for glasses', live: false })
  const [lit, setLit] = useState({})
  const [timing, setTiming] = useState({})
  const [sound, setSound] = useState(false)
  const [checks, setChecks] = useState([])
  const [picker, setPicker] = useState(null)
  const [speed, setSpeed] = useState(2)
  const [doneAt, setDoneAt] = useState({})
  const [warnEvents, setWarnEvents] = useState([])
  const [report, setReport] = useState(null)
  const [feedSize, setFeedSize] = useState([0, 0])
  const feedRef = useRef(null)
  const feedImg = useRef(null)
  const haveFeed = useRef(false)
  const timers = useRef({})
  const soundRef = useRef(sound)
  soundRef.current = sound

  useEffect(() => {
    const el = feedRef.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setFeedSize([e.contentRect.width, e.contentRect.height]))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const flash = (k, ms) => {
    setLit(l => ({ ...l, [k]: true }))
    clearTimeout(timers.current[k]); timers.current[k] = setTimeout(() => setLit(l => ({ ...l, [k]: false })), ms)
  }

  useSocket('/ws/dashboard', m => {
    if (m.type === 'ask') {
      setSaid({ text: m.answer, t: m.t, q: m.question })
      setChecks(c => [{ t: m.t, step: m.step, kind: 'ask', askRow: true, question: m.question, answer: m.answer }, ...c].slice(0, 60))
      flash('voice', 5000); flash('spark', 1500)
      if (soundRef.current && m.url) new Audio(m.url).play().catch(() => {})
    } else if (m.type === 'frame') {
      const src = 'data:image/jpeg;base64,' + m.jpeg
      // 8 frames a second: paint the feed directly instead of re-rendering the whole page each time
      if (feedImg.current) feedImg.current.src = src
      if (!haveFeed.current) { haveFeed.current = true; setNovice(src) }
      // the blurred backdrop is expensive to repaint, so it follows the feed only every few seconds
      if (performance.now() - backdropAt.current > 3000) { backdropAt.current = performance.now(); setBackdrop(src) }
    }
    else if (m.type === 'capped') setStatus({ text: `Stopped at the $${m.cap_usd} limit`, live: false })
    else if (m.type === 'session') {
      const started = m.status === 'started', replay = m.mode === 'replay'
      setStatus({ text: !started ? (replay ? 'Replay ended' : 'Session ended') : replay ? `Replay · ${m.run ?? m.session ?? ''}${m.speed && m.speed !== 1 ? ` · ${m.speed}x` : ''}` : 'Live', live: started && !replay, replay: started && replay })
      if (started) { haveFeed.current = false; setDoneAt({}); setWarnEvents([]); setReport(null); setChecks([]); setCurve([]); setWarns([]); setDone(new Set()); setStep(0); setSaid(null); setWarn(null) }
    } else if (m.type === 'state') {
      setNow(m.t)
      if (m.progress != null) { setProgress(m.progress); setCurve(c => [...c, [m.t, m.progress]]); flash('dino', 700) }
      if (m.box) { setBox(m.box); setFrameSize(m.frame_size); flash('sam', 700) } else setBox(null)
      if (m.mirror_t != null) setMirrorT(m.mirror_t)
      if (m.timing_ms) setTiming(t => ({ ...t, ...m.timing_ms }))
      setStep(m.step); setCost(m.cost_usd)
      setDone(d => {
        const n = new Set(d)
        for (const ev of m.events) if (ev.kind === 'step_done' || ev.kind === 'complete') { n.add(ev.step); setDoneAt(d => ({ ...d, [ev.step]: ev.t })) }
        if (m.finished && proc) proc.steps.forEach((_, i) => n.add(i))
        return n
      })
      for (const ev of m.events) {
        if (ev.kind === 'check') {
          flash('spark', 1500); setTiming(t => ({ ...t, spark: ev.latency_s }))
          setChecks(c => [ev, ...c].slice(0, 60))
        }
        if (ev.kind === 'warn') {
          setWarnEvents(w => [...w, ev])
          setChecks(c => [{ ...ev, warnRow: true }, ...c].slice(0, 60))
          setWarns(w => [...w, ev.t]); setWarn(ev)
          clearTimeout(timers.current.warn); timers.current.warn = setTimeout(() => setWarn(null), 9000)
        }
        if (ev.play) {
          setSaid({ text: ev.play.text, t: ev.t, warn: ev.kind === 'warn' })
          flash('voice', 5000)
          if (soundRef.current) new Audio(ev.play.url).play().catch(() => {})
        }
      }
    }
  })

  useEffect(() => { if (new URLSearchParams(location.search).has('demo')) fetch('/api/demo', { method: 'POST' }) }, [])
  const finishedRun = proc && doneAt[proc.steps.length - 1] != null
  useEffect(() => {
    if (finishedRun && !report) setReport(summarize(proc, doneAt, checks.filter(c => !c.warnRow), warnEvents, cost))
  }, [finishedRun]) // eslint-disable-line react-hooks/exhaustive-deps

  const openPicker = () => fetch('/api/sessions').then(r => r.json()).then(setPicker).catch(() => setPicker([]))
  const replay = name => { setPicker(null); fetch(`/api/demo?session=${encodeURIComponent(name)}&speed=${speed}`, { method: 'POST' }) }

  const tracked = novice && placeBox(box, frameSize, feedSize[0], feedSize[1], 'contain')
  const mirrorStep = proc && mirrorT != null ? proc.steps.find(s => mirrorT >= s.t_start && mirrorT <= s.t_end) : null
  const cur = proc?.steps[step]
  const span = proc ? Math.max(proc.steps.at(-1).t_end - proc.steps[0].t_start, now, 30) : 60

  return (
    <MotionConfig reducedMotion="user">
      <div className="relative h-full overflow-hidden bg-[#030507]">
        {/* blurred copy of the feed fills the screen behind everything */}
        {backdrop && <img src={backdrop} alt="" className="absolute inset-0 h-full w-full scale-110 object-cover opacity-35 blur-3xl transition-opacity" />}
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_20%,#030507_85%)]" />
        <motion.div className="pointer-events-none absolute inset-0 z-30"
          animate={{ boxShadow: warn ? ['inset 0 0 0 3px rgba(251,191,36,.9), inset 0 0 140px rgba(251,191,36,.35)', 'inset 0 0 0 3px rgba(251,191,36,.35), inset 0 0 50px rgba(251,191,36,.12)'] : 'inset 0 0 0 0 rgba(0,0,0,0)' }}
          transition={warn ? { duration: 0.8, repeat: Infinity, repeatType: 'mirror' } : { duration: 0.3 }} />

        <AnimatePresence>
          {report && (
            <motion.div key="report" className="absolute inset-0 z-40 grid place-items-center bg-black/50 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <ReportCard report={report} onClose={() => setReport(null)} />
            </motion.div>
          )}
        </AnimatePresence>
        <div className="relative grid h-full grid-cols-[300px_minmax(0,1fr)_340px] grid-rows-[auto_minmax(0,1fr)_auto] gap-4 p-5">
          {/* top bar */}
          <header className="col-span-3 flex items-center gap-4">
            <span className="flex items-center gap-2.5">
              <span className="grid h-7 w-7 place-items-center rounded-lg border border-cyan-300/50 bg-cyan-300/10"><span className="h-2 w-2 rounded-full bg-cyan-300 glow-cyan" /></span>
              <span className="text-lg font-semibold tracking-tight">ApprenticeAI</span>
            </span>
            <span className="hidden truncate text-sm text-white/50 xl:inline">{proc?.task}</span>
            <span className="flex-1" />
            <span className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 font-mono text-xs tracking-wider backdrop-blur">
              {status.live ? <LiveDot /> : <span className={`h-2 w-2 rounded-full ${status.replay ? 'bg-cyan-300' : 'bg-white/30'}`} />}
              {status.text.toUpperCase()}
            </span>
            <span className="font-mono text-xs tabular-nums text-white/50" title="Model calls this run; frames and stills stay on this laptop">
              ${cost.toFixed(2)} · {checks.filter(c => !c.warnRow).length} checks · {checks.filter(c => c.source === 'photo').length} stills
            </span>
            <div className="relative">
              <button onClick={() => (picker ? setPicker(null) : openPicker())}
                className="rounded-full border border-white/10 bg-white/5 px-3 py-1 font-mono text-xs tracking-wider text-white/70 transition hover:text-white">REPLAY A RUN</button>
              <AnimatePresence>
                {picker && (
                  <Glass initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
                    className="absolute right-0 top-9 z-40 w-[420px] bg-zinc-950/90 p-2">
                    <div className="flex items-center justify-between px-2 py-1.5">
                      <span className="font-mono text-[11px] tracking-[0.2em] text-white/45">SAVED RUNS</span>
                      <span className="flex gap-1">{[1, 2, 4].map(x => (
                        <button key={x} onClick={() => setSpeed(x)}
                          className={`rounded-md px-2 py-0.5 font-mono text-xs ${speed === x ? 'bg-cyan-300/20 text-cyan-100' : 'text-white/50 hover:text-white'}`}>{x}x</button>
                      ))}</span>
                    </div>
                    <ul className="max-h-[60vh] overflow-auto">
                      {!picker.length && <li className="px-2 py-3 text-sm text-white/50">No saved runs yet.</li>}
                      {picker.map(r => (
                        <li key={r.session}>
                          <button onClick={() => replay(r.session)} className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-white/5">
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium">{r.run || r.session}</span>
                              <span className="block font-mono text-[11px] text-white/45">{(r.started || '').replace('T', ' ').slice(5, 16)} · {r.procedure}</span>
                            </span>
                            <span className="font-mono text-xs tabular-nums text-white/60">{r.duration_s ? fmt(r.duration_s) : '--'}</span>
                            <span className="font-mono text-xs tabular-nums text-emerald-300">{r.steps_done} steps</span>
                            <span className={`font-mono text-xs tabular-nums ${r.warnings ? 'text-amber-300' : 'text-white/35'}`}>{r.warnings} warn</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </Glass>
                )}
              </AnimatePresence>
            </div>
            <button onClick={() => setSound(s => !s)} aria-pressed={sound}
              className={`rounded-full border px-3 py-1 font-mono text-xs tracking-wider transition ${sound ? 'border-cyan-300/60 bg-cyan-300/15 text-cyan-100' : 'border-white/10 bg-white/5 text-white/60 hover:text-white'}`}>
              SOUND {sound ? 'ON' : 'OFF'}
            </button>
          </header>

          {/* left: steps and telemetry */}
          <div className="flex min-h-0 flex-col gap-4">
            <Glass className="p-4">
              <p className="mb-3 font-mono text-[11px] tracking-[0.2em] text-white/45">PROCEDURE</p>
              <ol className="flex flex-col gap-1">
                {proc?.steps.map((s, i) => {
                  const isDone = done.has(i), isNow = i === step && !isDone
                  return (
                    <motion.li key={s.id} layout className={`relative flex items-center gap-3 rounded-lg px-2 py-1.5 ${isNow ? 'bg-cyan-300/10' : ''}`}>
                      {isNow && <motion.span layoutId="now" className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-cyan-300 glow-cyan" />}
                      <span className={`font-mono text-xs tabular-nums ${isDone ? 'text-emerald-400' : isNow ? 'text-cyan-200' : 'text-white/35'}`}>{isDone ? '✓' : pad(i + 1)}</span>
                      <span className={`text-sm ${isDone ? 'text-white/60 line-through decoration-white/20' : isNow ? 'font-medium text-white' : 'text-white/45'}`}>{s.title}</span>
                    </motion.li>
                  )
                })}
              </ol>
            </Glass>
            <Glass className="px-4 py-3">
              <p className="mb-1 font-mono text-[11px] tracking-[0.2em] text-white/45">META MODELS · LIVE</p>
              <div className="divide-y divide-white/5">
                <Telemetry on={lit.sam} model="SAM 3.1" what="tracks the workpiece" value={timing.sam ? `${(timing.sam / 1000).toFixed(1)}s` : null} />
                <Telemetry on={lit.dino} model="DINOv2" what="scores progress" value={timing.model != null ? `${timing.model}ms` : null} />
                <Telemetry on={lit.spark} model="Muse Spark" what="checks the step" value={timing.spark ? `${timing.spark.toFixed(1)}s` : null} />
                <Telemetry on={lit.voice} model="Muse Voice Transcribe" what="cut the expert's words" value={said ? 'clip' : null} />
              </div>
            </Glass>
            <Glass className="flex min-h-0 flex-1 flex-col px-4 py-3">
              <p className="mb-2 font-mono text-[11px] tracking-[0.2em] text-white/45">MUSE SPARK LOG</p>
              {!checks.length && <p className="text-sm text-white/40">Each check's answer and reason appears here.</p>}
              <ul className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
                <AnimatePresence initial={false}>
                  {checks.map((c, i) => (
                    <motion.li key={`${c.t}-${i}-${c.kind}`} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
                      className={`rounded-lg px-2.5 py-1.5 text-xs ${c.warnRow ? 'bg-amber-400/15 text-amber-100' : c.askRow ? 'bg-cyan-300/10' : 'bg-white/[.03]'}`}>
                      <div className="flex items-center gap-2 font-mono text-[11px] text-white/50">
                        <span className="tabular-nums">{fmt(c.t)}</span>
                        <span>step {pad(c.step + 1)}</span>
                        {c.askRow ? <span className="text-cyan-200">ASKED</span> : c.warnRow ? <span className="text-amber-300">WARNING</span>
                          : <span className={c.yes ? 'text-emerald-300' : 'text-white/60'}>{c.check === 'locate' ? `sees ${c.completed} done` : c.yes ? 'done' : 'not yet'}</span>}
                        {c.conf != null && <span>{Math.round(c.conf * 100)}%</span>}
                        <span className="flex-1" />
                        {c.trigger && <span className="text-cyan-200">{c.trigger === 'settle' ? 'hands off' : 'tapped'}</span>}
                        {c.source && <span>{c.source}</span>}
                        {c.latency_s != null && <span className="tabular-nums text-cyan-200">{c.latency_s.toFixed(1)}s</span>}
                      </div>
                      <p className="mt-0.5 leading-snug text-white/80">{c.askRow ? `“${c.question}” → ${c.answer}` : c.warnRow ? c.what : c.reason}</p>
                    </motion.li>
                  ))}
                </AnimatePresence>
              </ul>
            </Glass>
          </div>

          {/* center: the feed */}
          <section ref={feedRef} className="relative min-h-0">
            {novice ? <img ref={feedImg} src={novice} alt="Novice's view from the glasses" className="scan h-full w-full rounded-3xl object-contain" />
              : <div className="grid h-full place-items-center font-mono text-sm tracking-[0.2em] text-white/40">NO SIGNAL · START THE STREAM ON THE PHONE</div>}
            {tracked && (
              <motion.div className="absolute" initial={false} animate={tracked} transition={{ type: 'spring', stiffness: 200, damping: 26 }}>
                <Brackets tone={warn ? 'amber' : 'cyan'} label={warn ? 'MISMATCH' : 'WORKPIECE'} sub={warn ? null : `SAM 3.1${timing.sam ? ` · ${(timing.sam / 1000).toFixed(1)}s` : ''}`} className="inset-0" />
              </motion.div>
            )}
            <div className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-2 rounded-full border border-white/10 bg-zinc-950/60 px-3 py-1 font-mono text-[11px] tracking-wider backdrop-blur">
              <LiveDot /> NOVICE · {status.replay ? 'RECORDED' : 'LIVE'} FROM GLASSES · {fmt(now)}
            </div>

            <AnimatePresence mode="wait">
              {said && !warn && (
                <motion.div key={said.text} initial={{ y: 14, opacity: 0, filter: 'blur(6px)' }} animate={{ y: 0, opacity: 1, filter: 'blur(0px)' }} exit={{ opacity: 0 }} transition={spring}
                  className="absolute inset-x-8 bottom-5 flex items-start justify-center gap-3">
                  <div className="mt-1.5"><Waves /></div>
                  <p className="max-w-xl rounded-xl bg-zinc-950/70 px-4 py-2 text-center text-lg leading-snug backdrop-blur">
                    {said.q && <span className="block text-sm text-white/60">Asked: “{said.q}”</span>}“{said.text}”</p>
                </motion.div>
              )}
            </AnimatePresence>

            <AnimatePresence>
              {warn && (
                <Glass key="warn" role="alert" className="absolute inset-x-6 bottom-5 border-amber-400/60 bg-zinc-950/80 px-6 py-5 shadow-[0_0_80px_rgba(251,191,36,.25)]"
                  initial={{ y: 30, opacity: 0, scale: 0.97 }} animate={{ y: 0, opacity: 1, scale: 1 }} exit={{ y: 20, opacity: 0 }} transition={{ type: 'spring', stiffness: 420, damping: 26 }}>
                  <div className="flex items-center gap-6">
                    <motion.span className="text-6xl font-bold tracking-tight text-amber-400 glow-amber"
                      animate={{ x: [0, -5, 5, -2, 2, 0], opacity: [1, 0.6, 1, 0.8, 1] }} transition={{ delay: 0.1, duration: 0.4 }}>STOP</motion.span>
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 font-mono text-[11px] tracking-[0.2em] text-amber-300"><LiveDot tone="bg-amber-400" />MISTAKE DETECTED · MUSE SPARK</p>
                      <p className="mt-1 text-xl font-medium">{warn.what || 'Check your work'}</p>
                      {warn.play && <p className="mt-1 text-white/70">“{warn.play.text}”</p>}
                    </div>
                  </div>
                </Glass>
              )}
            </AnimatePresence>
          </section>

          {/* right: expert target and progress */}
          <div className="flex min-h-0 flex-col gap-4">
            <Glass className="flex min-h-0 flex-1 flex-col p-1.5">
              <div className="flex items-center justify-between px-2.5 pb-1.5 pt-1 font-mono text-[11px] tracking-wider">
                <span className="flex items-center gap-2 text-white/60"><span className="h-1.5 w-1.5 rounded-full bg-rose-400" />EXPERT · SAME POINT</span>
                <span className="truncate pl-2 text-white/40">{mirrorStep?.title}</span>
              </div>
              <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl bg-black/40">
                {mirrorT != null && <img src={`/api/mirror/${PROCEDURE}/${mirrorT.toFixed(1)}.jpg`} alt="The expert at the same point in the task" className="h-full w-full object-contain" />}
              </div>
            </Glass>
            <Glass className="flex items-center gap-4 p-4">
              <Ring value={progress} />
              <div className="min-w-0 flex-1">
                <p className="font-mono text-[11px] tracking-[0.2em] text-white/45">TASK PROGRESS</p>
                <p className="mb-1 text-xs text-white/40">Step from Muse Spark, position within it from DINOv2</p>
                <Spark curve={curve} warns={warns} span={span} />
              </div>
            </Glass>
          </div>

          {/* bottom: current step */}
          <Glass className="col-span-3 flex items-center gap-5 px-5 py-4">
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span key={step} initial={{ y: 12, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -12, opacity: 0 }} transition={spring}
                className="font-mono text-4xl font-medium tabular-nums text-cyan-200">{pad(step + 1)}</motion.span>
            </AnimatePresence>
            <div className="min-w-0 flex-1">
              <p className="text-2xl font-semibold tracking-tight">{cur?.title}</p>
              <p className="truncate text-white/55">{cur?.instruction}</p>
            </div>
            {proc && <Segments n={proc.steps.length} step={step} finished={done.size === proc.steps.length} className="w-72" />}
          </Glass>
        </div>
      </div>
    </MotionConfig>
  )
}

createRoot(document.getElementById('root')).render(<StrictMode><Dashboard /></StrictMode>)
