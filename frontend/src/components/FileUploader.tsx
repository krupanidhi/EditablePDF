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
          ? 'border-[#3b82f6] bg-[#EFF6FB]'
          : 'border-[#D9E8F6] hover:border-[#3b82f6] hover:bg-[#EFF6FB]'
        }
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
      `}
    >
      <input {...getInputProps()} />
      <div className="flex flex-col items-center gap-3">
        {isDragActive ? (
          <Upload className="w-10 h-10 text-[#3b82f6] animate-bounce" />
        ) : (
          <FileText className="w-10 h-10 text-[#94a3b8]" />
        )}
        <div>
          <p className="text-sm font-semibold text-[#0B4778]">{label}</p>
          <p className="text-xs text-[#64748b] mt-1">{description}</p>
        </div>
      </div>
    </div>
  );
}
