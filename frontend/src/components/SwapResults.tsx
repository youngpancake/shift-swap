import { SwapResponse, SwapOption } from "../api";

interface Props {
  response: SwapResponse;
  onClose: () => void;
}

function formatDate(iso: string) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric",
  });
}

function ShiftBadge({ type }: { type: string }) {
  const cls = type === "Day" ? "badge-day" : type === "Swing" ? "badge-swing" : type === "Night" ? "badge-night" : "badge-unknown";
  return <span className={`shift-badge ${cls}`}>{type}</span>;
}

function OptionCard({ opt }: { opt: SwapOption }) {
  return (
    <div className="option-card">
      <div className="option-card-header">
        <span className="coverer-name">{opt.coverer.name}</span>
        <span className={`level-badge level-${opt.coverer.level.toLowerCase()}`}>{opt.coverer.level}</span>
      </div>

      <div className="option-card-body">
        {opt.type === "mutual" && opt.mutual ? (
          <div className="swap-exchange">
            <div className="swap-side">
              <div className="swap-label">They cover your shift</div>
              <div className="swap-shift">
                <ShiftBadge type={opt.covered_shift_type} />
                <span>{opt.covered_shift_name}</span>
              </div>
            </div>
            <div className="swap-arrow">⇄</div>
            <div className="swap-side">
              <div className="swap-label">You cover {formatDate(opt.mutual.requester_covers_date)}</div>
              <div className="swap-shift">
                <ShiftBadge type={opt.mutual.requester_covers_shift_type} />
                <span>{opt.mutual.requester_covers_shift_name}</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="one-sided-info">
            <ShiftBadge type={opt.covered_shift_type} />
            <span>Covers your <strong>{opt.covered_shift_name}</strong> shift (one-sided)</span>
          </div>
        )}
      </div>
    </div>
  );
}

export function SwapResults({ response, onClose }: Props) {
  const total = response.mutual_options.length + response.one_sided_options.length;

  return (
    <div className="swap-results">
      <div className="results-header">
        <div>
          <h3>Swap options for {formatDate(response.request_date)}</h3>
          <p className="results-meta">
            <span className="shift-name-pill">{response.request_shift_name}</span>
            {total === 0
              ? "No valid swap options found."
              : `${total} valid option${total !== 1 ? "s" : ""} — ${response.mutual_options.length} mutual, ${response.one_sided_options.length} one-sided`}
          </p>
        </div>
        <button className="close-btn" onClick={onClose}>✕</button>
      </div>

      {total === 0 && (
        <div className="empty-state">
          <p>No resident can cover this shift without violating scheduling rules.</p>
          <p>This may be due to consecutive shift limits, missing day-off requirements, or seniority restrictions.</p>
        </div>
      )}

      {response.mutual_options.length > 0 && (
        <section>
          <h4 className="section-heading mutual-heading">
            Mutual swaps <span className="count-badge">{response.mutual_options.length}</span>
          </h4>
          <p className="section-desc">Both residents exchange a shift — preferred.</p>
          <div className="options-list">
            {response.mutual_options.map((opt, i) => (
              <OptionCard key={i} opt={opt} />
            ))}
          </div>
        </section>
      )}

      {response.one_sided_options.length > 0 && (
        <section>
          <h4 className="section-heading one-sided-heading">
            One-sided coverage <span className="count-badge">{response.one_sided_options.length}</span>
          </h4>
          <p className="section-desc">They pick up your shift, nothing in return — fallback only.</p>
          <div className="options-list">
            {response.one_sided_options.map((opt, i) => (
              <OptionCard key={i} opt={opt} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
