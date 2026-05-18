import { useState, useEffect, useCallback } from "react";
import { api, Resident, ShiftAssignment, SwapResponse } from "./api";
import { UploadPanel } from "./components/UploadPanel";
import { Calendar } from "./components/Calendar";
import { SwapResults } from "./components/SwapResults";
import "./styles.css";

function today() {
  return new Date();
}

export default function App() {
  const [residents, setResidents] = useState<Resident[] | null>(null);
  const [selectedResident, setSelectedResident] = useState<Resident | null>(null);
  const [year, setYear] = useState(today().getFullYear());
  const [month, setMonth] = useState(today().getMonth() + 1);
  const [assignments, setAssignments] = useState<ShiftAssignment[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [swapResult, setSwapResult] = useState<SwapResponse | null>(null);
  const [loadingSwap, setLoadingSwap] = useState(false);
  const [swapError, setSwapError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const loadResidents = useCallback(async () => {
    try {
      const list = await api.getResidents();
      setResidents(list);
      if (list.length > 0 && !selectedResident) {
        setSelectedResident(list[0]);
      }
    } catch {
      setResidents([]);
    }
  }, [selectedResident]);

  useEffect(() => { loadResidents(); }, []);

  useEffect(() => {
    if (!selectedResident) return;
    const start = `${year}-${String(month).padStart(2, "0")}-01`;
    const lastDay = new Date(year, month, 0).getDate();
    const end = `${year}-${String(month).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`;
    api.getSchedule(selectedResident.id, start, end).then(setAssignments).catch(() => setAssignments([]));
    setSelectedDate(null);
    setSwapResult(null);
  }, [selectedResident, year, month]);

  async function handleDateSelect(date: string) {
    setSelectedDate(date);
    setSwapResult(null);
    setSwapError(null);
    if (!selectedResident) return;
    setLoadingSwap(true);
    try {
      const result = await api.getSwapOptions(selectedResident.id, date);
      setSwapResult(result);
    } catch (e: unknown) {
      setSwapError(e instanceof Error ? e.message : "Failed to load swap options.");
    } finally {
      setLoadingSwap(false);
    }
  }

  function prevMonth() {
    if (month === 1) { setYear(y => y - 1); setMonth(12); }
    else setMonth(m => m - 1);
  }
  function nextMonth() {
    if (month === 12) { setYear(y => y + 1); setMonth(1); }
    else setMonth(m => m + 1);
  }

  // No residents loaded yet — show upload screen
  if (residents !== null && residents.length === 0 && !uploading) {
    return (
      <div className="app-shell">
        <UploadPanel onUploaded={() => { setUploading(false); loadResidents(); }} />
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-title">
          <span className="app-logo">⇄</span>
          Shift Swap Finder
        </div>
        <div className="header-actions">
          <label className="upload-btn" title="Upload new QGenda CSV">
            Upload CSV
            <input
              type="file"
              accept=".csv"
              style={{ display: "none" }}
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                try {
                  await api.uploadCSV(f);
                  await loadResidents();
                } catch {/* ignore */}
                e.target.value = "";
              }}
            />
          </label>
        </div>
      </header>

      <div className="app-body">
        {/* Sidebar */}
        <aside className="sidebar">
          <label className="field-label">Resident</label>
          <select
            className="resident-select"
            value={selectedResident?.id ?? ""}
            onChange={(e) => {
              const r = residents?.find((r) => r.id === Number(e.target.value));
              if (r) setSelectedResident(r);
            }}
          >
            {residents?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} ({r.level})
              </option>
            ))}
          </select>

          {selectedResident && (
            <div className="resident-card">
              <div className="resident-name">{selectedResident.name}</div>
              <div className={`resident-level level-${selectedResident.level.toLowerCase()}`}>
                {selectedResident.level} Resident
              </div>
              <div className="resident-stats">
                {assignments.length} shift{assignments.length !== 1 ? "s" : ""} this month
              </div>
            </div>
          )}

          <div className="sidebar-hint">
            Click a working day on the calendar to see swap options.
          </div>
        </aside>

        {/* Main content */}
        <main className="main-content">
          {residents === null ? (
            <div className="loading-state">Loading...</div>
          ) : (
            <>
              <Calendar
                year={year}
                month={month}
                assignments={assignments}
                selectedDate={selectedDate}
                onSelectDate={handleDateSelect}
                onPrev={prevMonth}
                onNext={nextMonth}
              />

              <div className="results-area">
                {loadingSwap && (
                  <div className="loading-state">
                    <span className="spinner" /> Finding swap options…
                  </div>
                )}
                {swapError && (
                  <div className="error-state">{swapError}</div>
                )}
                {swapResult && !loadingSwap && (
                  <SwapResults
                    response={swapResult}
                    onClose={() => { setSwapResult(null); setSelectedDate(null); }}
                  />
                )}
                {!loadingSwap && !swapResult && !swapError && (
                  <div className="empty-hint">
                    Select a shift on the calendar to find swap options.
                  </div>
                )}
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
