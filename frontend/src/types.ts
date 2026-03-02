export interface ConvertResponse {
  job_id: string;
  status: string;
}

export interface FolderConvertResponse {
  job_id: string;
  status: string;
  file_count: number;
}

export interface JobResult {
  editable_pdf: string;
  schema: string;
  stats: {
    pages: number;
    total_fields: number;
    by_type: Record<string, number>;
    processing_time_sec: number;
  };
}

export interface Job {
  id: string;
  status: 'processing' | 'completed' | 'failed';
  input_file?: string;
  input_folder?: string;
  file_count?: number;
  created_at: string;
  result?: JobResult;
  results?: { file: string; result: JobResult }[];
  errors?: { file: string; error: string }[];
  error?: string;
  completed?: number;
}

export interface FieldSchema {
  field_id: string;
  page: number;
  type: string;
  label: string;
  bbox: number[];
  required: boolean;
  validation: {
    data_type: string;
    max_length: number | null;
    pattern: string | null;
    min: number | null;
    max: number | null;
    format: string | null;
  } | null;
  group: string | null;
  options: { value: string; label: string; bbox: number[] | null }[] | null;
  depends_on: {
    field: string;
    condition: string;
    value: string;
    then_required: boolean;
  } | null;
}

export interface FormSchema {
  metadata: {
    source_file: string;
    generated_at: string;
    page_count: number;
    tool_version: string;
  };
  fields: FieldSchema[];
}

export interface ExtractedField {
  field_id: string;
  page: number;
  type: string;
  value: string | boolean | null;
  is_filled: boolean;
  label?: string;
  required?: boolean;
  checked?: boolean;
  selected_option?: string | null;
}

export interface ExtractedData {
  metadata: {
    source_file: string;
    extracted_at: string;
    page_count: number;
    tool_version: string;
    schema_matched?: string | null;
    fields_enriched?: boolean;
  };
  pages: Record<string, ExtractedField[]>;
  fields: ExtractedField[];
  summary: {
    total_fields: number;
    filled_fields: number;
    empty_fields: number;
    by_type: Record<string, number>;
  };
}

export interface ValidationResult {
  valid: boolean;
  errors: { rule_id: string; name: string; message: string }[];
  warnings: { rule_id: string; name: string; message: string }[];
  passed: { rule_id: string; name: string; message: string }[];
  skipped?: { rule_id: string; name: string; reason: string }[];
}

export interface AddRowsResponse {
  status: string;
  visible_rows: number;
  total_rows: number;
  hidden_rows: number;
  pre_created_fields: number;
  output_file: string;
  download_url: string;
}

export interface ExtractedFieldClean {
  label: string;
  field_id: string;
  field_type: string;
  value: string | boolean | null;
  page: number;
  required: boolean;
  data_type: string;
  readonly: boolean;
  max_length: number | null;
  xfa_name?: string;
}

export interface ExtractFieldsResponse {
  metadata: {
    source_file: string;
    extracted_at: string;
    page_count: number;
    total_fields: number;
  };
  fields: ExtractedFieldClean[];
}

export interface ApplyRequiredResponse {
  status: string;
  output_file: string;
  fields_updated: number;
  fields_total: number;
  download_url: string;
}

export interface HealthCheck {
  status: string;
  version: string;
  azure_configured: boolean;
}
