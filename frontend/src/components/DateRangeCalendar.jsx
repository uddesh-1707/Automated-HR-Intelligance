import { useState } from "react";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function toDateKey(date) {
  const pad = value => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function fromDateKey(key) {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function addDays(key, days) {
  const date = fromDateKey(key);
  date.setDate(date.getDate() + days);
  return toDateKey(date);
}

export function formatDateKey(key) {
  return fromDateKey(key).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

/**
 * Month calendar for choosing a first and last interview day.
 * `value` is `{ start, end }` as YYYY-MM-DD strings; `end` may be null while the range is being chosen.
 */
export default function DateRangeCalendar({ value, onChange, maxDays = 30 }) {
  const today = toDateKey(new Date());
  const [month, setMonth] = useState(() => {
    const base = value.start ? fromDateKey(value.start) : new Date();
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });
  const lastAllowed = value.start && !value.end ? addDays(value.start, maxDays - 1) : null;

  function pick(key) {
    if (key < today) return;
    if (!value.start || value.end) {
      onChange({ start: key, end: null });
    } else if (key < value.start) {
      onChange({ start: key, end: null });
    } else if (key <= lastAllowed) {
      onChange({ start: value.start, end: key });
    }
  }

  const firstWeekday = (month.getDay() + 6) % 7;
  const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => toDateKey(new Date(month.getFullYear(), month.getMonth(), index + 1)))
  ];
  const rangeEnd = value.end || value.start;

  return (
    <div className="date-calendar" role="group" aria-label="Choose interview dates">
      <div className="date-calendar-head">
        <button type="button" className="button button-quiet button-small" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button>
        <b>{month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</b>
        <button type="button" className="button button-quiet button-small" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button>
      </div>
      <div className="date-calendar-grid">
        {WEEKDAYS.map(day => <span className="date-calendar-weekday" key={day}>{day}</span>)}
        {cells.map((key, index) => {
          if (!key) return <span key={`blank-${index}`} />;
          const disabled = key < today || (lastAllowed && key > lastAllowed);
          const selected = key === value.start || key === value.end;
          const inRange = value.start && rangeEnd && key > value.start && key < rangeEnd;
          return <button
            type="button"
            key={key}
            disabled={disabled}
            aria-pressed={selected}
            aria-label={formatDateKey(key)}
            className={`date-calendar-day${selected ? " selected" : ""}${inRange ? " in-range" : ""}${key === today ? " today" : ""}`}
            onClick={() => pick(key)}
          >{Number(key.slice(8))}</button>;
        })}
      </div>
      <p className="date-calendar-hint">
        {!value.start ? "Click the first interview day." : !value.end ? "Click the last interview day, or generate the plan for just this day." : `Interviews will be placed between ${formatDateKey(value.start)} and ${formatDateKey(value.end)}.`}
      </p>
    </div>
  );
}
