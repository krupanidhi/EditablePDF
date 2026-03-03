import type { ValidationResult } from '../types';
import { CheckCircle2, XCircle, AlertTriangle, SkipForward } from 'lucide-react';

interface ValidationViewerProps {
  result: ValidationResult;
}

export default function ValidationViewer({ result }: ValidationViewerProps) {
  return (
    <div className="space-y-4">
      {/* Overall status */}
      <div
        className={`rounded-lg p-4 flex items-center gap-3 ${
          result.valid
            ? 'bg-green-50 border border-green-200'
            : 'bg-red-50 border border-red-200'
        }`}
      >
        {result.valid ? (
          <CheckCircle2 className="w-6 h-6 text-green-500" />
        ) : (
          <XCircle className="w-6 h-6 text-red-500" />
        )}
        <div>
          <p
            className={`text-sm font-semibold ${
              result.valid ? 'text-green-800' : 'text-red-800'
            }`}
          >
            {result.valid ? 'All Validations Passed' : 'Validation Failed'}
          </p>
          <p className="text-xs text-[#64748b] mt-0.5">
            {result.passed.length} passed, {result.errors.length} errors,{' '}
            {result.warnings.length} warnings
            {result.skipped && result.skipped.length > 0
              ? `, ${result.skipped.length} skipped`
              : ''}
          </p>
        </div>
      </div>

      {/* Errors */}
      {result.errors.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-red-700 flex items-center gap-1.5">
            <XCircle className="w-4 h-4" />
            Errors ({result.errors.length})
          </h4>
          {result.errors.map((e, i) => (
            <div
              key={i}
              className="bg-red-50 border border-red-100 rounded-lg px-3 py-2"
            >
              <p className="text-xs font-medium text-red-800">{e.name}</p>
              <p className="text-xs text-red-600 mt-0.5">{e.message}</p>
              <p className="text-xs text-red-400 mt-0.5">Rule: {e.rule_id}</p>
            </div>
          ))}
        </div>
      )}

      {/* Warnings */}
      {result.warnings.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-yellow-700 flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4" />
            Warnings ({result.warnings.length})
          </h4>
          {result.warnings.map((w, i) => (
            <div
              key={i}
              className="bg-yellow-50 border border-yellow-100 rounded-lg px-3 py-2"
            >
              <p className="text-xs font-medium text-yellow-800">{w.name}</p>
              <p className="text-xs text-yellow-600 mt-0.5">{w.message}</p>
            </div>
          ))}
        </div>
      )}

      {/* Passed */}
      {result.passed.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-sm font-semibold text-green-700 flex items-center gap-1.5">
            <CheckCircle2 className="w-4 h-4" />
            Passed ({result.passed.length})
          </h4>
          <div className="bg-green-50 border border-green-100 rounded-lg p-3">
            {result.passed.map((p, i) => (
              <div key={i} className="text-xs text-green-700 py-0.5">
                ✓ {p.name}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Skipped */}
      {result.skipped && result.skipped.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-sm font-semibold text-[#64748b] flex items-center gap-1.5">
            <SkipForward className="w-4 h-4" />
            Skipped ({result.skipped.length})
          </h4>
          {result.skipped.map((s, i) => (
            <div
              key={i}
              className="text-xs text-[#64748b] bg-[#EFF6FB] rounded px-3 py-1.5"
            >
              {s.name}: {s.reason}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
