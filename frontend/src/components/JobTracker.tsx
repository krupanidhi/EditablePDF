import { useState, useEffect, useRef } from 'react';
import { getJob, getDownloadUrl } from '../api';
import type { Job, AuditCheck } from '../types';
import {
  Loader2, CheckCircle2, XCircle, Download, FileJson,
  FileText, ChevronDown, ChevronRight, ShieldCheck,
  ListChecks, AlertTriangle, Info, Trash2,
} from 'lucide-react';

interface JobTrackerProps {
  jobId: string;
  onComplete?: (job: Job) => void;
  onDelete?: () => void;
}

/* ── tiny helpers ── */
const statusIcon = (s: AuditCheck['status']) => {
  switch (s) {
    case 'pass': return <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />;
    case 'fail': return <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />;
    case 'warn': return <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0" />;
    default:     return <Info className="w-3.5 h-3.5 text-blue-400 shrink-0" />;
  }
};
const statusBg = (s: AuditCheck['status']) => {
  switch (s) {
    case 'pass': return 'bg-green-50';
    case 'fail': return 'bg-red-50';
    case 'warn': return 'bg-amber-50';
    default:     return 'bg-blue-50';
  }
};

const typeBadgeColor = (t: string) => {
  switch (t) {
    case 'radio':    return 'bg-purple-50 text-purple-700 border-purple-200';
    case 'checkbox': return 'bg-amber-50 text-amber-700 border-amber-200';
    case 'textarea': return 'bg-blue-50 text-blue-700 border-blue-200';
    case 'date':     return 'bg-teal-50 text-teal-700 border-teal-200';
    case 'currency': return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    default:         return 'bg-gray-50 text-gray-600 border-gray-200';
  }
};

/* ── score ring (tiny SVG) ── */
function ScoreRing({ score }: { score: number }) {
  const r = 18, c = 2 * Math.PI * r;
  const offset = c - (score / 100) * c;
  const color = score >= 90 ? '#16a34a' : score >= 70 ? '#d97706' : '#dc2626';
  return (
    <div className="relative w-12 h-12">
      <svg viewBox="0 0 44 44" className="w-12 h-12 -rotate-90">
        <circle cx="22" cy="22" r={r} fill="none" stroke="#e5e7eb" strokeWidth="4" />
        <circle cx="22" cy="22" r={r} fill="none" stroke={color} strokeWidth="4"
          strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }} />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[11px] font-bold" style={{ color }}>
        {score}%
      </span>
    </div>
  );
}

/* ── collapsible section ── */
function Section({ title, icon, defaultOpen, children }: {
  title: string; icon: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="border border-[#D9E8F6] rounded-lg overflow-hidden">
      <button onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-[#EFF6FB] hover:bg-[#e2eef8] transition-colors text-left">
        {open ? <ChevronDown className="w-3.5 h-3.5 text-[#64748b]" /> : <ChevronRight className="w-3.5 h-3.5 text-[#64748b]" />}
        {icon}
        <span className="text-xs font-semibold text-[#0B4778]">{title}</span>
      </button>
      {open && <div className="px-3 py-2.5">{children}</div>}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════ */
export default function JobTracker({ jobId, onComplete, onDelete }: JobTrackerProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await getJob(jobId);
        if (cancelled) return;
        setJob(data);
        if (data.status === 'completed' || data.status === 'failed') {
          if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
          if (data.status === 'completed' && onComplete) onComplete(data);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to fetch job status');
      }
    };
    poll();
    intervalRef.current = setInterval(poll, 2000);
    return () => { cancelled = true; if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [jobId, onComplete]);

  /* ── error state ── */
  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-center gap-3">
        <XCircle className="w-5 h-5 text-red-500 shrink-0" />
        <div>
          <p className="text-sm font-medium text-red-800">Error</p>
          <p className="text-xs text-red-600">{error}</p>
        </div>
      </div>
    );
  }

  /* ── loading state ── */
  if (!job) {
    return (
      <div className="bg-[#EFF6FB] border border-[#D9E8F6] rounded-lg p-4 flex items-center gap-3">
        <Loader2 className="w-5 h-5 text-[#94a3b8] animate-spin" />
        <p className="text-sm text-[#0B4778]">Loading job status...</p>
      </div>
    );
  }

  const audit = job.result?.audit;
  const fieldsDetail = job.result?.fields_detail;

  /* ── main render ── */
  return (
    <div className="bg-white border border-[#D9E8F6] rounded-lg shadow-sm overflow-hidden">
      {/* ─── Collapsible Header ─── */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between bg-white hover:bg-[#f8fafc] transition-colors border-b border-[#D9E8F6]"
      >
        <div className="flex items-center gap-2.5">
          {expanded
            ? <ChevronDown className="w-4 h-4 text-[#94a3b8]" />
            : <ChevronRight className="w-4 h-4 text-[#94a3b8]" />}
          {job.status === 'processing' && <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />}
          {job.status === 'completed' && <CheckCircle2 className="w-4 h-4 text-green-500" />}
          {job.status === 'failed' && <XCircle className="w-4 h-4 text-red-500" />}
          <span className="text-sm font-semibold text-[#0B4778]">
            {job.input_file || `Job ${job.id}`}
          </span>
          {job.result?.stats && (
            <span className="text-[10px] text-[#94a3b8] font-normal">
              {job.result.stats.pages}p · {job.result.stats.total_fields} fields · {job.result.stats.processing_time_sec}s
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {audit && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
              audit.summary.score >= 90 ? 'bg-green-100 text-green-700' :
              audit.summary.score >= 70 ? 'bg-amber-100 text-amber-700' :
              'bg-red-100 text-red-700'
            }`}>
              {audit.summary.score}% quality
            </span>
          )}
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
            job.status === 'processing' ? 'bg-blue-100 text-blue-700' :
            job.status === 'completed' ? 'bg-green-100 text-green-700' :
            'bg-red-100 text-red-700'
          }`}>
            {job.status}
          </span>
          {onDelete && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              className="p-1 rounded text-[#94a3b8] hover:text-red-500 hover:bg-red-50 transition-colors"
              title="Delete job"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </button>

      {/* ─── Expanded Body ─── */}
      {expanded && (
        <div className="p-4 space-y-3">
          {/* Processing indicator */}
          {job.status === 'processing' && !job.result && (
            <div className="flex items-center gap-3">
              <div className="flex-1 bg-[#D9E8F6] rounded-full h-1.5 overflow-hidden">
                <div className="bg-[#3b82f6] h-full rounded-full animate-pulse w-2/3" />
              </div>
              <span className="text-xs text-[#64748b]">{job.input_file || 'Processing...'}</span>
            </div>
          )}

          {/* Single error */}
          {job.error && <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3">{job.error}</div>}

          {/* ── Completed single file ── */}
          {job.result && (
            <>
              {/* Download buttons + quick stats (conversion jobs only) */}
              {job.result.editable_pdf && (
              <div className="flex items-center justify-between flex-wrap gap-3">
                <div className="flex gap-2">
                  <a href={getDownloadUrl(job.result.editable_pdf.split(/[\\/]/).pop() || '')}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#0B4778] text-white text-xs font-medium rounded-md hover:bg-[#093d66] transition-colors"
                    download>
                    <Download className="w-3.5 h-3.5" /> Editable PDF
                  </a>
                  {job.result.schema && (
                  <a href={getDownloadUrl(job.result.schema.split(/[\\/]/).pop() || '')}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#EFF6FB] text-[#0B4778] text-xs font-medium rounded-md hover:bg-[#D9E8F6] transition-colors border border-[#D9E8F6]"
                    download>
                    <FileJson className="w-3.5 h-3.5" /> Schema JSON
                  </a>
                  )}
                </div>
                {/* Field type badges */}
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(job.result.stats?.by_type ?? {}).map(([k, v]) => (
                    <span key={k} className={`px-2 py-0.5 text-[10px] font-medium rounded-full border ${typeBadgeColor(k)}`}>
                      {k} × {v}
                    </span>
                  ))}
                </div>
              </div>
              )}

              {/* ── Quality Score + Compliance ── */}
              {audit && (
                <Section title={`Quality & 508 Compliance — ${audit.summary.passed}/${audit.summary.total} passed`}
                  icon={<ShieldCheck className="w-3.5 h-3.5 text-[#0B4778]" />}
                  defaultOpen>
                  <div className="flex items-start gap-4">
                    {/* Score ring */}
                    <ScoreRing score={audit.summary.score} />
                    {/* Counts */}
                    <div className="flex gap-4 text-xs pt-1">
                      <div className="text-center">
                        <div className="text-lg font-bold text-green-600">{audit.summary.passed}</div>
                        <div className="text-[#64748b]">Passed</div>
                      </div>
                      <div className="text-center">
                        <div className="text-lg font-bold text-red-600">{audit.summary.failed}</div>
                        <div className="text-[#64748b]">Failed</div>
                      </div>
                      <div className="text-center">
                        <div className="text-lg font-bold text-amber-600">{audit.summary.warnings}</div>
                        <div className="text-[#64748b]">Warnings</div>
                      </div>
                    </div>
                  </div>

                  {/* Check list */}
                  <div className="mt-3 space-y-0.5">
                    {/* 508 checks */}
                    <p className="text-[10px] uppercase tracking-wider text-[#94a3b8] font-semibold mt-2 mb-1">Section 508 Compliance</p>
                    {audit.checks.filter(c => c.category === '508').map((c, i) => (
                      <div key={i} className={`flex items-center gap-2 px-2 py-1 rounded text-xs ${statusBg(c.status)}`}>
                        {statusIcon(c.status)}
                        <span className="font-medium text-[#0B4778] w-48 shrink-0">{c.check}</span>
                        <span className="text-[#64748b] truncate" title={c.detail}>{c.detail}</span>
                      </div>
                    ))}
                    {/* Widget checks */}
                    <p className="text-[10px] uppercase tracking-wider text-[#94a3b8] font-semibold mt-3 mb-1">Widget Properties</p>
                    {audit.checks.filter(c => c.category === 'widget').map((c, i) => (
                      <div key={i} className={`flex items-center gap-2 px-2 py-1 rounded text-xs ${statusBg(c.status)}`}>
                        {statusIcon(c.status)}
                        <span className="font-medium text-[#0B4778] w-48 shrink-0">{c.check}</span>
                        <span className="text-[#64748b] truncate" title={c.detail}>{c.detail}</span>
                      </div>
                    ))}
                  </div>
                </Section>
              )}

              {/* ── Detected Fields ── */}
              {fieldsDetail && fieldsDetail.length > 0 && (
                <Section title={`Detected Fields (${fieldsDetail.length})`}
                  icon={<ListChecks className="w-3.5 h-3.5 text-[#0B4778]" />}>
                  <div className="overflow-x-auto max-h-[300px] overflow-y-auto border border-[#D9E8F6] rounded-lg">
                    <table className="w-full text-xs">
                      <thead className="bg-[#EFF6FB] sticky top-0">
                        <tr>
                          <th className="text-left px-2 py-1.5 font-medium text-[#0B4778]">Pg</th>
                          <th className="text-left px-2 py-1.5 font-medium text-[#0B4778]">Label</th>
                          <th className="text-left px-2 py-1.5 font-medium text-[#0B4778]">Type</th>
                          <th className="text-left px-2 py-1.5 font-medium text-[#0B4778]">ID</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#D9E8F6]">
                        {fieldsDetail.map((f, i) => (
                          <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-[#EFF6FB]/30'}>
                            <td className="px-2 py-1 text-[#94a3b8]">{f.page}</td>
                            <td className="px-2 py-1 text-[#0B4778] font-medium max-w-[220px] truncate" title={f.label}>
                              {f.label || '—'}
                              {f.required && <span className="ml-1 text-red-500">*</span>}
                            </td>
                            <td className="px-2 py-1">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${typeBadgeColor(f.type)}`}>
                                {f.type}
                              </span>
                            </td>
                            <td className="px-2 py-1 text-[#94a3b8] font-mono text-[10px] max-w-[140px] truncate" title={f.field_id}>
                              {f.field_id}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Section>
              )}
            </>
          )}

          {/* ── Folder results ── */}
          {job.results && job.results.length > 0 && (
            <Section title={`Folder Results — ${job.completed}/${job.file_count} files`}
              icon={<FileText className="w-3.5 h-3.5 text-[#0B4778]" />}
              defaultOpen>
              <div className="space-y-1.5">
                {job.results.map((r, i) => (
                  <div key={i} className="flex items-center justify-between py-1.5 border-b border-[#EFF6FB] last:border-0">
                    <div className="flex items-center gap-2">
                      <FileText className="w-3.5 h-3.5 text-[#94a3b8]" />
                      <span className="text-xs text-[#0B4778]">{r.file}</span>
                      <span className="text-[10px] text-[#94a3b8]">
                        {r.result?.stats?.total_fields ?? 0} fields
                      </span>
                    </div>
                    <div className="flex gap-1.5">
                      <a href={getDownloadUrl(r.result.editable_pdf.split(/[\\/]/).pop() || '')}
                        className="text-xs text-[#3b82f6] hover:underline" download>PDF</a>
                      <span className="text-[#D9E8F6]">|</span>
                      <a href={getDownloadUrl(r.result.schema.split(/[\\/]/).pop() || '')}
                        className="text-xs text-[#3b82f6] hover:underline" download>JSON</a>
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Folder errors */}
          {job.errors && job.errors.length > 0 && (
            <div className="space-y-1 bg-red-50 rounded-lg p-3">
              {job.errors.map((e, i) => (
                <div key={i} className="text-xs text-red-600">
                  <span className="font-medium">{e.file}:</span> {e.error}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
