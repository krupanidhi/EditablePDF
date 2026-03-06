import { useState, useCallback } from 'react';
import toast from 'react-hot-toast';
import { ShieldCheck, Download, FileSearch, Upload, Settings2, Trash2, RotateCcw, AlertTriangle } from 'lucide-react';
import { extractFields, applyRequired, getDownloadUrl } from '../api';
import type { ExtractFieldsResponse, ExtractedFieldClean, ApplyRequiredResponse } from '../types';
import FileUploader from './FileUploader';

export default function RequiredFieldsTab() {
  // Extract fields state
  const [extractFieldsData, setExtractFieldsData] = useState<ExtractFieldsResponse | null>(null);
  const [extractingFields, setExtractingFields] = useState(false);
  const [pdfFile, setPdfFile] = useState<File | null>(null);

  // Edited fields with required toggles
  const [editedFields, setEditedFields] = useState<ExtractedFieldClean[]>([]);

  // Apply required state
  const [applyingRequired, setApplyingRequired] = useState(false);
  const [applyRequiredResult, setApplyRequiredResult] = useState<ApplyRequiredResponse | null>(null);

  // Page filter
  const [pageFilter, setPageFilter] = useState<number | null>(null);

  // --- Handlers ---
  const handleUploadPdf = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    setExtractingFields(true);
    setExtractFieldsData(null);
    setEditedFields([]);
    setApplyRequiredResult(null);
    setPdfFile(files[0]);
    setPageFilter(null);
    try {
      const data = await extractFields(files[0]);
      setExtractFieldsData(data);
      setEditedFields(data.fields.map(f => ({ ...f })));
      toast.success(`Extracted ${data.metadata.total_fields} fields from ${files[0].name}`);
    } catch (err) {
      toast.error(`Field extraction failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setExtractingFields(false);
    }
  }, []);

  const handleToggleRequired = useCallback((index: number) => {
    setEditedFields(prev => prev.map((f, i) =>
      i === index ? { ...f, required: !f.required } : f
    ));
    setApplyRequiredResult(null);
  }, []);

  const handleChangeDataType = useCallback((index: number, value: string) => {
    setEditedFields(prev => prev.map((f, i) =>
      i === index ? { ...f, data_type: value } : f
    ));
    setApplyRequiredResult(null);
  }, []);

  const handleToggleDeleted = useCallback((index: number) => {
    setEditedFields(prev => prev.map((f, i) =>
      i === index ? { ...f, deleted: !f.deleted, required: !f.deleted ? false : f.required } : f
    ));
    setApplyRequiredResult(null);
  }, []);

  const handleChangeMaxLength = useCallback((index: number, value: string) => {
    setEditedFields(prev => prev.map((f, i) => {
      if (i !== index) return f;
      const parsed = value === '' ? null : parseInt(value, 10);
      return { ...f, max_length: (parsed !== null && !isNaN(parsed) && parsed > 0) ? parsed : null };
    }));
    setApplyRequiredResult(null);
  }, []);

  const handleToggleReadonly = useCallback((index: number) => {
    setEditedFields(prev => prev.map((f, i) =>
      i === index ? { ...f, readonly: !f.readonly, required: !f.readonly ? false : f.required } : f
    ));
    setApplyRequiredResult(null);
  }, []);

  const handleToggleScroll = useCallback((index: number) => {
    setEditedFields(prev => prev.map((f, i) =>
      i === index ? { ...f, scroll_enabled: !f.scroll_enabled } : f
    ));
    setApplyRequiredResult(null);
  }, []);

  const handleChangeMinValue = useCallback((index: number, value: string) => {
    setEditedFields(prev => prev.map((f, i) => {
      if (i !== index) return f;
      const parsed = value === '' ? null : parseFloat(value);
      return { ...f, min_value: (parsed !== null && !isNaN(parsed)) ? parsed : null };
    }));
    setApplyRequiredResult(null);
  }, []);

  const handleChangeMaxValue = useCallback((index: number, value: string) => {
    setEditedFields(prev => prev.map((f, i) => {
      if (i !== index) return f;
      const parsed = value === '' ? null : parseFloat(value);
      return { ...f, max_value: (parsed !== null && !isNaN(parsed)) ? parsed : null };
    }));
    setApplyRequiredResult(null);
  }, []);

  const handleSelectAll = useCallback(() => {
    setEditedFields(prev => prev.map(f => f.readonly ? f : { ...f, required: true }));
    setApplyRequiredResult(null);
  }, []);

  const handleDeselectAll = useCallback(() => {
    setEditedFields(prev => prev.map(f => ({ ...f, required: false })));
    setApplyRequiredResult(null);
  }, []);

  const handleApplyRequired = useCallback(async () => {
    if (!pdfFile || editedFields.length === 0) return;
    setApplyingRequired(true);
    setApplyRequiredResult(null);
    try {
      const jsonBlob = new Blob(
        [JSON.stringify({ fields: editedFields }, null, 2)],
        { type: 'application/json' }
      );
      const result = await applyRequired(pdfFile, jsonBlob);
      setApplyRequiredResult(result);
      toast.success(`PDF regenerated: ${result.fields_updated} required fields applied`);
    } catch (err) {
      toast.error(`Apply required failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setApplyingRequired(false);
    }
  }, [pdfFile, editedFields]);

  const handleDownloadJson = useCallback(() => {
    if (!extractFieldsData) return;
    const exportData = { ...extractFieldsData, fields: editedFields };
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const safeName = extractFieldsData.metadata.source_file
      .replace(/\.pdf$/i, '')
      .replace(/[^a-zA-Z0-9_-]/g, '_');
    a.download = `${safeName}_fields.json`;
    a.href = url;
    a.click();
    URL.revokeObjectURL(url);
  }, [extractFieldsData, editedFields]);

  const handleCopyJson = useCallback(() => {
    if (!extractFieldsData) return;
    const exportData = { ...extractFieldsData, fields: editedFields };
    navigator.clipboard.writeText(JSON.stringify(exportData, null, 2));
    toast.success('JSON copied to clipboard');
  }, [extractFieldsData, editedFields]);

  // Derived
  const requiredCount = editedFields.filter(f => f.required).length;
  const pages = extractFieldsData
    ? [...new Set(editedFields.map(f => f.page))].sort((a, b) => a - b)
    : [];
  const displayedFields = pageFilter !== null
    ? editedFields.map((f, i) => ({ ...f, _origIndex: i })).filter(f => f.page === pageFilter)
    : editedFields.map((f, i) => ({ ...f, _origIndex: i }));

  return (
    <div className="space-y-6">
      {/* Step 1: Upload PDF */}
      <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
        <div className="flex items-center gap-3 mb-1">
          <div className="flex items-center justify-center w-7 h-7 rounded-full bg-[#EFF6FB] text-[#0B4778] text-xs font-bold">1</div>
          <h2 className="text-sm font-semibold text-[#0B4778] flex items-center gap-2">
            <Upload className="w-4 h-4 text-[#0B4778]" />
            Upload PDF
          </h2>
        </div>
        <p className="text-xs text-[#64748b] mb-4 ml-10">
          Upload any editable PDF to extract its fields. Configure required fields, integer-only inputs, and more — then regenerate the PDF with Digitalization Workflow rules applied.
        </p>
        <div className="ml-10">
          <FileUploader
            onFilesSelected={handleUploadPdf}
            accept={{ 'application/pdf': ['.pdf'] }}
            label={pdfFile && !extractingFields ? pdfFile.name : 'Drop editable PDF here'}
            description="Supports any editable PDF generated by this tool"
            disabled={extractingFields}
          />
          {extractingFields && (
            <p className="text-sm text-[#0B4778] mt-3 animate-pulse">
              Extracting field metadata...
            </p>
          )}
        </div>
      </div>

      {/* Step 2: Configure Required Fields */}
      {extractFieldsData && editedFields.length > 0 && (
        <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-7 h-7 rounded-full bg-[#EFF6FB] text-[#0B4778] text-xs font-bold">2</div>
            <div className="flex-1">
              <h2 className="text-sm font-semibold text-[#0B4778] flex items-center gap-2">
                <Settings2 className="w-4 h-4 text-[#0B4778]" />
                Configure Digitalization Workflow
              </h2>
              <p className="text-xs text-[#64748b] mt-0.5">
                {extractFieldsData.metadata.source_file} — {extractFieldsData.metadata.page_count} page{extractFieldsData.metadata.page_count > 1 ? 's' : ''}, {extractFieldsData.metadata.total_fields} fields
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleCopyJson}
                className="px-3 py-1.5 bg-[#EFF6FB] text-[#0B4778] text-xs font-medium rounded-md hover:bg-[#D9E8F6] transition-colors border border-[#D9E8F6]"
              >
                Copy JSON
              </button>
              <button
                onClick={handleDownloadJson}
                className="px-3 py-1.5 bg-[#0B4778] text-white text-xs font-medium rounded-md hover:bg-[#093d66] transition-colors flex items-center gap-1.5"
              >
                <Download className="w-3.5 h-3.5" />
                Save JSON
              </button>
            </div>
          </div>

          {/* XFA warning */}
          {extractFieldsData.xfa_warning && (
            <div className="bg-[#FFF7ED] border border-[#FDBA74] rounded-lg p-3 mb-4 flex items-start gap-2.5">
              <AlertTriangle className="w-5 h-5 text-[#EA580C] shrink-0 mt-0.5" />
              <div className="text-xs text-[#9A3412]">
                <strong className="text-sm">XFA Form — Reader Extensions Required</strong>
                <p className="mt-1 whitespace-pre-line">{extractFieldsData.xfa_warning}</p>
              </div>
            </div>
          )}

          {/* Summary cards */}
          <div className="grid grid-cols-5 gap-3 mb-4">
            <div className="bg-blue-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-blue-700">{extractFieldsData.metadata.total_fields}</p>
              <p className="text-xs text-blue-600">Total Fields</p>
            </div>
            <div className="bg-green-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-green-700">
                {editedFields.filter(f => f.field_type === 'text' || f.field_type === 'textarea').length}
              </p>
              <p className="text-xs text-green-600">Text Fields</p>
            </div>
            <div className="bg-amber-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-amber-700">
                {editedFields.filter(f => f.field_type === 'radio').length}
              </p>
              <p className="text-xs text-amber-600">Radio Groups</p>
            </div>
            <div className="bg-purple-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-purple-700">
                {editedFields.filter(f => f.field_type === 'checkbox').length}
              </p>
              <p className="text-xs text-purple-600">Checkboxes</p>
            </div>
            <div className="bg-red-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-red-700">{requiredCount}</p>
              <p className="text-xs text-red-600">Required</p>
            </div>
          </div>

          {/* Toolbar: page filter + bulk actions */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              {pages.length > 1 && (
                <>
                  <span className="text-xs text-gray-500">Page:</span>
                  <button
                    onClick={() => setPageFilter(null)}
                    className={`px-2 py-1 text-xs rounded-md transition-colors ${
                      pageFilter === null
                        ? 'bg-[#0B4778] text-white'
                        : 'bg-[#EFF6FB] text-[#64748b] hover:bg-[#D9E8F6]'
                    }`}
                  >
                    All
                  </button>
                  {pages.map(p => (
                    <button
                      key={p}
                      onClick={() => setPageFilter(p)}
                      className={`px-2 py-1 text-xs rounded-md transition-colors ${
                        pageFilter === p
                          ? 'bg-[#0B4778] text-white'
                          : 'bg-[#EFF6FB] text-[#64748b] hover:bg-[#D9E8F6]'
                      }`}
                    >
                      {p}
                    </button>
                  ))}
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleSelectAll}
                className="px-2.5 py-1 text-xs font-medium text-[#0B4778] bg-[#EFF6FB] border border-[#D9E8F6] rounded-md hover:bg-[#D9E8F6] transition-colors"
              >
                Select All
              </button>
              <button
                onClick={handleDeselectAll}
                className="px-2.5 py-1 text-xs font-medium text-[#64748b] bg-[#f1f5f9] border border-[#D9E8F6] rounded-md hover:bg-[#D9E8F6] transition-colors"
              >
                Deselect All
              </button>
            </div>
          </div>

          {/* Interactive Fields table */}
          <div className="border border-[#D9E8F6] rounded-lg overflow-hidden mb-4 max-h-[500px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10">
                <tr className="bg-[#EFF6FB] border-b border-[#D9E8F6]">
                  <th className="px-3 py-2 text-center font-semibold text-[#0B4778] w-20">
                    <span>Required</span>
                  </th>
                  <th className="px-3 py-2 text-left font-semibold text-[#0B4778]">Label</th>
                  <th className="px-3 py-2 text-left font-semibold text-[#0B4778]">Field ID</th>
                  <th className="px-3 py-2 text-left font-semibold text-[#0B4778]">Type</th>
                  <th className="px-3 py-2 text-left font-semibold text-[#0B4778]">Data Type</th>
                  <th className="px-3 py-2 text-center font-semibold text-[#0B4778]">Page</th>
                  <th className="px-3 py-2 text-center font-semibold text-[#0B4778] w-24">Max Length</th>
                  <th className="px-3 py-2 text-center font-semibold text-[#0B4778] w-28">
                    <span title="Min / Max numeric value allowed (e.g. 0–5000)">Value Range</span>
                  </th>
                  <th className="px-3 py-2 text-center font-semibold text-[#0B4778]">
                    <span title="Read-Only: disable editing and skip all validation">Read-Only</span>
                  </th>
                  <th className="px-3 py-2 text-center font-semibold text-[#0B4778] w-16">
                    <span title="Enable scroll bars for text overflow">Scroll</span>
                  </th>
                  <th className="px-3 py-2 text-center font-semibold text-[#0B4778] w-16">
                    <span title="Mark field for deletion from PDF">Delete</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#D9E8F6]">
                {displayedFields.map((field) => (
                  <tr
                    key={field._origIndex}
                    className={`hover:bg-[#EFF6FB] transition-colors ${field.deleted ? 'bg-red-50/60 opacity-60' : field.required ? 'bg-[#EFF6FB]' : ''}`}
                  >
                    <td className="px-3 py-2 text-center">
                      {field.readonly ? (
                        <span className="text-gray-300 text-[10px]">N/A</span>
                      ) : (
                        <div className="flex flex-col items-center gap-0.5">
                          <input
                            type="checkbox"
                            checked={field.required}
                            onChange={() => handleToggleRequired(field._origIndex)}
                            className="w-4 h-4 rounded border-[#D9E8F6] text-[#0B4778] focus:ring-[#3b82f6] cursor-pointer"
                          />
                          {field.required && field.depends_on && (
                            <span className="text-[8px] text-orange-600 whitespace-nowrap" title={`Required only when "${editedFields.find(f => f.field_id === field.depends_on)?.label || field.depends_on}" = Yes`}>
                              if Yes
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                    <td className={`px-3 py-2 text-gray-800 font-medium max-w-[200px] truncate ${field.deleted ? 'line-through text-gray-400' : ''}`} title={field.label}>
                      {field.label || <span className="text-gray-300 italic">—</span>}
                      {field.required && <span className="text-[#990000] ml-1">*</span>}
                      {field.depends_on && (
                        <span className="text-orange-500 ml-1 text-[9px]" title={`Linked to: ${editedFields.find(f => f.field_id === field.depends_on)?.label || field.depends_on}`}>&#x1F517;</span>
                      )}
                      {field.deleted && <span className="text-red-400 ml-1 text-[10px] no-underline">(deleted)</span>}
                    </td>
                    <td className="px-3 py-2 font-mono text-gray-500 max-w-[160px] truncate" title={field.field_id}>
                      {field.field_id}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-block px-2 py-0.5 rounded border text-[10px] font-medium ${
                        field.field_type === 'text' ? 'bg-blue-50 text-blue-700 border-blue-200' :
                        field.field_type === 'textarea' ? 'bg-purple-50 text-purple-700 border-purple-200' :
                        field.field_type === 'radio' ? 'bg-amber-50 text-amber-700 border-amber-200' :
                        field.field_type === 'checkbox' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
                        'bg-gray-50 text-gray-600 border-gray-200'
                      }`}>
                        {field.field_type}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={field.data_type}
                        onChange={(e) => handleChangeDataType(field._origIndex, e.target.value)}
                        className="text-xs bg-white border border-gray-200 rounded px-1.5 py-0.5 text-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-200 cursor-pointer"
                      >
                        <option value="text">text</option>
                        <option value="integer">integer</option>
                        <option value="number">number</option>
                        <option value="date">date</option>
                        <option value="email">email</option>
                        <option value="phone">phone</option>
                        <option value="currency">currency</option>
                        <option value="boolean">boolean</option>
                        <option value="selection">selection</option>
                      </select>
                    </td>
                    <td className="px-3 py-2 text-center text-gray-500">{field.page}</td>
                    <td className="px-3 py-2 text-center">
                      {(field.field_type === 'text' || field.field_type === 'textarea') && !field.readonly ? (
                        <input
                          type="number"
                          min="1"
                          placeholder="—"
                          value={field.max_length ?? ''}
                          onChange={(e) => handleChangeMaxLength(field._origIndex, e.target.value)}
                          className="w-16 text-xs text-center bg-white border border-gray-200 rounded px-1.5 py-0.5 text-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-200"
                          title="Maximum number of characters allowed"
                        />
                      ) : (
                        <span className="text-gray-300 text-[10px]">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {['integer', 'number', 'currency'].includes(field.data_type) && !field.readonly ? (
                        <div className="flex items-center gap-1 justify-center">
                          <input
                            type="number"
                            step="any"
                            placeholder="Min"
                            value={field.min_value ?? ''}
                            onChange={(e) => handleChangeMinValue(field._origIndex, e.target.value)}
                            className="w-16 text-xs text-center bg-white border border-gray-200 rounded px-1 py-0.5 text-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-200"
                            title="Minimum allowed value"
                          />
                          <span className="text-gray-400 text-[10px]">–</span>
                          <input
                            type="number"
                            step="any"
                            placeholder="Max"
                            value={field.max_value ?? ''}
                            onChange={(e) => handleChangeMaxValue(field._origIndex, e.target.value)}
                            className="w-16 text-xs text-center bg-white border border-gray-200 rounded px-1 py-0.5 text-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-200"
                            title="Maximum allowed value"
                          />
                        </div>
                      ) : (
                        <span className="text-gray-300 text-[10px]">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <input
                        type="checkbox"
                        checked={field.readonly}
                        onChange={() => handleToggleReadonly(field._origIndex)}
                        className="w-4 h-4 rounded border-gray-300 text-orange-500 focus:ring-orange-400 cursor-pointer"
                        title={field.readonly ? 'Read-only: no validation applied' : 'Editable'}
                      />
                    </td>
                    <td className="px-3 py-2 text-center">
                      {(field.field_type === 'text' || field.field_type === 'textarea') ? (
                        <input
                          type="checkbox"
                          checked={field.scroll_enabled}
                          onChange={() => handleToggleScroll(field._origIndex)}
                          className="w-4 h-4 rounded border-gray-300 text-sky-500 focus:ring-sky-400 cursor-pointer"
                          title={field.scroll_enabled ? 'Scroll enabled: text will scroll when it overflows' : 'Scroll disabled: text may be clipped'}
                        />
                      ) : (
                        <span className="text-gray-300 text-[10px]">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <button
                        onClick={() => handleToggleDeleted(field._origIndex)}
                        className={`p-1 rounded transition-colors ${
                          field.deleted
                            ? 'text-green-600 hover:bg-green-50'
                            : 'text-gray-400 hover:text-red-500 hover:bg-red-50'
                        }`}
                        title={field.deleted ? 'Restore field' : 'Delete field from PDF'}
                      >
                        {field.deleted ? <RotateCcw className="w-3.5 h-3.5" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Info banner */}
          <div className="bg-[#EFF6FB] border border-[#D9E8F6] rounded-lg p-3 mb-4 text-xs text-[#0B4778]">
            <strong>What happens when you apply:</strong>
            <ul className="mt-1.5 ml-3 space-y-0.5 list-disc">
              <li><strong>Required fields</strong> — red border on open if empty, save &amp; print blocked with alert listing missing fields, close warning. Fields linked to a radio group (&#x1F517;) are only required when the radio = Yes</li>
              <li><strong>Integer fields</strong> — only digits allowed (keystroke filtered by data type)</li>
              <li><strong>Read-only fields</strong> — left untouched, no validation applied</li>
              <li><strong>Delete fields</strong> — permanently removes the control from the PDF</li>
              <li><strong>Max length</strong> — limits character input (e.g. 4000 chars, 5 digits)</li>
              <li><strong>Value range</strong> — min/max numeric value for integer, number, and currency fields (e.g. 0–5000). Shows an alert if the entered value is out of range</li>
              <li><strong>Scroll bars</strong> — vertical scroll for text boxes, horizontal scroll for text areas (toggle per field)</li>
              <li><strong>Fixed font</strong> — consistent 10pt font on all text fields (no auto-shrink on overflow)</li>
            </ul>
            <p className="mt-1.5">Control placement is never changed.</p>
          </div>
        </div>
      )}

      {/* Step 3: Generate PDF */}
      {extractFieldsData && editedFields.length > 0 && (
        <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-7 h-7 rounded-full bg-[#EFF6FB] text-[#0B4778] text-xs font-bold">3</div>
            <div className="flex-1">
              <h2 className="text-sm font-semibold text-[#0B4778] flex items-center gap-2">
                <FileSearch className="w-4 h-4 text-[#0B4778]" />
                Regenerate PDF
              </h2>
              <p className="text-xs text-[#64748b] mt-0.5">
                Apply Digitalization Workflow rules and download the updated PDF.
              </p>
            </div>
          </div>

          <div className="flex items-center justify-between">
            <div className="text-xs text-[#64748b] space-y-0.5">
              <p><span className="text-[#0B4778] font-bold text-lg">{requiredCount}</span><span className="ml-1.5">required field{requiredCount !== 1 ? 's' : ''}</span></p>
              <p><span className="text-[#3b82f6] font-bold text-lg">{editedFields.filter(f => f.data_type === 'integer' && !f.readonly).length}</span><span className="ml-1.5">integer-only field{editedFields.filter(f => f.data_type === 'integer' && !f.readonly).length !== 1 ? 's' : ''}</span></p>
              {editedFields.filter(f => f.deleted).length > 0 && (
                <p><span className="text-red-400 font-bold text-lg">{editedFields.filter(f => f.deleted).length}</span><span className="ml-1.5">field{editedFields.filter(f => f.deleted).length !== 1 ? 's' : ''} to delete</span></p>
              )}
            </div>
            <button
              onClick={handleApplyRequired}
              disabled={applyingRequired}
              className={`px-5 py-2.5 text-sm font-medium rounded-lg transition-colors flex items-center gap-2 ${
                applyingRequired
                  ? 'bg-[#D9E8F6] text-[#64748b] cursor-wait'
                  : 'bg-[#0B4778] text-white hover:bg-[#093d66] shadow-sm'
              }`}
            >
              <ShieldCheck className="w-4 h-4" />
              {applyingRequired ? 'Generating PDF...' : 'Apply & Regenerate PDF'}
            </button>
          </div>

          {/* Result */}
          {applyRequiredResult && (
            <div className="mt-4 space-y-3">
              <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-green-800">
                      PDF regenerated with Digitalization Workflow rules
                    </p>
                    <p className="text-xs text-green-600 mt-0.5">
                      {applyRequiredResult.fields_updated} of {applyRequiredResult.fields_total} fields updated
                    </p>
                  </div>
                  <a
                    href={getDownloadUrl(applyRequiredResult.output_file)}
                    download
                    className="px-5 py-2.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors flex items-center gap-2 shadow-sm"
                  >
                    <Download className="w-4 h-4" />
                    Download PDF
                  </a>
                </div>
              </div>
              {applyRequiredResult.xfa_warning && (
                <div className="p-4 bg-[#FFF7ED] border-2 border-[#EA580C] rounded-lg flex items-start gap-3">
                  <AlertTriangle className="w-6 h-6 text-[#EA580C] shrink-0 mt-0.5" />
                  <div className="text-sm text-[#9A3412]">
                    <strong className="text-base">⚠ Important: Re-apply Reader Extensions</strong>
                    <p className="mt-1.5 whitespace-pre-line">{applyRequiredResult.xfa_warning}</p>
                    <p className="mt-2 font-semibold">
                      Steps: Open in Adobe Acrobat Pro → File → Save As Other → Reader Extended PDF → Enable More Tools
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
