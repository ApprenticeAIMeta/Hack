import { StrictMode, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { motion } from 'framer-motion'
import './index.css'
import { PROCEDURE, fmt } from './shared.js'

// What one expert recording became, for the builder and the demo: his take on the glasses, his words
// as Muse Voice Transcribe timed them, the steps Muse Spark wrote from both, and every line in his
// cloned voice (or, with ES, the same guide translated by Muse Spark and spoken by MMS-TTS).
// Everything here is read from the compiled procedure and the files beside it.
const pad = (n) => String(n).padStart(2, '0')
const Label = ({ children, className = '' }) => <p className={`mono text-[11px] tracking-[0.18em] text-[#8C9099] ${className}`}>{children}</p>
const Card = ({ className = '', children }) => <section className={`rounded-[20px] border border-white/6 bg-[#131417] ${className}`}>{children}</section>

let current = null
function play(url, onEnd) {
  if (current) { current.pause(); current.onended?.() }
  const a = new Audio(url); current = a
  a.onended = () => { if (current === a) current = null; onEnd?.() }
  a.play().catch(() => onEnd?.())
  return a
}

function PlayButton({ url, label, tone = 'cyan', small = false }) {
  const [on, setOn] = useState(false)
  const c = tone === 'amber' ? 'text-amber-300 bg-amber-400/12' : tone === 'green' ? 'text-[#6EE7B7] bg-[#6EE7B7]/12' : 'text-[#7DD3F5] bg-[#7DD3F5]/12'
  return (
    <button onClick={() => { if (on) { current?.pause(); current = null; setOn(false) } else { setOn(true); play(url, () => setOn(false)) } }}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 ${small ? 'py-0.5 text-[12px]' : 'py-1 text-[13px]'} font-medium ${c} hover:brightness-125 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#7DD3F5]`}>
      <span aria-hidden className="text-[10px]">{on ? '■' : '▶'}</span>{label}
    </button>
  )
}

/** The voice sample's waveform, drawn from the audio itself. */
function Wave({ url, progress }) {
  const [peaks, setPeaks] = useState(null)
  useEffect(() => {
    fetch(url).then(r => r.arrayBuffer()).then(b => new AudioContext().decodeAudioData(b)).then(buf => {
      const d = buf.getChannelData(0), n = 90, step = Math.floor(d.length / n), out = []
      for (let i = 0; i < n; i++) { let m = 0; for (let j = i * step; j < (i + 1) * step; j += 16) m = Math.max(m, Math.abs(d[j])); out.push(m) }
      const top = Math.max(...out, 1e-6); setPeaks(out.map(v => v / top))
    }).catch(() => {})
  }, [url])
  if (!peaks) return <div className="h-16" />
  return (
    <div className="flex h-16 items-center gap-[2px]" aria-hidden>
      {peaks.map((v, i) => <i key={i} className="flex-1 rounded-full" style={{ height: `${8 + v * 92}%`, background: i / peaks.length <= progress ? '#6EE7B7' : 'rgba(255,255,255,0.16)' }} />)}
    </div>
  )
}

function VoiceSample({ name, lines, lang }) {
  const url = `/api/voice_sample/${name}`
  const [p, setP] = useState(0), [on, setOn] = useState(false), [dur, setDur] = useState(null)
  const audio = useRef(null)
  useEffect(() => { const a = new Audio(url); a.onloadedmetadata = () => setDur(a.duration) }, [url])
  const toggle = () => {
    if (on) { audio.current?.pause(); setOn(false); return }
    const a = play(url, () => { setOn(false); setP(0) }); audio.current = a; setOn(true)
    const tick = () => { if (!a.paused) { setP(a.currentTime / a.duration); requestAnimationFrame(tick) } }
    requestAnimationFrame(tick)
  }
  return (
    <Card className="p-5">
      <Label className="text-[#6EE7B7]">HIS VOICE</Label>
      <p className="mt-2 text-[15px] leading-snug text-[#D4D6DA]">{dur ? `${Math.round(dur)} seconds` : 'A short stretch'} of his own recording, taken from the same take.</p>
      <div className="mt-3 flex items-center gap-3">
        <button onClick={toggle} aria-label={on ? 'Stop the sample' : 'Play the sample'}
          className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[#6EE7B7] text-[#0C0D0F] hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#6EE7B7]">{on ? '■' : '▶'}</button>
        <div className="min-w-0 flex-1"><Wave url={url} progress={p} /></div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="rounded-xl bg-[#17181C] p-3">
          <p className="text-3xl font-semibold tabular-nums">{lang === 'en' ? lines : '0'}</p>
          <p className="mt-0.5 text-[12px] leading-snug text-[#A0A4AC]">{lang === 'en' ? 'lines spoken in his cloned voice' : 'lines in his voice in Spanish'}</p>
        </div>
        <div className="rounded-xl bg-[#17181C] p-3">
          <p className="text-3xl font-semibold tabular-nums">{lang === 'en' ? 'his' : 'MMS'}</p>
          <p className="mt-0.5 text-[12px] leading-snug text-[#A0A4AC]">{lang === 'en' ? 'own recordings answer questions' : 'Meta MMS-TTS speaks Spanish'}</p>
        </div>
      </div>
      <p className="mt-3 text-[12px] leading-snug text-[#8C9099]">
        {lang === 'en' ? 'Cloned with Chatterbox, open weights, on this laptop, with his written consent.' : 'Muse Spark translated the guide; the checks still compare against his work.'}
      </p>
    </Card>
  )
}

function Runs({ name, n }) {
  const [runs, setRuns] = useState([])
  useEffect(() => { fetch('/api/sessions').then(r => r.json()).then(all => setRuns(all.filter(r => r.procedure === name))).catch(() => {}) }, [name])
  const done = runs.filter(r => r.complete && r.steps_done === n && r.finished_s)   // runs of this version of the guide
  return (
    <Card className="flex min-h-0 flex-1 flex-col p-5">
      <div className="flex items-baseline justify-between">
        <Label>RUNS WITH THIS GUIDE</Label>
        <span className="mono text-[11px] text-[#5C6068]">from out/sessions</span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {[[runs.length, 'runs recorded'], [done.length, `finished all ${n} steps`], [done.length ? fmt(Math.min(...done.map(r => r.finished_s))) : '--', `fastest, all ${n} steps`]].map(([v, l]) => (
          <div key={l} className="rounded-xl bg-[#17181C] p-3"><p className="text-2xl font-semibold tabular-nums">{v}</p><p className="text-[12px] leading-snug text-[#A0A4AC]">{l}</p></div>
        ))}
      </div>
      <ul className="mt-3 min-h-0 flex-1 space-y-1 overflow-auto pr-1">
        {runs.map(r => (
          <li key={r.session} className="mono grid grid-cols-[1fr_auto_auto_auto] items-center gap-3 rounded-lg bg-[#17181C] px-3 py-1.5 text-[12px]">
            <span className="truncate text-[#D4D6DA]">{r.run || r.session}</span>
            <span className="tabular-nums text-[#A0A4AC]">{r.finished_s ? fmt(r.finished_s) : r.duration_s ? fmt(r.duration_s) : '--'}</span>
            <span className="tabular-nums text-[#A0A4AC]">{r.steps_done} steps</span>
            <span className={r.complete ? 'text-[#6EE7B7]' : 'text-[#5C6068]'}>{r.complete ? 'done' : 'stopped'}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function Build() {
  const name = PROCEDURE === 'airplane' && !new URLSearchParams(location.search).get('procedure') ? 'thermocouple' : PROCEDURE
  const [proc, setProc] = useState(null)
  const [local, setLocal] = useState(null)
  const [lang, setLang] = useState(new URLSearchParams(location.search).get('lang') || 'en')
  const [lines, setLines] = useState({})
  const [t, setT] = useState(0)
  const [sel, setSel] = useState(null)
  const video = useRef(null), list = useRef(null)
  useEffect(() => { fetch(`/api/procedure/${name}`).then(r => r.json()).then(setProc) }, [name])
  useEffect(() => { fetch(`/procedures/${name}/voice/lines.json`).then(r => r.ok ? r.json() : {}).then(setLines).catch(() => {}) }, [name])
  useEffect(() => { if (lang === 'en') setLocal(null); else fetch(`/api/procedure/${name}?lang=${lang}`).then(r => r.json()).then(setLocal) }, [name, lang])

  const turns = proc?.transcript ?? []
  const at = turns.findLast?.(u => u.start <= t) ?? null
  const stepAt = proc ? proc.steps.findIndex(s => t >= s.t_start && t < s.t_end) : -1
  const active = sel ?? (stepAt >= 0 ? stepAt : null)
  useEffect(() => {
    const el = list.current?.querySelector(`[data-turn="${at?.id}"]`)
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [at?.id])
  const seek = (s) => { if (video.current) { video.current.currentTime = s; video.current.play().catch(() => {}) } }
  const warnTurns = useMemo(() => new Set(turns.filter(u => /mistake|polarity|wrong/i.test(u.text)).map(u => u.id)), [turns])

  if (!proc) return <div className="dash grid h-full place-items-center text-[#8C9099]">Loading the guide…</div>
  const steps = (local?.steps ?? proc.steps)
  const voiceLines = Object.keys(lines).length
  const words = turns.reduce((n, u) => n + u.text.split(/\s+/).length, 0)
  const say = (k) => lang === 'en' ? `/procedures/${name}/voice/step_${pad(k + 1)}_say.wav` : `/procedures/${name}/clips/${lang}/step_${pad(k + 1)}_say.wav`
  const warnUrl = (k) => lang === 'en' ? (lines[`step_${pad(k + 1)}_warn1`] ? `/procedures/${name}/voice/step_${pad(k + 1)}_warn1.wav` : null) : `/procedures/${name}/clips/${lang}/step_${pad(k + 1)}_warn1.wav`

  return (
    <div className="dash flex h-full flex-col gap-4 overflow-hidden p-6">
      <header className="flex items-center gap-4">
        <svg viewBox="0 0 104 104" className="h-9 w-9 shrink-0" aria-hidden><rect width="104" height="104" rx="24" fill="#14262D" stroke="#7DD3F5" strokeOpacity=".35" strokeWidth="3" /><circle cx="52" cy="52" r="31" fill="none" stroke="#7DD3F5" strokeWidth="6" /><path d="M40 65 52 38 64 65M45 56h14" fill="none" stroke="#7DD3F5" strokeWidth="7.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        <div className="flex min-w-0 items-baseline gap-3">
          <h1 className="text-[22px] font-semibold tracking-[-0.01em]">ApprenticeAI</h1>
          <span className="truncate text-[15px] text-[#A0A4AC]">Guide built from one recording · {local?.task ?? proc.task}</span>
        </div>
        <span className="flex-1" />
        <div role="group" aria-label="Language" className="mono flex rounded-full border border-white/10 p-1 text-[12px]">
          {['en', 'es'].map(l => (
            <button key={l} onClick={() => setLang(l)} aria-pressed={lang === l}
              className={`rounded-full px-3 py-1 tracking-[0.12em] ${lang === l ? 'bg-[#7DD3F5] text-[#0C0D0F]' : 'text-[#A0A4AC] hover:text-white'}`}>{l === 'en' ? 'ENGLISH' : 'ESPAÑOL'}</button>
          ))}
        </div>
      </header>

      {/* the pipeline, with what each stage actually produced */}
      <ol className="grid grid-cols-4 gap-3">
        {[
          ['Ray-Ban Meta', `one take · ${fmt(proc.source.duration_s)}`, 'Device Access Toolkit, his glasses view'],
          ['Muse Voice Transcribe', `${turns.length} lines · ${words} words`, 'every utterance timed to the second'],
          ['Muse Spark 1.3', `${proc.steps.length} steps · ${fmt(proc.compile.wall_s)}`, `strict schema, $${(proc.compile.cost_usd_this_run.steps + proc.compile.cost_usd_this_run.transcribe).toFixed(2)} to compile`],
          lang === 'en' ? ['His voice', `${voiceLines} lines`, 'cloned from his own take'] : ['Spanish', `${steps.length} steps translated`, 'Muse Spark + MMS-TTS'],
        ].map(([a, b, c], i) => (
          <li key={a} className="flex items-center gap-3 rounded-2xl border border-white/6 bg-[#131417] px-4 py-3">
            <span className="mono text-[13px] text-[#7DD3F5]">{pad(i + 1)}</span>
            <div className="min-w-0"><p className="text-[15px] font-semibold">{a}</p><p className="mono text-[12px] text-[#7DD3F5]">{b}</p><p className="truncate text-[12px] text-[#8C9099]">{c}</p></div>
          </li>
        ))}
      </ol>

      <main className="grid min-h-0 flex-1 grid-cols-[minmax(0,5fr)_minmax(0,4.4fr)_minmax(0,3.2fr)] gap-4">
        {/* his take and his words */}
        <div className="grid min-h-0 grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-3">
          <Card className="relative min-h-0 overflow-hidden">
            <video ref={video} src={`/api/source/${name}`} className="h-full w-full bg-black object-cover" controls playsInline
              onTimeUpdate={e => setT(e.currentTarget.currentTime)} onSeeked={e => setT(e.currentTarget.currentTime)} />
            <span className="mono absolute left-3 top-3 rounded-full bg-black/70 px-3 py-1 text-[11px] tracking-[0.12em] text-white">EXPERT · HIS GLASSES · {fmt(t)}</span>
          </Card>
          <Card className="flex min-h-0 flex-col p-4">
            <div className="mb-2 flex items-baseline justify-between"><Label>HIS WORDS, TIMED</Label><span className="mono text-[11px] text-[#5C6068]">Voice Transcribe</span></div>
            <ul ref={list} className="min-h-0 flex-1 space-y-0.5 overflow-auto pr-1">
              {turns.map(u => {
                const now = at?.id === u.id, inStep = active != null && u.start >= proc.steps[active].t_start - 0.5 && u.start < proc.steps[active].t_end
                const warn = warnTurns.has(u.id)
                return (
                  <li key={u.id} data-turn={u.id}>
                    <button onClick={() => { setSel(null); seek(u.start) }}
                      className={`grid w-full grid-cols-[2.6rem_1fr] gap-2 rounded-lg px-2 py-1 text-left text-[13px] leading-snug ${now ? 'bg-[#7DD3F5]/14 text-white' : inStep ? 'bg-white/[0.04] text-[#D4D6DA]' : 'text-[#8C9099]'} ${warn ? '!text-amber-300' : ''}`}>
                      <span className="mono text-[11px] tabular-nums opacity-70">{fmt(u.start)}</span><span>{u.text}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </Card>
        </div>

        {/* the steps it became */}
        <Card className="flex min-h-0 flex-col p-4">
          <div className="mb-2 flex items-baseline justify-between px-1"><Label>THE GUIDE MUSE SPARK WROTE</Label><span className="mono text-[11px] text-[#5C6068]">{lang === 'en' ? 'in his voice' : 'en español'}</span></div>
          <ol className="min-h-0 flex-1 space-y-2 overflow-auto pr-1">
            {proc.steps.map((s, k) => {
              const ls = steps[k] ?? s, on = active === k
              const ref = s.reference_frame?.crop
              return (
                <motion.li key={s.id} layout onClick={() => { setSel(k); seek(s.t_start) }}
                  className={`grid cursor-pointer grid-cols-[96px_1fr] gap-3 rounded-2xl border p-2.5 ${on ? 'border-[#7DD3F5]/50 bg-[#17202A]' : 'border-transparent bg-[#17181C] hover:bg-[#1B1C21]'}`}>
                  {ref ? <img src={`/procedures/${name}/${ref}`} alt="" className="h-[96px] w-[96px] rounded-xl object-cover" />
                    : <div className="grid h-[96px] w-[96px] place-items-center rounded-xl bg-[#131417] text-[11px] text-[#5C6068]">no photo</div>}
                  <div className="min-w-0">
                    <div className="flex items-baseline gap-2">
                      <span className="mono text-[12px] text-[#7DD3F5]">{pad(k + 1)}</span>
                      <span className="truncate text-[16px] font-semibold">{ls.title}</span>
                      <span className="flex-1" />
                      <span className="mono shrink-0 text-[11px] tabular-nums text-[#8C9099]">{fmt(s.t_start)}–{fmt(s.t_end)}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[13px] leading-snug text-[#D4D6DA]" lang={lang}>“{lang === 'en' ? (lines[`step_${pad(k + 1)}_say`] || s.spoken || s.instruction) : ls.instruction}”</p>
                    <div className="mt-1.5 flex flex-wrap gap-1.5" onClick={e => e.stopPropagation()}>
                      <PlayButton small url={say(k)} label={lang === 'en' ? 'his voice' : 'escuchar'} tone="green" />
                      {lang === 'en' && s.voice?.[0] && <PlayButton small url={`/procedures/${name}/${s.voice[0].file}`} label="his recording" />}
                      {s.expert_warnings?.length > 0 && warnUrl(k) && <PlayButton small url={warnUrl(k)} label={lang === 'en' ? 'his warning' : 'advertencia'} tone="amber" />}
                      {s.verifiable === false && <span className="rounded-full bg-white/6 px-2.5 py-0.5 text-[12px] text-[#8C9099]">not checked by picture</span>}
                    </div>
                  </div>
                </motion.li>
              )
            })}
          </ol>
        </Card>

        <div className="flex min-h-0 flex-col gap-4">
          <VoiceSample name={name} lines={voiceLines} lang={lang} />
          <Runs name={name} n={proc.steps.length} />
        </div>
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<StrictMode><Build /></StrictMode>)
