import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import {
  FileUp,
  FolderOpen,
  FileSearch,
  ShieldCheck,
  Activity,
  ListChecks,
  Trash2,
} from 'lucide-react';
import { healthCheck, convertFile, convertFolder, extractFields, validateData, listJobs, deleteJob as apiDeleteJob, deleteAllJobs as apiDeleteAllJobs } from './api';
import type { ExtractFieldsResponse, ValidationResult, HealthCheck } from './types';
import FileUploader from './components/FileUploader';
import JobTracker from './components/JobTracker';
import ValidationViewer from './components/ValidationViewer';
import RequiredFieldsTab from './components/RequiredFieldsTab';

type TabGroup = 'digitalization' | 'validation';
type Tab = 'convert' | 'required' | 'extract' | 'validate';

function App() {
  const [activeGroup, setActiveGroup] = useState<TabGroup>('digitalization');
  const [activeTab, setActiveTab] = useState<Tab>('convert');
  const [health, setHealth] = useState<HealthCheck | null>(null);

  // Convert state — jobs persisted on backend disk
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [folderPath, setFolderPath] = useState('');
  const [folderProcessing, setFolderProcessing] = useState(false);

  // Extract state
  const [extractedData, setExtractedData] = useState<ExtractFieldsResponse | null>(null);
  const [extracting, setExtracting] = useState(false);

  // Validate state
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);


  useEffect(() => {
    healthCheck()
      .then(setHealth)
      .catch(() => setHealth(null));
    // Load persisted jobs from backend
    listJobs()
      .then((jobs) => setJobIds(jobs.map((j) => j.id)))
      .catch(() => {});
  }, []);

  // --- Convert handlers ---
  const handleFileUpload = useCallback(async (files: File[]) => {
    for (const file of files) {
      try {
        const res = await convertFile(file);
        setJobIds((prev) => [res.job_id, ...prev]);
        toast.success(`Processing: ${file.name}`);
      } catch (err) {
        toast.error(`Failed to upload ${file.name}: ${err instanceof Error ? err.message : 'Unknown error'}`);
      }
    }
  }, []);

  const handleFolderConvert = useCallback(async () => {
    if (!folderPath.trim()) {
      toast.error('Please enter a folder path');
      return;
    }
    setFolderProcessing(true);
    try {
      const res = await convertFolder(folderPath.trim());
      setJobIds((prev) => [res.job_id, ...prev]);
      toast.success(`Processing ${res.file_count} files from folder`);
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setFolderProcessing(false);
    }
  }, [folderPath]);

  const handleJobComplete = useCallback(async () => {
    toast.success('Conversion complete!');
  }, []);

  const removeJob = useCallback(async (id: string) => {
    try {
      await apiDeleteJob(id);
    } catch { /* ignore 404 */ }
    setJobIds((prev) => prev.filter((j) => j !== id));
  }, []);

  const clearAllJobs = useCallback(async () => {
    try {
      await apiDeleteAllJobs();
    } catch { /* ignore */ }
    setJobIds([]);
  }, []);

  // --- Extract handlers ---
  const handleExtract = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    setExtracting(true);
    setExtractedData(null);
    try {
      const data = await extractFields(files[0]);
      setExtractedData(data);
      toast.success(`Extracted ${data.metadata.total_fields} fields`);
    } catch (err) {
      toast.error(`Extraction failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setExtracting(false);
    }
  }, []);

  // --- Validate handlers ---
  const handleValidate = useCallback(async (formFile: File, rulesFile: File) => {
    setValidating(true);
    setValidationResult(null);
    try {
      const result = await validateData(formFile, rulesFile);
      setValidationResult(result);
      if (result.valid) {
        toast.success('All validations passed!');
      } else {
        toast.error(`${result.errors.length} validation error(s) found`);
      }
    } catch (err) {
      toast.error(`Validation failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setValidating(false);
    }
  }, []);

  const groups: { id: TabGroup; label: string; tabs: { id: Tab; label: string; icon: React.ReactNode }[] }[] = [
    {
      id: 'digitalization',
      label: 'Digitalization Process',
      tabs: [
        { id: 'convert', label: 'Generate Editable PDF', icon: <FileUp className="w-4 h-4" /> },
        { id: 'required', label: 'Validation Rules', icon: <ListChecks className="w-4 h-4" /> },
      ],
    },
    {
      id: 'validation',
      label: 'Validation Process',
      tabs: [
        { id: 'extract', label: 'Extract Data', icon: <FileSearch className="w-4 h-4" /> },
        { id: 'validate', label: 'Validate Data', icon: <ShieldCheck className="w-4 h-4" /> },
      ],
    },
  ];

  return (
    <div style={{ minHeight: '100vh', background: '#EFF6FB', color: '#0B4778' }}>
      {/* Container */}
      <div style={{ maxWidth: '100%', margin: '0 auto', padding: '20px 20px 0 20px' }}>
        {/* HRSA Header */}
        <div style={{
          backgroundColor: '#0B4778',
          color: '#FFFFFF',
          padding: '20px 30px',
          borderTopLeftRadius: '12px',
          borderTopRightRadius: '12px',
          position: 'relative',
        }}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <img src="/image/HRSA_Logo.png" alt="HRSA" style={{ height: '48px', flexShrink: 0 }} />
              <div>
                <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 600, color: '#FFFFFF', lineHeight: 1.3 }}>
                  AI Based Universal 1-Tier Application Submission Assistant
                </h1>
                <p style={{ margin: 0, fontSize: '0.85rem', color: '#FFFFFF', opacity: 0.9, lineHeight: 1.3 }}>
                  Digitalization & Validation Process
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2" style={{ flexShrink: 0 }}>
              <Activity
                className={`w-4 h-4`}
                style={{ color: health?.status === 'ok' ? '#4ade80' : '#f87171' }}
              />
              <span style={{ fontSize: '0.75rem', color: '#FFFFFF', opacity: 0.8 }}>
                {health?.status === 'ok'
                  ? `API v${health.version}${health.azure_configured ? '' : ' (Azure N/A)'}`
                  : 'API offline'}
              </span>
            </div>
          </div>
        </div>

        {/* Card with red top accent */}
        <div style={{
          background: '#FFFFFF',
          borderBottomRightRadius: '12px',
          borderBottomLeftRadius: '12px',
          padding: '24px 30px 30px 30px',
          boxShadow: '0 10px 30px rgba(0, 0, 0, 0.1)',
          marginBottom: '20px',
          border: '1px solid #D9E8F6',
          borderTop: '5px solid #990000',
        }}>
          {/* Process Group Tabs */}
          <div style={{ display: 'flex', gap: '0', marginBottom: '0', borderBottom: '2px solid #D9E8F6' }}>
            {groups.map((group) => (
              <button
                key={group.id}
                onClick={() => {
                  setActiveGroup(group.id);
                  setActiveTab(group.tabs[0].id);
                }}
                style={{
                  padding: '12px 28px',
                  border: 'none',
                  borderBottom: activeGroup === group.id ? '3px solid #0B4778' : '3px solid transparent',
                  cursor: 'pointer',
                  fontSize: '0.95rem',
                  fontWeight: 700,
                  transition: 'all 0.2s',
                  background: 'transparent',
                  color: activeGroup === group.id ? '#0B4778' : '#94a3b8',
                  fontFamily: 'inherit',
                  marginBottom: '-2px',
                }}
              >
                {group.label}
              </button>
            ))}
          </div>

          {/* Sub-Tabs within active group */}
          <div style={{
            display: 'flex', gap: '4px', margin: '16px 0 20px 0',
            background: '#f1f5f9', borderRadius: '10px', padding: '4px',
            width: 'fit-content',
          }}>
            {groups.find(g => g.id === activeGroup)?.tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  padding: '10px 24px',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  fontSize: '0.9rem',
                  fontWeight: 600,
                  transition: 'all 0.3s',
                  background: activeTab === tab.id ? '#0B4778' : 'transparent',
                  color: activeTab === tab.id ? '#FFFFFF' : '#64748b',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontFamily: 'inherit',
                }}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div>
        {/* Convert Tab */}
        {activeTab === 'convert' && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Upload file */}
              <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
                <h2 className="text-sm font-semibold text-[#0B4778] mb-3 flex items-center gap-2">
                  <FileUp className="w-4 h-4 text-[#0B4778]" />
                  Upload Document
                </h2>
                <FileUploader
                  onFilesSelected={handleFileUpload}
                  multiple
                  label="Drop PDF or DOCX files"
                  description="Supports PDF and Word documents. Multiple files allowed."
                />
              </div>

              {/* Folder path */}
              <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
                <h2 className="text-sm font-semibold text-[#0B4778] mb-3 flex items-center gap-2">
                  <FolderOpen className="w-4 h-4 text-[#0B4778]" />
                  Convert Folder
                </h2>
                <div className="space-y-3">
                  <input
                    type="text"
                    value={folderPath}
                    onChange={(e) => setFolderPath(e.target.value)}
                    placeholder="C:\path\to\documents"
                    className="w-full px-3 py-2 border border-[#D9E8F6] rounded-lg text-sm text-[#0B4778] focus:outline-none focus:ring-2 focus:ring-[#3b82f6] focus:border-transparent bg-[#EFF6FB]"
                  />
                  <button
                    onClick={handleFolderConvert}
                    disabled={folderProcessing || !folderPath.trim()}
                    className="w-full px-4 py-2 bg-[#0B4778] text-white text-sm font-medium rounded-lg hover:bg-[#093d66] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {folderProcessing ? 'Processing...' : 'Convert All Files in Folder'}
                  </button>
                </div>
              </div>
            </div>

            {/* Jobs — newest first, persisted on backend disk */}
            {jobIds.length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-[#0B4778]">
                    Conversion Jobs ({jobIds.length})
                  </h2>
                  <button
                    onClick={clearAllJobs}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-red-600 bg-red-50 border border-red-200 rounded-md hover:bg-red-100 transition-colors"
                  >
                    <Trash2 className="w-3 h-3" />
                    Clear All
                  </button>
                </div>
                {jobIds.map((id) => (
                  <JobTracker key={id} jobId={id} onComplete={handleJobComplete} onDelete={() => removeJob(id)} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Required Fields Tab */}
        {activeTab === 'required' && <RequiredFieldsTab />}

        {/* Extract Tab */}
        {activeTab === 'extract' && (
          <div className="space-y-6">
            {/* Extract filled data */}
            <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
              <h2 className="text-sm font-semibold text-[#0B4778] mb-1 flex items-center gap-2">
                <FileSearch className="w-4 h-4 text-[#0B4778]" />
                Extract Field Data from PDF
              </h2>
              <p className="text-xs text-[#64748b] mb-4">
                Upload an editable PDF to extract all form field controls and their values as JSON.
                The output uses the same field structure as the <strong>Validation Rules</strong>.
              </p>
              <FileUploader
                onFilesSelected={handleExtract}
                accept={{ 'application/pdf': ['.pdf'] }}
                label="Drop editable PDF here"
                description="Supports any editable PDF with form controls"
                disabled={extracting}
              />
              {extracting && (
                <p className="text-sm text-[#0B4778] mt-3 animate-pulse">
                  Extracting form fields...
                </p>
              )}
            </div>

            {extractedData && (
              <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-sm font-semibold text-[#0B4778]">
                      Extracted Fields
                    </h2>
                    <p className="text-xs text-[#64748b] mt-0.5">
                      {extractedData.metadata.source_file} — {extractedData.metadata.page_count} page{extractedData.metadata.page_count > 1 ? 's' : ''}, {extractedData.metadata.total_fields} fields
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(
                          JSON.stringify(extractedData, null, 2)
                        );
                        toast.success('JSON copied to clipboard');
                      }}
                      className="px-3 py-1.5 bg-[#EFF6FB] text-[#0B4778] text-xs font-medium rounded-md hover:bg-[#D9E8F6] transition-colors border border-[#D9E8F6]"
                    >
                      Copy JSON
                    </button>
                    <button
                      onClick={() => {
                        const blob = new Blob(
                          [JSON.stringify(extractedData, null, 2)],
                          { type: 'application/json' }
                        );
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'extracted_fields.json';
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                      className="px-3 py-1.5 bg-[#0B4778] text-white text-xs font-medium rounded-md hover:bg-[#093d66] transition-colors"
                    >
                      Download JSON
                    </button>
                  </div>
                </div>

                {/* Summary badges */}
                <div className="flex flex-wrap gap-2 mb-4">
                  {Object.entries(
                    extractedData.fields.reduce<Record<string, number>>((acc, f) => {
                      acc[f.field_type] = (acc[f.field_type] || 0) + 1;
                      return acc;
                    }, {})
                  ).map(([type, count]) => (
                    <span key={type} className="px-2 py-0.5 bg-[#EFF6FB] text-[#0B4778] text-xs rounded-full border border-[#D9E8F6]">
                      {type} × {count}
                    </span>
                  ))}
                  <span className="px-2 py-0.5 bg-[#F0FDF4] text-[#16a34a] text-xs rounded-full border border-[#bbf7d0]">
                    {extractedData.fields.filter(f => f.value && String(f.value).trim()).length} filled
                  </span>
                  <span className="px-2 py-0.5 bg-[#f1f5f9] text-[#64748b] text-xs rounded-full border border-[#D9E8F6]">
                    {extractedData.fields.filter(f => !f.value || !String(f.value).trim()).length} empty
                  </span>
                </div>

                {/* Fields table */}
                <div className="overflow-x-auto max-h-[500px] overflow-y-auto border border-[#D9E8F6] rounded-lg">
                  <table className="w-full text-xs">
                    <thead className="bg-[#EFF6FB] sticky top-0">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium text-[#0B4778]">Pg</th>
                        <th className="text-left px-3 py-2 font-medium text-[#0B4778]">Label</th>
                        <th className="text-left px-3 py-2 font-medium text-[#0B4778]">Field ID</th>
                        <th className="text-left px-3 py-2 font-medium text-[#0B4778]">Type</th>
                        <th className="text-left px-3 py-2 font-medium text-[#0B4778]">Value</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#D9E8F6]">
                      {extractedData.fields.map((f, i) => (
                        <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-[#EFF6FB]/50'}>
                          <td className="px-3 py-1.5 text-[#94a3b8]">{f.page}</td>
                          <td className="px-3 py-1.5 text-[#0B4778] font-medium max-w-[200px] truncate" title={f.label}>{f.label || '—'}</td>
                          <td className="px-3 py-1.5 text-[#64748b] font-mono max-w-[160px] truncate" title={f.field_id}>{f.field_id}</td>
                          <td className="px-3 py-1.5">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                              f.field_type === 'radio' ? 'bg-purple-50 text-purple-700' :
                              f.field_type === 'checkbox' ? 'bg-amber-50 text-amber-700' :
                              f.field_type === 'textarea' ? 'bg-blue-50 text-blue-700' :
                              'bg-gray-100 text-gray-600'
                            }`}>
                              {f.field_type}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 text-[#0B4778] max-w-[250px] truncate" title={String(f.value ?? '')}>
                            {f.value && String(f.value).trim() ? String(f.value) : <span className="text-[#D9E8F6] italic">empty</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Validate Tab */}
        {activeTab === 'validate' && (
          <ValidateTab
            onValidate={handleValidate}
            validating={validating}
            result={validationResult}
          />
        )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ValidateTab({
  onValidate,
  validating,
  result,
}: {
  onValidate: (formFile: File, rulesFile: File) => void;
  validating: boolean;
  result: ValidationResult | null;
}) {
  const [formFile, setFormFile] = useState<File | null>(null);
  const [rulesFile, setRulesFile] = useState<File | null>(null);

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
        <h2 className="text-sm font-semibold text-[#0B4778] mb-3 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-[#0B4778]" />
          Validate Form Data Against Rules
        </h2>
        <p className="text-xs text-[#64748b] mb-4">
          Upload the extracted form data JSON and a rules JSON file to validate.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs font-medium text-[#0B4778] mb-1 block">
              Form Data JSON
            </label>
            <FileUploader
              onFilesSelected={(files) => setFormFile(files[0])}
              accept={{ 'application/json': ['.json'] }}
              label={formFile ? formFile.name : 'Drop form data JSON'}
              description="The extracted_data.json from the Extract tab"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-[#0B4778] mb-1 block">
              Rules JSON
            </label>
            <FileUploader
              onFilesSelected={(files) => setRulesFile(files[0])}
              accept={{ 'application/json': ['.json'] }}
              label={rulesFile ? rulesFile.name : 'Drop rules JSON'}
              description="Business rules definition file"
            />
          </div>
        </div>

        <button
          onClick={() => {
            if (formFile && rulesFile) onValidate(formFile, rulesFile);
          }}
          disabled={!formFile || !rulesFile || validating}
          className="mt-4 w-full px-4 py-2 bg-[#0B4778] text-white text-sm font-medium rounded-lg hover:bg-[#093d66] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {validating ? 'Validating...' : 'Run Validation'}
        </button>
      </div>

      {result && (
        <div className="bg-white rounded-xl border border-[#D9E8F6] shadow-sm p-5">
          <h2 className="text-sm font-semibold text-[#0B4778] mb-3">
            Validation Results
          </h2>
          <ValidationViewer result={result} />
        </div>
      )}
    </div>
  );
}

export default App;
