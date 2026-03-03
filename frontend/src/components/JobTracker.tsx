import { useState, useEffect, useRef } from 'react';
import { getJob, getDownloadUrl } from '../api';
import type { Job } from '../types';
import { Loader2, CheckCircle2, XCircle, Download, FileJson, FileText } from 'lucide-react';

interface JobTrackerProps {
  jobId: string;
  onComplete?: (job: Job) => void;
}

export default function JobTracker({ jobId, onComplete }: JobTrackerProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const data = await getJob(jobId);
        if (cancelled) return;
        setJob(data);

        if (data.status === 'completed' || data.status === 'failed') {
          if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
          if (data.status === 'completed' && onComplete) {
            onComplete(data);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to fetch job status');
        }
      }
    };

    poll();
    intervalRef.current = setInterval(poll, 2000);

    return () => {
      cancelled = true;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [jobId, onComplete]);

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

  if (!job) {
    return (
      <div className="bg-[#EFF6FB] border border-[#D9E8F6] rounded-lg p-4 flex items-center gap-3">
        <Loader2 className="w-5 h-5 text-[#94a3b8] animate-spin" />
        <p className="text-sm text-[#0B4778]">Loading job status...</p>
      </div>
    );
  }

  return (
    <div className="bg-white border border-[#D9E8F6] rounded-lg shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[#D9E8F6] flex items-center justify-between">
        <div className="flex items-center gap-2">
          {job.status === 'processing' && (
            <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
          )}
          {job.status === 'completed' && (
            <CheckCircle2 className="w-4 h-4 text-green-500" />
          )}
          {job.status === 'failed' && (
            <XCircle className="w-4 h-4 text-red-500" />
          )}
          <span className="text-sm font-medium text-[#0B4778]">
            Job {job.id}
          </span>
        </div>
        <span
          className={`text-xs font-medium px-2 py-0.5 rounded-full ${
            job.status === 'processing'
              ? 'bg-blue-100 text-blue-700'
              : job.status === 'completed'
              ? 'bg-green-100 text-green-700'
              : 'bg-red-100 text-red-700'
          }`}
        >
          {job.status}
        </span>
      </div>

      {/* Body */}
      <div className="p-4">
        {/* Single file result */}
        {job.result && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 text-xs text-[#64748b]">
              <div>
                <span className="font-medium">Pages:</span> {job.result.stats.pages}
              </div>
              <div>
                <span className="font-medium">Fields:</span> {job.result.stats.total_fields}
              </div>
              <div>
                <span className="font-medium">Time:</span> {job.result.stats.processing_time_sec}s
              </div>
              <div>
                <span className="font-medium">Types:</span>{' '}
                {Object.entries(job.result.stats.by_type)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(', ')}
              </div>
            </div>

            <div className="flex gap-2 pt-2">
              <a
                href={getDownloadUrl(
                  job.result.editable_pdf.split(/[\\/]/).pop() || ''
                )}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#0B4778] text-white text-xs font-medium rounded-md hover:bg-[#093d66] transition-colors"
                download
              >
                <Download className="w-3.5 h-3.5" />
                Editable PDF
              </a>
              <a
                href={getDownloadUrl(
                  job.result.schema.split(/[\\/]/).pop() || ''
                )}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#EFF6FB] text-[#0B4778] text-xs font-medium rounded-md hover:bg-[#D9E8F6] transition-colors border border-[#D9E8F6]"
                download
              >
                <FileJson className="w-3.5 h-3.5" />
                Schema JSON
              </a>
            </div>
          </div>
        )}

        {/* Folder results */}
        {job.results && job.results.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs text-[#64748b]">
              {job.completed}/{job.file_count} files processed
            </p>
            {job.results.map((r, i) => (
              <div
                key={i}
                className="flex items-center justify-between py-2 border-b border-[#EFF6FB] last:border-0"
              >
                <div className="flex items-center gap-2">
                  <FileText className="w-3.5 h-3.5 text-[#94a3b8]" />
                  <span className="text-xs text-[#0B4778]">{r.file}</span>
                </div>
                <div className="flex gap-1">
                  <a
                    href={getDownloadUrl(
                      r.result.editable_pdf.split(/[\\/]/).pop() || ''
                    )}
                    className="text-xs text-[#3b82f6] hover:underline"
                    download
                  >
                    PDF
                  </a>
                  <span className="text-[#D9E8F6]">|</span>
                  <a
                    href={getDownloadUrl(
                      r.result.schema.split(/[\\/]/).pop() || ''
                    )}
                    className="text-xs text-[#3b82f6] hover:underline"
                    download
                  >
                    JSON
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Errors */}
        {job.errors && job.errors.length > 0 && (
          <div className="mt-3 space-y-1">
            {job.errors.map((e, i) => (
              <div key={i} className="text-xs text-red-600">
                <span className="font-medium">{e.file}:</span> {e.error}
              </div>
            ))}
          </div>
        )}

        {/* Single error */}
        {job.error && (
          <div className="text-sm text-red-600">{job.error}</div>
        )}

        {/* Processing indicator */}
        {job.status === 'processing' && !job.result && (
          <div className="flex items-center gap-3">
            <div className="flex-1 bg-[#D9E8F6] rounded-full h-1.5 overflow-hidden">
              <div className="bg-[#3b82f6] h-full rounded-full animate-pulse w-2/3" />
            </div>
            <span className="text-xs text-[#64748b]">
              {job.input_file || 'Processing...'}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
