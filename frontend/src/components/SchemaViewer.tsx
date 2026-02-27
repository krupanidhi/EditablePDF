import { useState } from 'react';
import type { FormSchema, FieldSchema } from '../types';
import {
  ChevronDown,
  ChevronRight,
  Type,
  Hash,
  Calendar,
  DollarSign,
  Mail,
  Phone,
  CircleDot,
  CheckSquare,
  AlignLeft,
  List,
} from 'lucide-react';

const typeIcons: Record<string, React.ReactNode> = {
  text: <Type className="w-3.5 h-3.5" />,
  textarea: <AlignLeft className="w-3.5 h-3.5" />,
  number: <Hash className="w-3.5 h-3.5" />,
  currency: <DollarSign className="w-3.5 h-3.5" />,
  date: <Calendar className="w-3.5 h-3.5" />,
  email: <Mail className="w-3.5 h-3.5" />,
  phone: <Phone className="w-3.5 h-3.5" />,
  radio: <CircleDot className="w-3.5 h-3.5" />,
  checkbox: <CheckSquare className="w-3.5 h-3.5" />,
  dropdown: <List className="w-3.5 h-3.5" />,
};

const typeColors: Record<string, string> = {
  text: 'bg-blue-100 text-blue-700',
  textarea: 'bg-indigo-100 text-indigo-700',
  number: 'bg-purple-100 text-purple-700',
  currency: 'bg-green-100 text-green-700',
  date: 'bg-orange-100 text-orange-700',
  email: 'bg-cyan-100 text-cyan-700',
  phone: 'bg-teal-100 text-teal-700',
  radio: 'bg-pink-100 text-pink-700',
  checkbox: 'bg-yellow-100 text-yellow-700',
  dropdown: 'bg-gray-100 text-gray-700',
};

interface SchemaViewerProps {
  schema: FormSchema;
}

export default function SchemaViewer({ schema }: SchemaViewerProps) {
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set());

  const toggleField = (fieldId: string) => {
    setExpandedFields((prev) => {
      const next = new Set(prev);
      if (next.has(fieldId)) next.delete(fieldId);
      else next.add(fieldId);
      return next;
    });
  };

  const groupedByPage: Record<number, FieldSchema[]> = {};
  for (const field of schema.fields) {
    const page = field.page || 1;
    if (!groupedByPage[page]) groupedByPage[page] = [];
    groupedByPage[page].push(field);
  }

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="grid grid-cols-4 gap-3">
        <div className="bg-blue-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-blue-700">{schema.fields.length}</p>
          <p className="text-xs text-blue-600">Total Fields</p>
        </div>
        <div className="bg-green-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-green-700">
            {schema.fields.filter((f) => f.required).length}
          </p>
          <p className="text-xs text-green-600">Required</p>
        </div>
        <div className="bg-purple-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-purple-700">
            {schema.metadata.page_count}
          </p>
          <p className="text-xs text-purple-600">Pages</p>
        </div>
        <div className="bg-orange-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-orange-700">
            {new Set(schema.fields.map((f) => f.type)).size}
          </p>
          <p className="text-xs text-orange-600">Field Types</p>
        </div>
      </div>

      {/* Fields by page */}
      {Object.entries(groupedByPage)
        .sort(([a], [b]) => Number(a) - Number(b))
        .map(([page, fields]) => (
          <div key={page} className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="bg-gray-50 px-4 py-2 border-b border-gray-200">
              <h3 className="text-sm font-semibold text-gray-700">
                Page {page}{' '}
                <span className="font-normal text-gray-500">
                  ({fields.length} field{fields.length !== 1 ? 's' : ''})
                </span>
              </h3>
            </div>
            <div className="divide-y divide-gray-100">
              {fields.map((field) => (
                <FieldRow
                  key={field.field_id}
                  field={field}
                  expanded={expandedFields.has(field.field_id)}
                  onToggle={() => toggleField(field.field_id)}
                />
              ))}
            </div>
          </div>
        ))}
    </div>
  );
}

function FieldRow({
  field,
  expanded,
  onToggle,
}: {
  field: FieldSchema;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div>
      <button
        onClick={onToggle}
        className="w-full px-4 py-2.5 flex items-center gap-3 hover:bg-gray-50 transition-colors text-left"
      >
        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-gray-400 shrink-0" />
        )}

        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
            typeColors[field.type] || 'bg-gray-100 text-gray-700'
          }`}
        >
          {typeIcons[field.type] || <Type className="w-3.5 h-3.5" />}
          {field.type}
        </span>

        <span className="text-sm text-gray-800 flex-1 truncate">
          {field.label || field.field_id}
        </span>

        {field.required && (
          <span className="text-xs text-red-500 font-medium">Required</span>
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-3 pl-12 space-y-2 text-xs text-gray-600">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <span className="font-medium text-gray-500">Field ID:</span>{' '}
              <code className="bg-gray-100 px-1 rounded">{field.field_id}</code>
            </div>
            <div>
              <span className="font-medium text-gray-500">BBox:</span>{' '}
              [{field.bbox.map((b) => b.toFixed(1)).join(', ')}]
            </div>
          </div>

          {field.validation && (
            <div>
              <span className="font-medium text-gray-500">Validation:</span>
              <pre className="mt-1 bg-gray-50 p-2 rounded text-xs overflow-x-auto">
                {JSON.stringify(field.validation, null, 2)}
              </pre>
            </div>
          )}

          {field.options && field.options.length > 0 && (
            <div>
              <span className="font-medium text-gray-500">Options:</span>
              <div className="mt-1 flex flex-wrap gap-1">
                {field.options.map((opt, i) => (
                  <span
                    key={i}
                    className="bg-gray-100 px-2 py-0.5 rounded text-xs"
                  >
                    {opt.label || opt.value}
                  </span>
                ))}
              </div>
            </div>
          )}

          {field.depends_on && (
            <div>
              <span className="font-medium text-gray-500">Depends on:</span>{' '}
              <code className="bg-yellow-50 px-1 rounded">
                {field.depends_on.field}
              </code>{' '}
              {field.depends_on.condition} &quot;{field.depends_on.value}&quot;
            </div>
          )}

          {field.group && (
            <div>
              <span className="font-medium text-gray-500">Group:</span>{' '}
              <code className="bg-pink-50 px-1 rounded">{field.group}</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
