import { useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import { Upload, FileText } from 'lucide-react';

interface FileUploaderProps {
  onFilesSelected: (files: File[]) => void;
  accept?: Record<string, string[]>;
  multiple?: boolean;
  label?: string;
  description?: string;
  disabled?: boolean;
}

export default function FileUploader({
  onFilesSelected,
  accept = {
    'application/pdf': ['.pdf'],
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  },
  multiple = false,
  label = 'Upload Document',
  description = 'Drag & drop PDF or DOCX files here, or click to browse',
  disabled = false,
}: FileUploaderProps) {
  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      if (acceptedFiles.length > 0) {
        onFilesSelected(acceptedFiles);
      }
    },
    [onFilesSelected]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept,
    multiple,
    disabled,
  });

  return (
    <div
      {...getRootProps()}
      className={`
        border-2 border-dashed rounded-xl p-8 text-center cursor-pointer
        transition-all duration-200 ease-in-out
        ${isDragActive
          ? 'border-blue-500 bg-blue-50'
          : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
        }
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
      `}
    >
      <input {...getInputProps()} />
      <div className="flex flex-col items-center gap-3">
        {isDragActive ? (
          <Upload className="w-10 h-10 text-blue-500 animate-bounce" />
        ) : (
          <FileText className="w-10 h-10 text-gray-400" />
        )}
        <div>
          <p className="text-sm font-semibold text-gray-700">{label}</p>
          <p className="text-xs text-gray-500 mt-1">{description}</p>
        </div>
      </div>
    </div>
  );
}
