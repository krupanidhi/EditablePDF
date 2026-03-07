import { useState, useEffect, useRef, useCallback } from 'react';
import {
  FileUp, FileSpreadsheet, Play, Loader2, CheckCircle2, XCircle,
  ChevronDown, ChevronRight, ShieldCheck, Download, AlertTriangle,
  Info, BarChart3, Settings2, Zap,
} from 'lucide-react';
import { generateNap, getNapJob, getDownloadUrl } from '../api';
import type { NapJob, NapComplianceCheck } from '../types';
import FileUploader from './FileUploader';

/* ── tiny helpers ── */
const statusIcon = (s: NapComplianceCheck['status']) => {
  switch (s) {
    case 'pass': return <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />;
    case 'fail': return <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />;
    case 'warn': return <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0" />;
    default:     return <Info className="w-3.5 h-3.5 text-blue-400 shrink-0" />;
  }
};
const statusBg = (s: NapComplianceCheck['status']) => {
  switch (s) {
    case 'pass': return 'bg-green-50';
    case 'fail': return 'bg-red-50';
    case 'warn': return 'bg-amber-50';
    default:     return 'bg-blue-50';
  }
};

/* ── score ring ── */
function ScoreRing({ score, label }: { score: number; label: string }) {
  const r = 18, c = 2 * Math.PI * r;
  const offset = c - (score / 100) * c;
  const color = score >= 90 ? '#16a34a' : score >= 70 ? '#d97706' : '#dc2626';
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-14 h-14">
        <svg viewBox="0 0 44 44" className="w-14 h-14 -rotate-90">
          <circle cx="22" cy="22" r={r} fill="none" stroke="#e5e7eb" strokeWidth="4" />
          <circle cx="22" cy="22" r={r} fill="none" stroke={color} strokeWidth="4"
            strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
            style={{ transition: 'stroke-dashoffset 0.6s ease' }} />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-[12px] font-bold" style={{ color }}>
          {score}%
        </span>
      </div>
      <span className="text-[10px] text-[#64748b] font-medium">{label}</span>
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
export default function NapGeneratorTab() {
  const [templateFile, setTemplateFile] = useState<File | null>(null);
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<NapJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Poll job status
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await getNapJob(jobId);
        if (cancelled) return;
        setJob(data);
        if (data.status === 'completed' || data.status === 'failed') {
          if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to fetch job');
      }
    };
    poll();
    intervalRef.current = setInterval(poll, 2000);
    return () => { cancelled = true; if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [jobId]);

  const handleGenerate = useCallback(async () => {
    if (!templateFile || !excelFile) return;
    setSubmitting(true);
    setError(null);
    setJob(null);
    setJobId(null);
    try {
      const res = await generateNap(templateFile, excelFile);
      setJobId(res.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setSubmitting(false);
    }
  }, [templateFile, excelFile]);

  const result = job?.result;
  const compliance = result?.compliance;

  return (
    <div className="space-y-6">
      {/* ── Upload Section ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Template PDF */}
        <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
          <h2 className="text-sm font-semibold text-[#0B4778] mb-1 flex items-center gap-2">
            <FileUp className="w-4 h-4 text-[#0B4778]" />
            Template PDF
          </h2>
          <p className="text-xs text-[#64748b] mb-3">
            Upload the digitalized NAP template PDF with radio buttons and JS validation.
          </p>
          <FileUploader
            onFilesSelected={(files) => setTemplateFile(files[0])}
            accept={{ 'application/pdf': ['.pdf'] }}
            label={templateFile ? templateFile.name : 'Drop template PDF here'}
            description="Must be an editable PDF from the Digitalization workflow"
          />
        </div>

        {/* Excel Data */}
        <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
          <h2 className="text-sm font-semibold text-[#0B4778] mb-1 flex items-center gap-2">
            <FileSpreadsheet className="w-4 h-4 text-[#0B4778]" />
            Excel Data File
          </h2>
          <p className="text-xs text-[#64748b] mb-3">
            Upload the H8S Application Info Excel with site data (one row per site).
          </p>
          <FileUploader
            onFilesSelected={(files) => setExcelFile(files[0])}
            accept={{ 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] }}
            label={excelFile ? excelFile.name : 'Drop Excel file here'}
            description="Columns: Grant, Tracking, Grantee, UEI, Org, Site Name, Site Address"
          />
        </div>
      </div>

      {/* Generate Button */}
      <button
        onClick={handleGenerate}
        disabled={!templateFile || !excelFile || submitting || (job?.status === 'processing')}
        className="w-full px-4 py-3 bg-[#0B4778] text-white text-sm font-semibold rounded-lg hover:bg-[#093d66] disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
      >
        {submitting || job?.status === 'processing'
          ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating PDFs...</>
          : <><Play className="w-4 h-4" /> Generate NAP PDFs</>}
      </button>

      {/* Error */}
      {(error || job?.error) && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-center gap-3">
          <XCircle className="w-5 h-5 text-red-500 shrink-0" />
          <div>
            <p className="text-sm font-medium text-red-800">Generation Failed</p>
            <p className="text-xs text-red-600">{error || job?.error}</p>
          </div>
        </div>
      )}

      {/* Processing indicator */}
      {job?.status === 'processing' && (
        <div className="bg-[#EFF6FB] border border-[#D9E8F6] rounded-lg p-4 flex items-center gap-3">
          <Loader2 className="w-5 h-5 text-blue-500 animate-spin shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-medium text-[#0B4778]">Generating PDFs...</p>
            <p className="text-xs text-[#64748b]">This may take 15-30 seconds for large datasets.</p>
            <div className="mt-2 bg-[#D9E8F6] rounded-full h-1.5 overflow-hidden">
              <div className="bg-[#3b82f6] h-full rounded-full animate-pulse w-2/3" />
            </div>
          </div>
        </div>
      )}

      {/* ── Results ── */}
      {result && (
        <div className="bg-white border border-[#D9E8F6] rounded-lg shadow-sm overflow-hidden">
          {/* Header */}
          <div className="px-4 py-3 bg-green-50 border-b border-green-200 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-green-500" />
              <span className="text-sm font-semibold text-green-800">
                Generation Complete — {result.total_pdfs} PDFs
              </span>
              <span className="text-[10px] text-green-600 font-normal">
                {result.total_sites} sites · {result.processing_time_sec}s
              </span>
            </div>
            {result.sample_file && (
              <a href={getDownloadUrl(result.sample_file)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#0B4778] text-white text-xs font-medium rounded-md hover:bg-[#093d66] transition-colors"
                download>
                <Download className="w-3.5 h-3.5" /> Sample PDF
              </a>
            )}
          </div>

          <div className="p-4 space-y-4">
            {/* Score Rings */}
            <div className="flex items-center gap-6 justify-center py-2">
              {compliance && <ScoreRing score={compliance.score} label="508 Compliance" />}
              <ScoreRing score={result.confidence} label="Confidence" />
            </div>

            {/* Quick Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-[#EFF6FB] rounded-lg p-3 text-center">
                <div className="text-lg font-bold text-[#0B4778]">{result.total_pdfs}</div>
                <div className="text-[10px] text-[#64748b]">PDFs Generated</div>
              </div>
              <div className="bg-[#EFF6FB] rounded-lg p-3 text-center">
                <div className="text-lg font-bold text-[#0B4778]">{result.total_sites}</div>
                <div className="text-[10px] text-[#64748b]">Total Sites</div>
              </div>
              <div className="bg-[#EFF6FB] rounded-lg p-3 text-center">
                <div className="text-lg font-bold text-[#0B4778]">{result.processing_time_sec}s</div>
                <div className="text-[10px] text-[#64748b]">Processing Time</div>
              </div>
              <div className="bg-[#EFF6FB] rounded-lg p-3 text-center">
                <div className="text-lg font-bold text-[#0B4778]">{result.template_info.text_widgets + 2}</div>
                <div className="text-[10px] text-[#64748b]">Widgets/PDF</div>
              </div>
            </div>

            {/* 508 Compliance */}
            {compliance && (
              <Section
                title={`Section 508 Compliance — ${compliance.passed}/${compliance.total} passed`}
                icon={<ShieldCheck className="w-3.5 h-3.5 text-[#0B4778]" />}
                defaultOpen>
                <div className="space-y-0.5">
                  {compliance.checks.map((c, i) => (
                    <div key={i} className={`flex items-center gap-2 px-2 py-1 rounded text-xs ${statusBg(c.status)}`}>
                      {statusIcon(c.status)}
                      <span className="font-medium text-[#0B4778] w-48 shrink-0">{c.check}</span>
                      <span className="text-[#64748b] truncate" title={c.detail}>{c.detail}</span>
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* Template Info */}
            <Section
              title="Template Analysis"
              icon={<Settings2 className="w-3.5 h-3.5 text-[#0B4778]" />}>
              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between py-1 border-b border-[#EFF6FB]">
                  <span className="text-[#64748b]">Template</span>
                  <span className="text-[#0B4778] font-medium">{result.template}</span>
                </div>
                <div className="flex justify-between py-1 border-b border-[#EFF6FB]">
                  <span className="text-[#64748b]">Page Size</span>
                  <span className="text-[#0B4778]">{result.template_info.page_size}</span>
                </div>
                <div className="flex justify-between py-1 border-b border-[#EFF6FB]">
                  <span className="text-[#64748b]">Header Text</span>
                  <span className="text-[#0B4778]">{result.template_info.header_text}</span>
                </div>
                <div className="flex justify-between py-1 border-b border-[#EFF6FB]">
                  <span className="text-[#64748b]">Radio Group</span>
                  <span className="text-[#0B4778] font-mono text-[10px]">{result.template_info.radio_field}</span>
                </div>
                <div className="flex justify-between py-1 border-b border-[#EFF6FB]">
                  <span className="text-[#64748b]">JS Validation</span>
                  <div className="flex gap-1">
                    {Object.entries(result.template_info.js_streams).map(([k, v]) => (
                      <span key={k} className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        v ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
                      }`}>
                        {k}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </Section>

            {/* Widget Mapping */}
            <Section
              title="Auto-Detected Widget Mapping"
              icon={<Zap className="w-3.5 h-3.5 text-[#0B4778]" />}>
              <div className="space-y-0.5 text-xs">
                {Object.entries(result.widget_mapping).map(([dataKey, fieldName]) => (
                  <div key={dataKey} className="flex items-center gap-2 px-2 py-1 bg-[#EFF6FB]/50 rounded">
                    <span className="font-medium text-[#0B4778] w-24">{dataKey}</span>
                    <span className="text-[#64748b]">→</span>
                    <span className="text-[#64748b] font-mono text-[10px]">{fieldName}</span>
                  </div>
                ))}
              </div>
            </Section>

            {/* Generation Stats */}
            <Section
              title="Generation Statistics"
              icon={<BarChart3 className="w-3.5 h-3.5 text-[#0B4778]" />}>
              <div className="text-xs text-[#64748b] space-y-1">
                <p>Generated <strong className="text-[#0B4778]">{result.total_pdfs}</strong> PDFs covering <strong className="text-[#0B4778]">{result.total_sites}</strong> sites in <strong className="text-[#0B4778]">{result.processing_time_sec}s</strong>.</p>
                <p>Average: <strong className="text-[#0B4778]">{(result.total_sites / result.total_pdfs).toFixed(1)}</strong> sites/PDF, <strong className="text-[#0B4778]">{(result.processing_time_sec / result.total_pdfs * 1000).toFixed(0)}ms</strong>/PDF.</p>
                <p>Output directory: <code className="bg-[#EFF6FB] px-1 py-0.5 rounded text-[10px]">{result.output_dir}</code></p>
              </div>
            </Section>
          </div>
        </div>
      )}
    </div>
  );
}
