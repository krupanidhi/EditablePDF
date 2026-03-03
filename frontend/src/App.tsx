import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import {
  FileUp,
  FolderOpen,
  FileSearch,
  ShieldCheck,
  Activity,
  FileText,
  ListChecks,
} from 'lucide-react';
import { healthCheck, convertFile, convertFolder, extractFields, validateData } from './api';
import type { FormSchema, ExtractFieldsResponse, ValidationResult, HealthCheck } from './types';
import FileUploader from './components/FileUploader';
import JobTracker from './components/JobTracker';
import SchemaViewer from './components/SchemaViewer';
import ValidationViewer from './components/ValidationViewer';
import RequiredFieldsTab from './components/RequiredFieldsTab';

type Tab = 'convert' | 'extract' | 'required' | 'validate';

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('convert');
  const [health, setHealth] = useState<HealthCheck | null>(null);

  // Convert state
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [folderPath, setFolderPath] = useState('');
  const [folderProcessing, setFolderProcessing] = useState(false);
  const [completedSchemas, setCompletedSchemas] = useState<FormSchema[]>([]);

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
  }, []);

  // --- Convert handlers ---
  const handleFileUpload = useCallback(async (files: File[]) => {
    for (const file of files) {
      try {
        const res = await convertFile(file);
        setJobIds((prev) => [...prev, res.job_id]);
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
      setJobIds((prev) => [...prev, res.job_id]);
      toast.success(`Processing ${res.file_count} files from folder`);
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setFolderProcessing(false);
    }
  }, [folderPath]);

  const handleJobComplete = useCallback(async (job: { result?: { schema: string } }) => {
    if (job.result?.schema) {
      try {
        const filename = job.result.schema.split(/[\\/]/).pop() || '';
        const res = await fetch(`/api/download/${encodeURIComponent(filename)}`);
        if (res.ok) {
          const schema = await res.json() as FormSchema;
          if (schema?.metadata?.source_file) {
            setCompletedSchemas((prev) => [...prev, schema]);
          }
        }
      } catch {
        // Schema fetch failed, not critical
      }
    }
    toast.success('Conversion complete!');
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

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'convert', label: 'Convert', icon: <FileUp className="w-4 h-4" /> },
    { id: 'required', label: 'Digitalization Workflow', icon: <ListChecks className="w-4 h-4" /> },
    { id: 'extract', label: 'Extract', icon: <FileSearch className="w-4 h-4" /> },
    { id: 'validate', label: 'Validate', icon: <ShieldCheck className="w-4 h-4" /> },
  ];

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <FileText className="w-7 h-7 text-blue-600" />
            <div>
              <h1 className="text-lg font-bold text-gray-900">AI Based Universal 1-Tier Application Submission Assistant</h1>
              <p className="text-xs text-gray-500">Document → Editable Form Converter & Digitalization</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Activity
              className={`w-4 h-4 ${
                health?.status === 'ok' ? 'text-green-500' : 'text-red-400'
              }`}
            />
            <span className="text-xs text-gray-500">
              {health?.status === 'ok'
                ? `API v${health.version}${health.azure_configured ? '' : ' (Azure not configured)'}`
                : 'API offline'}
            </span>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <div className="max-w-6xl mx-auto px-6 pt-6">
        <div className="flex gap-1 bg-gray-100 p-1 rounded-lg w-fit">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all ${
                activeTab === tab.id
                  ? 'bg-white text-blue-700 shadow-sm'
                  : 'text-gray-600 hover:text-gray-800'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-6 py-6">
        {/* Convert Tab */}
        {activeTab === 'convert' && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Upload file */}
              <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
                <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  <FileUp className="w-4 h-4 text-blue-500" />
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
              <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
                <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  <FolderOpen className="w-4 h-4 text-purple-500" />
                  Convert Folder
                </h2>
                <div className="space-y-3">
                  <input
                    type="text"
                    value={folderPath}
                    onChange={(e) => setFolderPath(e.target.value)}
                    placeholder="C:\path\to\documents"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                  <button
                    onClick={handleFolderConvert}
                    disabled={folderProcessing || !folderPath.trim()}
                    className="w-full px-4 py-2 bg-purple-600 text-white text-sm font-medium rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {folderProcessing ? 'Processing...' : 'Convert All Files in Folder'}
                  </button>
                </div>
              </div>
            </div>

            {/* Active jobs */}
            {jobIds.length > 0 && (
              <div className="space-y-3">
                <h2 className="text-sm font-semibold text-gray-800">Jobs</h2>
                {jobIds.map((id) => (
                  <JobTracker key={id} jobId={id} onComplete={handleJobComplete} />
                ))}
              </div>
            )}

            {/* Completed schemas */}
            {completedSchemas.length > 0 && (
              <div className="space-y-3">
                <h2 className="text-sm font-semibold text-gray-800">
                  Detected Fields
                </h2>
                {completedSchemas.map((schema, i) => (
                  <div
                    key={i}
                    className="bg-white rounded-xl border border-gray-200 shadow-sm p-5"
                  >
                    <p className="text-xs text-gray-500 mb-3">
                      {schema.metadata.source_file}
                    </p>
                    <SchemaViewer schema={schema} />
                  </div>
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
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
              <h2 className="text-sm font-semibold text-gray-800 mb-1 flex items-center gap-2">
                <FileSearch className="w-4 h-4 text-blue-500" />
                Extract Field Data from PDF
              </h2>
              <p className="text-xs text-gray-500 mb-4">
                Upload an editable PDF to extract all form field controls and their values as JSON.
                The output uses the same field structure as the <strong>Digitalization Workflow</strong>.
              </p>
              <FileUploader
                onFilesSelected={handleExtract}
                accept={{ 'application/pdf': ['.pdf'] }}
                label="Drop editable PDF here"
                description="Supports any editable PDF with form controls"
                disabled={extracting}
              />
              {extracting && (
                <p className="text-sm text-blue-600 mt-3 animate-pulse">
                  Extracting form fields...
                </p>
              )}
            </div>

            {extractedData && (
              <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-sm font-semibold text-gray-800">
                      Extracted Fields
                    </h2>
                    <p className="text-xs text-gray-500 mt-0.5">
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
                      className="px-3 py-1.5 bg-gray-100 text-gray-700 text-xs font-medium rounded-md hover:bg-gray-200 transition-colors border border-gray-300"
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
                      className="px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-md hover:bg-green-700 transition-colors"
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
                    <span key={type} className="px-2 py-0.5 bg-blue-50 text-blue-700 text-xs rounded-full border border-blue-200">
                      {type} × {count}
                    </span>
                  ))}
                  <span className="px-2 py-0.5 bg-green-50 text-green-700 text-xs rounded-full border border-green-200">
                    {extractedData.fields.filter(f => f.value && String(f.value).trim()).length} filled
                  </span>
                  <span className="px-2 py-0.5 bg-gray-50 text-gray-500 text-xs rounded-full border border-gray-200">
                    {extractedData.fields.filter(f => !f.value || !String(f.value).trim()).length} empty
                  </span>
                </div>

                {/* Fields table */}
                <div className="overflow-x-auto max-h-[500px] overflow-y-auto border border-gray-200 rounded-lg">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50 sticky top-0">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Pg</th>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Label</th>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Field ID</th>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Type</th>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Value</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {extractedData.fields.map((f, i) => (
                        <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                          <td className="px-3 py-1.5 text-gray-400">{f.page}</td>
                          <td className="px-3 py-1.5 text-gray-800 font-medium max-w-[200px] truncate" title={f.label}>{f.label || '—'}</td>
                          <td className="px-3 py-1.5 text-gray-500 font-mono max-w-[160px] truncate" title={f.field_id}>{f.field_id}</td>
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
                          <td className="px-3 py-1.5 text-gray-700 max-w-[250px] truncate" title={String(f.value ?? '')}>
                            {f.value && String(f.value).trim() ? String(f.value) : <span className="text-gray-300 italic">empty</span>}
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
      </main>
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
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-amber-500" />
          Validate Form Data Against Rules
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          Upload the extracted form data JSON and a rules JSON file to validate.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs font-medium text-gray-600 mb-1 block">
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
            <label className="text-xs font-medium text-gray-600 mb-1 block">
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
          className="mt-4 w-full px-4 py-2 bg-amber-600 text-white text-sm font-medium rounded-lg hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {validating ? 'Validating...' : 'Run Validation'}
        </button>
      </div>

      {result && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
          <h2 className="text-sm font-semibold text-gray-800 mb-3">
            Validation Results
          </h2>
          <ValidationViewer result={result} />
        </div>
      )}
    </div>
  );
}

export default App;
