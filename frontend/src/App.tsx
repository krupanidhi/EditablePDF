import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import {
  FileUp,
  FolderOpen,
  FileSearch,
  ShieldCheck,
  Activity,
  FileText,
  Plus,
  Download,
  TableProperties,
  ListChecks,
} from 'lucide-react';
import { healthCheck, convertFile, convertFolder, extractData, validateData, addRows, getDownloadUrl } from './api';
import type { FormSchema, ExtractedData, ValidationResult, AddRowsResponse, HealthCheck } from './types';
import FileUploader from './components/FileUploader';
import JobTracker from './components/JobTracker';
import SchemaViewer from './components/SchemaViewer';
import ExtractedDataViewer from './components/ExtractedDataViewer';
import ValidationViewer from './components/ValidationViewer';
import RequiredFieldsTab from './components/RequiredFieldsTab';

type Tab = 'convert' | 'extract' | 'required' | 'validate' | 'add-rows';

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('convert');
  const [health, setHealth] = useState<HealthCheck | null>(null);

  // Convert state
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [folderPath, setFolderPath] = useState('');
  const [folderProcessing, setFolderProcessing] = useState(false);
  const [completedSchemas, setCompletedSchemas] = useState<FormSchema[]>([]);

  // Extract state
  const [extractedData, setExtractedData] = useState<ExtractedData | null>(null);
  const [extracting, setExtracting] = useState(false);

  // Validate state
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);

  // Add Rows state
  const [addRowsResult, setAddRowsResult] = useState<AddRowsResponse | null>(null);
  const [addingRows, setAddingRows] = useState(false);

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
      const data = await extractData(files[0]);
      setExtractedData(data);
      toast.success(`Extracted ${data.summary.total_fields} fields`);
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
    { id: 'extract', label: 'Extract', icon: <FileSearch className="w-4 h-4" /> },
    { id: 'required', label: 'Field Validation', icon: <ListChecks className="w-4 h-4" /> },
    { id: 'validate', label: 'Validate', icon: <ShieldCheck className="w-4 h-4" /> },
    { id: 'add-rows', label: 'Add Rows', icon: <TableProperties className="w-4 h-4" /> },
  ];

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <FileText className="w-7 h-7 text-blue-600" />
            <div>
              <h1 className="text-lg font-bold text-gray-900">EditablePDF</h1>
              <p className="text-xs text-gray-500">Document → Editable Form Converter</p>
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

        {/* Extract Tab */}
        {activeTab === 'extract' && (
          <div className="space-y-6">
            {/* Extract filled data */}
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
              <h2 className="text-sm font-semibold text-gray-800 mb-1 flex items-center gap-2">
                <FileSearch className="w-4 h-4 text-blue-500" />
                Extract Filled Values from PDF
              </h2>
              <p className="text-xs text-gray-500 mb-4">
                Upload a <strong>filled</strong> editable PDF to extract user-entered values with schema enrichment.
              </p>
              <FileUploader
                onFilesSelected={handleExtract}
                accept={{ 'application/pdf': ['.pdf'] }}
                label="Drop filled PDF"
                description="Upload the editable PDF that has been filled out"
                disabled={extracting}
              />
              {extracting && (
                <p className="text-sm text-blue-600 mt-3 animate-pulse">
                  Extracting form data...
                </p>
              )}
            </div>

            {extractedData && (
              <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-sm font-semibold text-gray-800">
                      Extracted Data
                    </h2>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {extractedData.metadata.source_file.split(/[\\/]/).pop()} — {extractedData.metadata.page_count} pages
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
                        a.download = 'extracted_data.json';
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                      className="px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-md hover:bg-green-700 transition-colors"
                    >
                      Download JSON
                    </button>
                  </div>
                </div>
                <ExtractedDataViewer data={extractedData} />
              </div>
            )}
          </div>
        )}

        {/* Required Fields Tab */}
        {activeTab === 'required' && <RequiredFieldsTab />}

        {/* Validate Tab */}
        {activeTab === 'validate' && (
          <ValidateTab
            onValidate={handleValidate}
            validating={validating}
            result={validationResult}
          />
        )}

        {/* Add Rows Tab */}
        {activeTab === 'add-rows' && (
          <AddRowsTab
            addingRows={addingRows}
            setAddingRows={setAddingRows}
            result={addRowsResult}
            setResult={setAddRowsResult}
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

function AddRowsTab({
  addingRows,
  setAddingRows,
  result,
  setResult,
}: {
  addingRows: boolean;
  setAddingRows: (v: boolean) => void;
  result: AddRowsResponse | null;
  setResult: (v: AddRowsResponse | null) => void;
}) {
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [maxRows, setMaxRows] = useState(20);

  const handleAddRows = async () => {
    if (!pdfFile) return;
    setAddingRows(true);
    setResult(null);
    try {
      const res = await addRows(pdfFile, maxRows);
      setResult(res);
      toast.success(`PDF ready with dynamic "Add Row" button (up to ${res.total_rows} rows)`);
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setAddingRows(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <h2 className="text-sm font-semibold text-gray-800 mb-1 flex items-center gap-2">
          <TableProperties className="w-4 h-4 text-indigo-500" />
          Dynamic Rows — Embed "Add Row" Button in PDF
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          Upload an editable PDF with a table (e.g. Equipment List). The system will embed
          a <strong>"+ Add Row"</strong> button directly inside the PDF. Users can click it
          in Adobe Acrobat or Foxit Reader to dynamically add more rows.
          The PDF starts with 1 visible row.
        </p>

        <div className="space-y-4">
          <FileUploader
            onFilesSelected={(files) => setPdfFile(files[0])}
            accept={{ 'application/pdf': ['.pdf'] }}
            label={pdfFile ? pdfFile.name : 'Drop editable PDF with table'}
            description="Upload an editable PDF that has a repeating table structure"
          />

          <div className="flex items-end gap-4">
            <div className="flex-1">
              <label className="text-xs font-medium text-gray-600 mb-1.5 block">
                Maximum rows available (pre-created hidden)
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={5}
                  max={50}
                  value={maxRows}
                  onChange={(e) => setMaxRows(Number(e.target.value))}
                  className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                />
                <span className="text-lg font-bold text-indigo-700 w-8 text-center">
                  {maxRows}
                </span>
              </div>
            </div>

            <button
              onClick={handleAddRows}
              disabled={!pdfFile || addingRows}
              className="px-5 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              {addingRows ? 'Processing...' : 'Build Dynamic PDF'}
            </button>
          </div>
        </div>

        {addingRows && (
          <p className="text-sm text-indigo-600 mt-3 animate-pulse">
            Embedding "Add Row" button and pre-creating hidden rows...
          </p>
        )}
      </div>

      {result && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center">
              <Plus className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-800">
                Dynamic PDF Ready
              </h2>
              <p className="text-xs text-gray-500">
                PDF has a "+ Add Row" button. Starts with {result.visible_rows} row, supports up to {result.total_rows} rows.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3 mb-4">
            <div className="bg-indigo-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-indigo-700">{result.visible_rows}</p>
              <p className="text-xs text-indigo-600">Visible Rows</p>
            </div>
            <div className="bg-blue-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-blue-700">{result.total_rows}</p>
              <p className="text-xs text-blue-600">Max Rows</p>
            </div>
            <div className="bg-amber-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-amber-700">{result.hidden_rows}</p>
              <p className="text-xs text-amber-600">Hidden (expandable)</p>
            </div>
          </div>

          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 text-xs text-blue-700">
            <strong>How it works:</strong> Open the downloaded PDF in Adobe Acrobat or Foxit Reader.
            Click the <strong>"+ Add Row"</strong> button to reveal one more row each time.
          </div>

          <a
            href={getDownloadUrl(result.output_file)}
            download
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors"
          >
            <Download className="w-4 h-4" />
            Download Dynamic PDF
          </a>
        </div>
      )}
    </div>
  );
}

export default App;
