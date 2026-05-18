import { ShiftAssignment, ShiftType } from "../api";

interface Props {
  year: number;
  month: number; // 1-based
  assignments: ShiftAssignment[];
  selectedDate: string | null;
  onSelectDate: (date: string) => void;
  onPrev: () => void;
  onNext: () => void;
}

const MONTH_NAMES = [
  "January","February","March","April","May","June",
  "July","August","September","October","November","December",
];
const DAY_LABELS = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];

const SHIFT_COLORS: Record<ShiftType, string> = {
  Day: "shift-day",
  Swing: "shift-swing",
  Night: "shift-night",
  Unknown: "shift-unknown",
};

function isoDate(year: number, month: number, day: number) {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function Calendar({ year, month, assignments, selectedDate, onSelectDate, onPrev, onNext }: Props) {
  const workingDays = new Map(assignments.map((a) => [a.work_date, a]));

  const firstDay = new Date(year, month - 1, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(year, month, 0).getDate();

  const cells: (number | null)[] = [
    ...Array(firstDay).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];

  // pad to complete last row
  while (cells.length % 7 !== 0) cells.push(null);

  return (
    <div className="calendar">
      <div className="calendar-header">
        <button className="nav-btn" onClick={onPrev}>‹</button>
        <span className="calendar-title">{MONTH_NAMES[month - 1]} {year}</span>
        <button className="nav-btn" onClick={onNext}>›</button>
      </div>

      <div className="calendar-grid">
        {DAY_LABELS.map((d) => (
          <div key={d} className="cal-label">{d}</div>
        ))}

        {cells.map((day, i) => {
          if (!day) return <div key={`empty-${i}`} className="cal-cell empty" />;

          const iso = isoDate(year, month, day);
          const shift = workingDays.get(iso);
          const isSelected = iso === selectedDate;
          const isWorking = !!shift;

          return (
            <div
              key={iso}
              className={[
                "cal-cell",
                isWorking ? "working" : "off",
                isWorking ? SHIFT_COLORS[shift!.shift_type] : "",
                isSelected ? "selected" : "",
              ].join(" ")}
              onClick={() => isWorking && onSelectDate(iso)}
              title={shift ? shift.shift_name : "Off"}
            >
              <span className="day-num">{day}</span>
              {shift && (
                <span className="shift-tag">{shift.shift_type}</span>
              )}
            </div>
          );
        })}
      </div>

      <div className="calendar-legend">
        <span className="legend-item shift-day">Day</span>
        <span className="legend-item shift-swing">Swing</span>
        <span className="legend-item shift-night">Night</span>
        <span className="legend-item off">Off</span>
      </div>
    </div>
  );
}
