import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AnimatePresence, MotionConfig, motion, useSpring, useTransform } from 'framer-motion'
import './index.css'
import { fmt, useSessionProcedure, useSocket } from './shared.js'
import { placeBox } from './hud.jsx'
import { ReportCard, summarize } from './report.jsx'

// The laptop view for the video (design handoff, Nov 4): novice and expert side by side at equal
// size with the spoken line across both, the procedure, each Meta model lighting up with its real
// timing, the step-anchored progress gauge, and every Muse Spark answer with its reason.
const spring = { type: 'spring', stiffness: 300, damping: 30 }
const pad = (n) => String(n).padStart(2, '0')

const hindi = t => /[ऀ-ॿ]/.test(t || '')

/** Where the gauge sits inside step i's own band, for the step bar (0 when unknown). */
function withinStep(p, proc, i) {
  if (p == null) return 0.08
  const t0 = proc.steps[0].t_start, T = proc.steps.at(-1).t_end - t0
  const a = (proc.steps[i].t_start - t0) / T, b = (proc.steps[i].t_end - t0) / T
  return Math.max(0.08, Math.min(1, (p - a) / Math.max(b - a, 1e-6)))
}

function verdict(c) {
  if (c.unclear) return 'unclear'
  if (c.check === 'done') return c.completed > c.step ? 'step done' : `on step ${pad((c.completed ?? c.step) + 1)}`
  return c.yes ? 'possible mistake' : 'no mistake'
}

function Card({ className = '', children }) {
  return <section className={`rounded-[20px] border border-white/6 bg-[#131417] ${className}`}>{children}</section>
}

function Label({ className = '', children }) {
  return <h2 className={`mono m-0 text-xs font-normal tracking-[0.2em] text-[#8C9099] ${className}`}>{children}</h2>
}

function Pill({ children }) {
  return (
    <div className="mono absolute left-1/2 top-4 flex max-w-[calc(100%-24px)] -translate-x-1/2 items-center gap-2.5 whitespace-nowrap rounded-full bg-[#0C0D0F]/80 px-4 py-2 text-xs tracking-[0.1em] backdrop-blur-[10px]">
      {children}
    </div>
  )
}

function Check() {
  return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#6EE7B7" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5 10 17 19 7" /></svg>
}

function Bars() {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#7DD3F5" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true" className="shrink-0"><path d="M4 10v4" /><path d="M8 7v10" /><path d="M12 4v16" /><path d="M16 8v8" /><path d="M20 11v2" /></svg>
}

function Ring({ value }) {
  const v = useSpring(0, { stiffness: 80, damping: 18 })
  useEffect(() => { v.set(value ?? 0) }, [value, v])
  const r = 52, c = 2 * Math.PI * r
  const dash = useTransform(v, p => `${c * p} ${c}`)
  const [shown, setShown] = useState(0)
  useEffect(() => v.on('change', p => setShown(Math.round(p * 100))), [v])
  return (
    <div className="relative h-[132px] w-[132px] shrink-0">
      <svg width="132" height="132" viewBox="0 0 140 140" role="img" aria-label={`Task progress ${value == null ? 'unknown' : `${shown} percent`}`}>
        <circle cx="70" cy="70" r="64" fill="none" stroke="rgba(255,255,255,0.14)" strokeWidth="3" strokeDasharray="1 6" />
        <circle cx="70" cy="70" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="9" />
        <motion.circle cx="70" cy="70" r={r} fill="none" stroke="#7DD3F5" strokeWidth="9" strokeLinecap="round" style={{ strokeDasharray: dash }} transform="rotate(-90 70 70)" />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center text-[38px] font-semibold tracking-[-0.03em]">
        {value == null ? '--' : shown}<span className="ml-px mt-3.5 text-[13px] text-[#8C9099]">%</span>
      </div>
    </div>
  )
}

function Spark({ curve, warns, span }) {
  const X = t => (t / span) * 160, Y = p => 38 - p * 34
  const d = curve.map(([t, p], i) => `${i ? 'L' : 'M'}${X(t).toFixed(1)} ${Y(p).toFixed(1)}`).join(' ')
  return (
    <svg width="100%" height="40" viewBox="0 0 160 40" preserveAspectRatio="none" aria-hidden="true" className="mt-1.5">
      <path d={`M0 ${Y(0)} L${X(span)} ${Y(1)}`} fill="none" stroke="rgba(248,113,113,0.5)" strokeWidth="1" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
      {warns.map(t => <line key={t} x1={X(t)} x2={X(t)} y1="0" y2="40" stroke="#fbbf24" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />)}
      <path d={d} fill="none" stroke="#7DD3F5" strokeWidth="1.6" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function Model({ on, name, what, value }) {
  return (
    <div className="flex items-center gap-3.5 border-t border-white/5 px-1 py-2">
      <motion.span className="h-[7px] w-[7px] shrink-0 rounded-full" animate={{ backgroundColor: on ? '#7DD3F5' : '#4A4E56' }} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-[14px] font-medium">{name}</span>
        <span className="truncate text-[12px] text-[#8C9099]">{what}</span>
      </div>
      <span className="mono text-[13px] tabular-nums text-[#7DD3F5]">{value ?? '--'}</span>
    </div>
  )
}

function Dashboard() {
  const [proc, procName, followSession] = useSessionProcedure()
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
      setSaid({ text: m.answer, t: m.t, q: m.question, clip: m.clip?.text })
      setChecks(c => [{ t: m.t, step: m.step, kind: 'ask', askRow: true, question: m.question, answer: m.answer, clip: m.clip }, ...c].slice(0, 60))
      flash('voice', 5000); flash('spark', 1500); flash('asr', 2500)
      setTiming(t => ({ ...t, ...(m.asr_s != null && { asr: m.asr_s }), ...(m.latency_s != null && { spark: m.latency_s }) }))
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
      followSession(m)
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
          setSaid({ text: ev.play.text, t: ev.t, warn: ev.kind === 'warn', cloned: (ev.play.url || '').includes('/voice/') })
          flash('voice', 5000)
          if (soundRef.current) new Audio(ev.play.url).play().catch(() => {})
        }
      }
    }
  })

  useEffect(() => { if (new URLSearchParams(location.search).has('demo')) fetch('/api/demo?speed=2', { method: 'POST' }) }, [])
  const finishedRun = proc && doneAt[proc.steps.length - 1] != null
  useEffect(() => {
    if (finishedRun && !report) setReport(summarize(proc, doneAt, checks.filter(c => !c.warnRow), warnEvents, cost))
  }, [finishedRun]) // eslint-disable-line react-hooks/exhaustive-deps

  const openPicker = () => fetch('/api/sessions').then(r => r.json()).then(setPicker).catch(() => setPicker([]))
  const replay = name => { setPicker(null); fetch(`/api/demo?session=${encodeURIComponent(name)}&speed=${speed}`, { method: 'POST' }) }

  const tracked = novice && placeBox(box, frameSize, feedSize[0], feedSize[1], 'cover')
  const mirrorStep = proc && mirrorT != null ? proc.steps.find(s => mirrorT >= s.t_start && mirrorT <= s.t_end) : null
  const cur = proc?.steps[step]
  const span = proc ? Math.max(proc.steps.at(-1).t_end - proc.steps[0].t_start, now, 30) : 60
  const allDone = proc && done.size === proc.steps.length
  const nChecks = checks.filter(c => !c.warnRow && !c.askRow).length
  const nStills = checks.filter(c => c.source === 'photo').length

  return (
    <MotionConfig reducedMotion="user">
      <div className="dash relative flex min-h-full flex-col gap-[18px] px-4 pb-6 pt-5 sm:px-6 lg:h-full lg:overflow-hidden">
        <AnimatePresence>
          {report && (
            <motion.div key="report" className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <ReportCard report={report} onClose={() => setReport(null)} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* header */}
        <header className="flex flex-wrap items-center justify-between gap-3.5">
          <div className="flex min-w-0 flex-wrap items-center gap-3.5">
            <span className="grid h-9 w-9 place-items-center rounded-[10px] border border-[#7DD3F5]/35 bg-[#14262D]"><span className="h-2.5 w-2.5 rounded-full bg-[#7DD3F5]" /></span>
            <span className="text-xl font-semibold tracking-[-0.02em]">ApprenticeAI</span>
            {proc?.task && <span className="text-[15px] text-[#A0A4AC]">{proc.task}</span>}
          </div>
          <div className="mono flex flex-wrap items-center gap-3 text-xs tracking-[0.08em]">
            <span className="flex h-9 items-center gap-2 rounded-full border border-white/10 bg-[#141518] px-3.5">
              <span className={`h-[7px] w-[7px] rounded-full ${status.live ? 'bg-[#F43F5E]' : status.replay ? 'bg-[#7DD3F5]' : 'bg-[#7C8089]'}`} />
              {status.text.toUpperCase()}
            </span>
            <span className="tracking-[0.02em] text-[#A0A4AC]" title="Model calls this run">${cost.toFixed(2)} · {nChecks} checks · {nStills} stills</span>
            <div className="relative">
              <button type="button" onClick={() => (picker ? setPicker(null) : openPicker())}
                className="h-9 rounded-full border border-white/12 px-4 tracking-[0.08em] text-[#F2F3F5] transition hover:bg-white/5 focus-visible:outline-2 focus-visible:outline-[#7DD3F5]">REPLAY A RUN</button>
              <AnimatePresence>
                {picker && (
                  <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
                    className="absolute right-0 top-11 z-40 w-[min(420px,calc(100vw-32px))] rounded-[20px] border border-white/10 bg-[#131417] p-2 font-sans tracking-normal shadow-2xl">
                    <div className="flex items-center justify-between px-2 py-1.5">
                      <span className="mono text-[11px] tracking-[0.2em] text-[#8C9099]">SAVED RUNS</span>
                      <span className="flex gap-1">{[1, 2, 4].map(x => (
                        <button type="button" key={x} onClick={() => setSpeed(x)}
                          className={`mono rounded-md px-2 py-0.5 text-xs ${speed === x ? 'bg-[#7DD3F5]/20 text-[#B5E6FA]' : 'text-[#8C9099] hover:text-white'}`}>{x}x</button>
                      ))}</span>
                    </div>
                    <ul className="max-h-[60vh] overflow-auto">
                      {!picker.length && <li className="px-2 py-3 text-sm text-[#8C9099]">No saved runs yet. Run a session with the glasses first.</li>}
                      {picker.map(r => (
                        <li key={r.session}>
                          <button type="button" onClick={() => replay(r.session)} className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-white/5">
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium">{r.run || r.session}</span>
                              <span className="mono block text-[11px] text-[#8C9099]">{(r.started || '').replace('T', ' ').slice(5, 16)} · {r.procedure}</span>
                            </span>
                            <span className="mono text-xs tabular-nums text-[#A0A4AC]">{r.duration_s ? fmt(r.duration_s) : '--'}</span>
                            <span className="mono text-xs tabular-nums text-[#6EE7B7]">{r.steps_done} steps</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
            <button type="button" onClick={() => setSound(s => !s)} aria-pressed={sound}
              className={`h-9 rounded-full border px-4 tracking-[0.08em] transition focus-visible:outline-2 focus-visible:outline-[#7DD3F5] ${sound ? 'border-[#7DD3F5]/60 bg-[#7DD3F5]/12 text-[#B5E6FA]' : 'border-white/12 text-[#F2F3F5] hover:bg-white/5'}`}>
              SOUND {sound ? 'ON' : 'OFF'}
            </button>
          </div>
        </header>

        <main className="grid min-h-0 flex-1 grid-cols-1 gap-[18px] lg:grid-cols-[minmax(280px,320px)_minmax(0,1fr)] xl:grid-cols-[minmax(280px,320px)_minmax(0,1fr)_minmax(280px,330px)]">
          {/* left: procedure, models, progress */}
          <aside className="flex min-h-0 flex-col gap-3.5">
            <Card className="px-[18px] pb-3 pt-4">
              <Label className="mb-2.5 px-1">PROCEDURE</Label>
              <ol className="flex flex-col gap-0.5">
                {proc?.steps.map((s, i) => {
                  const isDone = done.has(i), isNow = i === step && !isDone
                  if (isNow) return (
                    <li key={s.id} aria-current="step" className="my-0.5 flex items-center gap-3 rounded-[14px] bg-[#172630] px-3.5 py-2.5">
                      <span className="mono text-[13px] text-[#7DD3F5]">{pad(i + 1)}</span>
                      <span className="flex-1 text-base font-semibold">{s.title}</span>
                      <span className="ripple" aria-hidden />
                    </li>
                  )
                  return (
                    <li key={s.id} className="flex items-center gap-3.5 px-2 py-[7px]">
                      {isDone ? <Check /> : <span className="mono w-[13px] text-center text-[11px] text-[#5C6068]">{i + 1}</span>}
                      <span className={`text-[15px] ${isDone ? 'text-[#A0A4AC]' : 'text-[#70747C]'}`}>{s.title}</span>
                    </li>
                  )
                })}
              </ol>
            </Card>

            <Card className="px-[18px] pb-1.5 pt-4">
              <Label className="mb-1.5 px-1">META MODELS · LIVE</Label>
              <Model on={lit.sam} name="SAM 3.1" what="tracks the workpiece" value={timing.sam ? `${(timing.sam / 1000).toFixed(1)}s` : null} />
              <Model on={lit.dino} name="DINOv2" what="places the work within the step" value={timing.model != null ? `${timing.model}ms` : null} />
              <Model on={lit.spark} name="Muse Spark" what="judges the step, answers questions" value={timing.spark ? `${timing.spark.toFixed(1)}s` : null} />
              <Model on={lit.asr} name="Muse Voice Transcribe" what="hears the novice's question" value={timing.asr ? `${timing.asr.toFixed(1)}s` : null} />
              <Model on={lit.voice && !said?.cloned} name="MMS-TTS" what="speaks the guide and answers" value={said && !said.cloned ? 'speaking' : null} />
            </Card>

          </aside>

          {/* center: novice and expert, equal size, with the spoken caption across both */}
          <section className="relative grid min-h-[480px] grid-cols-2 gap-4 lg:min-h-0">
            <figure ref={feedRef} className={`relative m-0 min-h-[360px] overflow-hidden rounded-[20px] border bg-black ${warn ? 'border-amber-400/70' : 'border-[#7DD3F5]/35'}`}>
              {novice ? <img ref={feedImg} src={novice} alt="Novice's view from the glasses" className="absolute inset-0 h-full w-full object-cover" />
                : <div className="mono grid h-full place-items-center px-6 text-center text-xs tracking-[0.2em] text-[#8C9099]">NO SIGNAL. START THE STREAM ON THE PHONE.</div>}
              {tracked && (
                <motion.div className={`absolute rounded-[10px] border-[1.5px] ${warn ? 'border-amber-400' : 'border-[#7DD3F5]'}`} initial={false} animate={tracked} transition={{ type: 'spring', stiffness: 200, damping: 26 }}>
                  <span className={`mono absolute -top-6 left-0 whitespace-nowrap text-[11px] tracking-[0.08em] ${warn ? 'text-amber-300' : 'text-[#7DD3F5]'}`}>
                    {warn ? 'CHECK THIS' : `WORKPIECE · SAM 3.1${timing.sam ? ` · ${(timing.sam / 1000).toFixed(1)}s` : ''}`}
                  </span>
                </motion.div>
              )}
              <Pill><span className={`h-[9px] w-[9px] rounded-full ${status.live ? 'bg-[#F43F5E]' : 'bg-[#7DD3F5]'}`} />NOVICE · {status.replay ? 'RECORDED' : 'LIVE'} FROM GLASSES · {fmt(now)}</Pill>
            </figure>

            <figure className="relative m-0 min-h-[360px] overflow-hidden rounded-[20px] border border-white/10 bg-black">
              {mirrorT != null && <img src={`/api/mirror/${procName}/${mirrorT.toFixed(1)}.jpg`} alt="The expert at the same point in the task" className="absolute inset-0 h-full w-full object-cover" />}
              <Pill><span className="h-[9px] w-[9px] rounded-full bg-[#A0A4AC]" />EXPERT · SAME POINT{mirrorStep && <span className="truncate tracking-[0.02em] text-[#8C9099]">{mirrorStep.title}</span>}</Pill>
            </figure>

            <AnimatePresence mode="wait">
              {warn ? (
                <motion.div key="warn" role="alert" initial={{ y: 18, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 12, opacity: 0 }} transition={spring}
                  className="absolute bottom-[22px] left-1/2 flex w-[min(760px,calc(100%-32px))] -translate-x-1/2 items-center gap-5 rounded-2xl border border-amber-400/50 bg-[#0C0D0F]/90 px-6 py-4 backdrop-blur-[14px]">
                  <span className="text-4xl font-bold tracking-tight text-amber-400">STOP</span>
                  <div className="min-w-0">
                    <Label className="text-amber-300">MUSE SPARK · CHECK AGAINST THE EXPERT</Label>
                    <p className="mt-1 text-lg font-medium">{warn.what || 'Check your work against the expert\'s.'}</p>
                  </div>
                </motion.div>
              ) : said && (
                <motion.div key={said.text} initial={{ y: 14, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ opacity: 0 }} transition={spring}
                  className="absolute bottom-[22px] left-1/2 flex w-[min(720px,calc(100%-32px))] -translate-x-1/2 items-center gap-3.5 rounded-2xl border border-white/8 bg-[#0C0D0F]/86 px-[22px] py-3.5 backdrop-blur-[14px]">
                  <Bars />
                  <div className="flex-1 text-center">
                    {said.q && <p className="mb-1 text-sm text-[#A0A4AC]" lang={hindi(said.q) ? 'hi' : undefined}>Asked: “{said.q}”</p>}
                    {said.clip && <p className="mb-1 text-[15px] italic text-[#B5E6FA]"><span className="mono not-italic mr-2 text-[11px] tracking-[0.1em] text-[#7DD3F5]">EXPERT, RECORDED</span>“{said.clip}”</p>}
                    <p className="text-lg font-medium leading-[1.45]" lang={hindi(said.text) ? 'hi' : undefined}>“{said.text}”</p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </section>

          {/* right: progress, then every Spark answer and its reason */}
          <div className="flex min-h-0 flex-col gap-3.5 lg:col-span-2 xl:col-span-1">
            <Card className="flex items-center gap-[18px] p-[18px]">
    <Ring value={progress} />
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <Label>TASK PROGRESS</Label>
      <span className="text-[13px] leading-snug text-[#A0A4AC]">Step from Muse Spark, position within it from DINOv2</span>
      <Spark curve={curve} warns={warns} span={span} />
    </div>
  </Card>
            <Card className="flex min-h-[320px] flex-1 flex-col px-[18px] pb-3 pt-5 xl:min-h-0">
            <div className="mb-2.5 flex items-baseline justify-between px-1">
              <Label>MUSE SPARK LOG</Label>
              <span className="mono text-[11px] text-[#5C6068]">newest first</span>
            </div>
            {!checks.length && <p className="px-1 text-sm text-[#8C9099]">Each check's answer and reason appears here as Muse Spark returns it.</p>}
            <ul className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
              <AnimatePresence initial={false}>
                {checks.map((c, i) => (
                  <motion.li key={`${c.t}-${i}-${c.kind}`} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
                    className={`rounded-xl px-3 py-2 text-[13px] ${c.warnRow ? 'bg-amber-400/10' : c.askRow ? 'bg-[#172630]' : 'bg-[#17181C]'}`}>
                    <div className="mono flex items-center gap-x-2 whitespace-nowrap text-[11px] text-[#8C9099]">
                      <span className="tabular-nums">{fmt(c.t)}</span>
                      <span>s{pad(c.step + 1)}</span>
                      {c.askRow ? <span className="text-[#7DD3F5]">ASKED</span> : c.warnRow ? <span className="text-amber-300">WARNING</span>
                        : <span className={c.unclear ? 'text-[#8C9099]' : c.check === 'done' ? (c.completed > c.step ? 'text-[#6EE7B7]' : 'text-[#A0A4AC]') : c.yes ? 'text-amber-300' : 'text-[#A0A4AC]'}>{verdict(c)}</span>}
                      {c.conf != null && <span>{Math.round(c.conf * 100)}%</span>}
                      <span className="flex-1" />
                      {c.latency_s != null && <span className="tabular-nums text-[#7DD3F5]">{c.latency_s.toFixed(1)}s</span>}
                    </div>
                    <p className="mt-0.5 leading-snug text-[#D4D6DA]">
                      {(c.trigger || c.source === 'photo') && (
                        <span className="mono mr-1.5 text-[11px] text-[#7DD3F5]">{[c.trigger && (c.trigger === 'settle' ? 'hands off' : 'tapped'), c.source === 'photo' && 'still'].filter(Boolean).join(' · ')}</span>
                      )}{c.askRow ? <>“{c.question}”{c.clip && <> <span className="text-[#7DD3F5]">plays the expert at {fmt(c.clip.start)}:</span> “{c.clip.text}”</>} <span className="text-[#8C9099]">answered:</span> {c.answer}</> : c.warnRow ? c.what : c.reason}</p>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
            </Card>
          </div>
        </main>

        {/* current step */}
        <footer className="flex flex-wrap items-center gap-6 rounded-[20px] border border-white/6 bg-[#131417] px-7 py-[22px]">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span key={step} initial={{ y: 12, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -12, opacity: 0 }} transition={spring}
              className="mono text-5xl leading-none text-[#7DD3F5]">{pad(step + 1)}</motion.span>
          </AnimatePresence>
          <div className="flex min-w-0 flex-[1_1_400px] flex-col gap-1.5">
            <span className="text-[26px] font-semibold tracking-[-0.02em]">{cur?.title ?? 'Waiting for the first step'}</span>
            {cur && <span className="truncate text-base text-[#A0A4AC]" lang={hindi(cur.instruction) ? 'hi' : undefined}>{cur.instruction}</span>}
          </div>
          {proc && (
            <div role="img" aria-label={`Step ${step + 1} of ${proc.steps.length}`} className="grid flex-[0_1_360px] gap-1.5" style={{ gridTemplateColumns: `repeat(${proc.steps.length}, minmax(0, 1fr))` }}>
              {proc.steps.map((_, i) => {
                const d = allDone || done.has(i), n = !d && i === step
                return <div key={i} className="h-1 rounded" style={{ background: d ? '#6EE7B7' : n ? `linear-gradient(90deg, #7DD3F5 ${Math.round(withinStep(progress, proc, i) * 100)}%, rgba(255,255,255,0.1) 0)` : 'rgba(255,255,255,0.1)' }} />
              })}
            </div>
          )}
        </footer>
      </div>
    </MotionConfig>
  )
}

createRoot(document.getElementById('root')).render(<StrictMode><Dashboard /></StrictMode>)
