import { useState, useRef } from "react";
import { api } from "../api";

interface Props {
  onUploaded: () => void;
}

export function UploadPanel({ onUploaded }: Props) {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File) {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.uploadCSV(file);
      setResult(
        `Loaded ${res.residents} residents — ${res.inserted} new shifts, ${res.updated} updated.`
      );
      setTimeout(onUploaded, 1200);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setLoading(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }

  return (
    <div className="upload-panel">
      <div className="upload-hero">
        <div className="upload-icon">📅</div>
        <h2>Shift Swap Finder</h2>
        <p>Load your QGenda schedule to find valid swap options.</p>
      </div>

      <div
        className={`drop-zone ${dragging ? "drag-over" : ""} ${loading ? "loading" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          style={{ display: "none" }}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
        />
        {loading ? (
          <span className="spinner" />
        ) : (
          <>
            <span className="drop-icon">⬆</span>
            <span>Drop CSV here or click to browse</span>
          </>
        )}
      </div>

      {result && <div className="upload-success">{result}</div>}
      {error && <div className="upload-error">{error}</div>}

      <div className="upload-help">
        <strong>How to export from QGenda:</strong>
        <ol>
          <li>Go to <em>Reports → Schedule Export</em></li>
          <li>Select your date range and all residents</li>
          <li>Download as CSV and upload above</li>
        </ol>
      </div>
    </div>
  );
}
