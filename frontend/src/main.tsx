import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { Toaster } from 'react-hot-toast'
import App from './App'

// Catch uncaught errors and display them
window.onerror = (msg, src, line, col, err) => {
  document.getElementById('root')!.innerHTML = `
    <div style="padding:40px;font-family:monospace">
      <h1 style="color:red">JS Error</h1>
      <pre style="white-space:pre-wrap;background:#f5f5f5;padding:20px">${msg}\n${src}:${line}:${col}\n${err?.stack || ''}</pre>
    </div>`;
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Toaster position="top-right" />
    <App />
  </StrictMode>,
)
