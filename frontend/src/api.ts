import axios from 'axios';
import type {
  ConvertResponse,
  FolderConvertResponse,
  Job,
  ExtractedData,
  ExtractFieldsResponse,
  ApplyRequiredResponse,
  ValidationResult,
  AddRowsResponse,
  HealthCheck,
} from './types';

const api = axios.create({
  baseURL: '/api',
  timeout: 300000, // 5 min for large PDFs
});

export async function healthCheck(): Promise<HealthCheck> {
  const { data } = await api.get<HealthCheck>('/health');
  return data;
}

export async function convertFile(file: File): Promise<ConvertResponse> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await api.post<ConvertResponse>('/convert', form);
  return data;
}

export async function convertFolder(folderPath: string): Promise<FolderConvertResponse> {
  const form = new FormData();
  form.append('folder_path', folderPath);
  const { data } = await api.post<FolderConvertResponse>('/convert-folder', form);
  return data;
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await api.get<Job>(`/jobs/${jobId}`);
  return data;
}

export async function extractData(file: File, schemaFile?: File): Promise<ExtractedData> {
  const form = new FormData();
  form.append('file', file);
  if (schemaFile) {
    form.append('schema_file', schemaFile);
  }
  const { data } = await api.post<ExtractedData>('/extract', form);
  return data;
}

export async function validateData(
  formDataFile: File,
  rulesFile: File
): Promise<ValidationResult> {
  const form = new FormData();
  form.append('form_data_file', formDataFile);
  form.append('rules_file', rulesFile);
  const { data } = await api.post<ValidationResult>('/validate', form);
  return data;
}

export async function addRows(
  file: File,
  rowsToAdd: number
): Promise<AddRowsResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('rows_to_add', String(rowsToAdd));
  const { data } = await api.post<AddRowsResponse>('/add-rows', form);
  return data;
}

export async function extractFields(file: File): Promise<ExtractFieldsResponse> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await api.post<ExtractFieldsResponse>('/extract-fields', form);
  return data;
}

export async function applyRequired(
  file: File,
  fieldsJson: Blob
): Promise<ApplyRequiredResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('fields_json', fieldsJson, 'fields.json');
  const { data } = await api.post<ApplyRequiredResponse>('/apply-required', form);
  return data;
}

export function getDownloadUrl(filename: string): string {
  return `/api/download/${encodeURIComponent(filename)}`;
}
