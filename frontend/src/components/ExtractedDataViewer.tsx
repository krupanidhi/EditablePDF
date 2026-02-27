import { useState, useMemo } from 'react';
import type { ExtractedData, ExtractedField } from '../types';
import {
  CheckCircle2,
  XCircle,
  MinusCircle,
  Search,
  Filter,
  ChevronDown,
  ChevronRight,
  FileText,
  ToggleLeft,
  ToggleRight,
  Type,
  ListChecks,
  Hash,
} from 'lucide-react';

interface ExtractedDataViewerProps {
  data: ExtractedData;
}

type FilterMode = 'all' | 'filled' | 'empty';

const TYPE_ICONS: Record<string, React.ReactNode> = {
  text: <Type className="w-3.5 h-3.5" />,
  textarea: <FileText className="w-3.5 h-3.5" />,
  radio: <ToggleRight className="w-3.5 h-3.5" />,
  checkbox: <ListChecks className="w-3.5 h-3.5" />,
  dropdown: <Hash className="w-3.5 h-3.5" />,
};

const TYPE_COLORS: Record<string, string> = {
  text: 'bg-blue-50 text-blue-700 border-blue-200',
  textarea: 'bg-purple-50 text-purple-700 border-purple-200',
  radio: 'bg-amber-50 text-amber-700 border-amber-200',
  checkbox: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  dropdown: 'bg-cyan-50 text-cyan-700 border-cyan-200',
};

function FieldValue({ field }: { field: ExtractedField }) {
  if (field.type === 'checkbox') {
    return field.checked ? (
      <span className="inline-flex items-center gap-1 text-green-700">
        <ToggleRight className="w-3.5 h-3.5" /> Yes
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 text-gray-400">
        <ToggleLeft className="w-3.5 h-3.5" /> No
      </span>
    );
  }
  if (field.type === 'radio') {
    const val = field.selected_option;
    if (!val) return <span className="text-gray-300 italic">Not selected</span>;
    return (
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
          val === 'Yes'
            ? 'bg-green-100 text-green-800'
            : val === 'No'
            ? 'bg-red-100 text-red-800'
            : 'bg-gray-100 text-gray-800'
        }`}
      >
        {val}
      </span>
    );
  }
  const val = String(field.value || '');
  if (!val) return <span className="text-gray-300 italic">Empty</span>;
  if (val.length > 120) {
    return (
      <span className="text-gray-800" title={val}>
        {val.slice(0, 120)}...
      </span>
    );
  }
  return <span className="text-gray-800">{val}</span>;
}

function PageGroup({
  pageNum,
  fields,
  defaultOpen,
}: {
  pageNum: string;
  fields: ExtractedField[];
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const filled = fields.filter((f) => f.is_filled).length;

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 hover:bg-gray-100 transition-colors"
      >
        <div className="flex items-center gap-3">
          {open ? (
            <ChevronDown className="w-4 h-4 text-gray-500" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-500" />
          )}
          <span className="text-sm font-semibold text-gray-800">
            Page {pageNum}
          </span>
          <span className="text-xs text-gray-500">
            {fields.length} field{fields.length !== 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-2 py-0.5 bg-green-100 text-green-700 rounded-full">
            {filled} filled
          </span>
          {fields.length - filled > 0 && (
            <span className="text-xs px-2 py-0.5 bg-orange-100 text-orange-700 rounded-full">
              {fields.length - filled} empty
            </span>
          )}
        </div>
      </button>

      {open && (
        <div className="divide-y divide-gray-100">
          {fields.map((field, i) => (
            <div
              key={i}
              className={`grid grid-cols-12 gap-2 px-4 py-2.5 items-center text-xs hover:bg-slate-50 transition-colors ${
                field.is_filled ? '' : 'bg-gray-50/50'
              }`}
            >
              {/* Status icon */}
              <div className="col-span-1 flex justify-center">
                {field.is_filled ? (
                  <CheckCircle2 className="w-4 h-4 text-green-500" />
                ) : field.required ? (
                  <XCircle className="w-4 h-4 text-red-500" />
                ) : (
                  <MinusCircle className="w-4 h-4 text-gray-300" />
                )}
              </div>

              {/* Label + field ID */}
              <div className="col-span-4">
                {field.label ? (
                  <div>
                    <p className="text-sm text-gray-800 font-medium leading-tight">
                      {field.label}
                    </p>
                    <p className="text-[10px] font-mono text-gray-400 mt-0.5">
                      {field.field_id}
                    </p>
                  </div>
                ) : (
                  <p className="font-mono text-gray-600">{field.field_id}</p>
                )}
              </div>

              {/* Type badge */}
              <div className="col-span-2">
                <span
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium ${
                    TYPE_COLORS[field.type] || 'bg-gray-50 text-gray-600 border-gray-200'
                  }`}
                >
                  {TYPE_ICONS[field.type] || null}
                  {field.type}
                </span>
              </div>

              {/* Value */}
              <div className="col-span-5">
                <FieldValue field={field} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ExtractedDataViewer({ data }: ExtractedDataViewerProps) {
  const [search, setSearch] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');

  const filteredPages = useMemo(() => {
    const result: Record<string, ExtractedField[]> = {};
    const searchLower = search.toLowerCase();

    for (const [pageNum, fields] of Object.entries(data.pages)) {
      const filtered = fields.filter((f) => {
        // Filter by fill status
        if (filterMode === 'filled' && !f.is_filled) return false;
        if (filterMode === 'empty' && f.is_filled) return false;

        // Filter by search
        if (searchLower) {
          const haystack = [
            f.field_id,
            f.label || '',
            String(f.value || ''),
            f.type,
            f.selected_option || '',
          ]
            .join(' ')
            .toLowerCase();
          if (!haystack.includes(searchLower)) return false;
        }
        return true;
      });

      if (filtered.length > 0) {
        result[pageNum] = filtered;
      }
    }
    return result;
  }, [data.pages, search, filterMode]);

  const totalVisible = Object.values(filteredPages).reduce(
    (sum, arr) => sum + arr.length,
    0
  );

  const fillPct = data.summary.total_fields
    ? Math.round((data.summary.filled_fields / data.summary.total_fields) * 100)
    : 0;

  return (
    <div className="space-y-4">
      {/* Schema match indicator */}
      {data.metadata.schema_matched && (
        <div className="flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-200 rounded-lg text-xs text-blue-700">
          <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          <span>
            Schema auto-matched:{' '}
            <span className="font-mono font-medium">
              {data.metadata.schema_matched}
            </span>{' '}
            — field labels enriched
          </span>
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-3">
        <div className="bg-blue-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-blue-700">
            {data.summary.total_fields}
          </p>
          <p className="text-xs text-blue-600">Total Fields</p>
        </div>
        <div className="bg-green-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-green-700">
            {data.summary.filled_fields}
          </p>
          <p className="text-xs text-green-600">Filled</p>
        </div>
        <div className="bg-orange-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-orange-700">
            {data.summary.empty_fields}
          </p>
          <p className="text-xs text-orange-600">Empty</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-slate-700">{fillPct}%</p>
          <p className="text-xs text-slate-600">Completion</p>
        </div>
      </div>

      {/* Completion bar */}
      <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            fillPct === 100
              ? 'bg-green-500'
              : fillPct > 50
              ? 'bg-blue-500'
              : 'bg-orange-400'
          }`}
          style={{ width: `${fillPct}%` }}
        />
      </div>

      {/* Type breakdown */}
      <div className="flex flex-wrap gap-2">
        {Object.entries(data.summary.by_type).map(([type, count]) => (
          <span
            key={type}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium ${
              TYPE_COLORS[type] || 'bg-gray-50 text-gray-600 border-gray-200'
            }`}
          >
            {TYPE_ICONS[type] || null}
            {type}: {count}
          </span>
        ))}
      </div>

      {/* Search & Filter toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search fields by label, ID, or value..."
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
        <div className="flex items-center gap-1 bg-gray-100 p-0.5 rounded-lg">
          {(['all', 'filled', 'empty'] as FilterMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => setFilterMode(mode)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                filterMode === mode
                  ? 'bg-white text-blue-700 shadow-sm'
                  : 'text-gray-600 hover:text-gray-800'
              }`}
            >
              <Filter className="w-3 h-3" />
              {mode.charAt(0).toUpperCase() + mode.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Results count */}
      {(search || filterMode !== 'all') && (
        <p className="text-xs text-gray-500">
          Showing {totalVisible} of {data.summary.total_fields} fields
          {search && (
            <>
              {' '}matching "<span className="font-medium">{search}</span>"
            </>
          )}
        </p>
      )}

      {/* Page groups */}
      <div className="space-y-3">
        {Object.entries(filteredPages)
          .sort(([a], [b]) => Number(a) - Number(b))
          .map(([pageNum, fields]) => (
            <PageGroup
              key={pageNum}
              pageNum={pageNum}
              fields={fields}
              defaultOpen={Object.keys(filteredPages).length <= 4}
            />
          ))}
      </div>

      {totalVisible === 0 && (
        <div className="text-center py-8 text-gray-400 text-sm">
          No fields match your search criteria
        </div>
      )}
    </div>
  );
}
