const BASE = "/api";

export type ShiftType = "Day" | "Swing" | "Night" | "Unknown";
export type SeniorityLevel = "Sr" | "Jr" | "Unknown";

export interface Resident {
  id: number;
  name: string;
  level: SeniorityLevel;
}

export interface ShiftAssignment {
  work_date: string; // ISO date
  shift_name: string;
  shift_type: ShiftType;
  seniority: SeniorityLevel;
}

export interface MutualDetail {
  requester_covers_date: string;
  requester_covers_shift_name: string;
  requester_covers_shift_type: ShiftType;
}

export interface SwapOption {
  type: "mutual" | "one_sided";
  coverer: Resident;
  covered_shift_name: string;
  covered_shift_type: ShiftType;
  covered_seniority: SeniorityLevel;
  mutual?: MutualDetail;
}

export interface SwapResponse {
  requester: Resident;
  request_date: string;
  request_shift_name: string;
  mutual_options: SwapOption[];
  one_sided_options: SwapOption[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? res.statusText);
  }
  return res.json();
}

export const api = {
  getResidents: () => request<Resident[]>("/residents"),

  getSchedule: (residentId: number, startDate: string, endDate: string) =>
    request<ShiftAssignment[]>(
      `/residents/${residentId}/schedule?start_date=${startDate}&end_date=${endDate}`
    ),

  getSwapOptions: (residentId: number, requestDate: string) =>
    request<SwapResponse>("/swap-options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resident_id: residentId, request_date: requestDate }),
    }),

  uploadCSV: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ residents: number; inserted: number; updated: number }>(
      "/upload-csv",
      { method: "POST", body: form }
    );
  },

  syncQGenda: (startDate: string, endDate: string) =>
    request<{ residents: number; inserted: number; updated: number }>(
      `/sync-qgenda?start_date=${startDate}&end_date=${endDate}`,
      { method: "POST" }
    ),
};
