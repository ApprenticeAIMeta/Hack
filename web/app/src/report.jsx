import { motion } from 'framer-motion'
import { fmt } from './shared.js'

// The run's report card: how the novice's time on each step compares with the expert's, what the
// models did, and what it cost. Shown on the phone and the dashboard when the procedure completes.
// Every number comes from events the session already publishes.
export function summarize(proc, doneAt, checks, warns, cost) {
  if (!proc) return null
  const steps = proc.steps.map((s, i) => {
    const start = i ? doneAt[i - 1] : 0
    const end = doneAt[i]
    return {
      title: s.title,
      novice: end != null && start != null ? Math.max(0, end - start) : null,
      expert: s.t_end - s.t_start,
    }
  })
  const photos = checks.filter(c => c.source === 'photo').length
  const total = doneAt[proc.steps.length - 1]
  const expertTotal = proc.steps.at(-1).t_end - proc.steps[0].t_start
  return { steps, photos, checks: checks.length, warns: warns.length, cost, total, expertTotal }
}

export function ReportCard({ report, compact = false, onClose }) {
  if (!report) return null
  const max = Math.max(...report.steps.map(s => Math.max(s.novice ?? 0, s.expert)), 1)
  const ratio = report.total && report.expertTotal ? report.total / report.expertTotal : null
  return (
    <motion.div initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ type: 'spring', stiffness: 260, damping: 28 }}
      className={`rounded-2xl border border-white/10 bg-zinc-950/85 p-5 text-left shadow-[0_8px_40px_rgba(0,0,0,.5)] backdrop-blur-xl ${compact ? 'w-full' : 'w-[560px]'}`}>
      <div className="mb-3 flex items-baseline justify-between">
        <p className="font-mono text-[11px] tracking-[0.2em] text-emerald-300">RUN REPORT</p>
        {ratio != null && <p className="font-mono text-xs text-white/60">{fmt(report.total)} · {ratio.toFixed(1)}× the expert's time</p>}
      </div>
      <ol className="flex flex-col gap-2">
        {report.steps.map((s, i) => (
          <li key={i} className="grid grid-cols-[1.4rem_1fr_3.2rem] items-center gap-x-2 gap-y-1">
            <span className="font-mono text-xs text-white/50">{String(i + 1).padStart(2, '0')}</span>
            <span className={`truncate ${compact ? 'text-sm' : 'text-[15px]'}`}>{s.title}</span>
            <span className="text-right font-mono text-xs tabular-nums text-cyan-200">{s.novice != null ? `${Math.round(s.novice)}s` : '--'}</span>
            <span />
            <div className="col-span-2 flex flex-col gap-0.5">
              <motion.i className="block h-1.5 rounded-full bg-cyan-300" initial={{ width: 0 }} animate={{ width: `${((s.novice ?? 0) / max) * 100}%` }} transition={{ delay: 0.1 + i * 0.08, duration: 0.5 }} />
              <i className="block h-1 rounded-full bg-rose-400/60" style={{ width: `${(s.expert / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-3 flex gap-4 font-mono text-[11px] text-white/50">
        <span><i className="mr-1.5 inline-block h-1.5 w-4 rounded-full bg-cyan-300 align-middle" />you</span>
        <span><i className="mr-1.5 inline-block h-1 w-4 rounded-full bg-rose-400/60 align-middle" />expert</span>
      </div>
      <div className={`mt-4 grid gap-2 ${compact ? 'grid-cols-2' : 'grid-cols-4'}`}>
        {[['Spark checks', report.checks], ['stills used', report.photos], ['warnings', report.warns], ['cost', `$${(report.cost ?? 0).toFixed(2)}`]].map(([k, v]) => (
          <div key={k} className="rounded-xl bg-white/[.04] px-3 py-2">
            <p className="font-mono text-lg tabular-nums">{v}</p>
            <p className="text-[11px] text-white/50">{k}</p>
          </div>
        ))}
      </div>
      {onClose && <button onClick={onClose} className="mt-4 w-full rounded-xl border border-white/15 py-2 text-sm text-white/70 hover:text-white">Close</button>}
    </motion.div>
  )
}
