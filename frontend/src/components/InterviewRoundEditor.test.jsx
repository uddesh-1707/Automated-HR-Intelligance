import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import InterviewRoundEditor from "./InterviewRoundEditor";

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("../services/api", () => ({ apiRequest: mocks.apiRequest }));

const job = rounds => ({ id: "job-1", title: "Backend Engineer", interview_rounds: rounds });
const saved = (id, order, name, extra = {}) => ({
  round_id: id, name, order, duration_minutes: 60, interview_type: "technical", required_interviewers: ["lead@example.com"],
  requires_hr: false, prerequisite_round_ids: [], location: null, working_day_start_hour: 9, working_day_end_hour: 17,
  slot_interval_minutes: 30, ...extra
});

function setup(jobs = [job([])]) {
  const onSaved = vi.fn();
  const view = render(<InterviewRoundEditor jobs={jobs} onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText("Job"), { target: { value: "job-1" } });
  return { ...view, onSaved };
}

const cards = () => [...document.querySelectorAll("article.round-config-card")];
const field = (card, label) => within(card).getByLabelText(new RegExp(`^${label}`));

describe("InterviewRoundEditor keeps what the user typed", () => {
  beforeEach(() => { cleanup(); sessionStorage.clear(); vi.resetAllMocks(); vi.spyOn(window, "confirm").mockReturnValue(true); });

  it("keeps Round 1 intact when adding Round 2 and Round 3", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: /Add round/ }));
    let [first] = cards();
    fireEvent.change(field(first, "Round name"), { target: { value: "Technical Interview 1" } });
    fireEvent.change(field(first, "Duration"), { target: { value: "45" } });
    fireEvent.change(field(first, "Required interviewer emails"), { target: { value: "a@x.com, b@x.com" } });
    fireEvent.click(within(first).getByLabelText(/HR attendance required/));

    fireEvent.click(screen.getByRole("button", { name: /Add round/ }));
    let [one, two] = cards();
    expect(field(one, "Round name")).toHaveValue("Technical Interview 1");
    expect(field(one, "Duration")).toHaveValue(45);
    expect(field(one, "Required interviewer emails")).toHaveValue("a@x.com, b@x.com");
    expect(within(one).getByLabelText(/HR attendance required/)).toBeChecked();
    expect(field(two, "Round name")).toHaveValue("");
    expect(field(two, "Order")).toHaveValue(2);

    fireEvent.change(field(two, "Round name"), { target: { value: "Technical Interview 2" } });
    fireEvent.click(within(two).getByLabelText("Technical Interview 1"));
    fireEvent.click(screen.getByRole("button", { name: /Add round/ }));
    [one, two] = cards();
    const three = cards()[2];
    expect(field(one, "Round name")).toHaveValue("Technical Interview 1");
    expect(field(two, "Round name")).toHaveValue("Technical Interview 2");
    expect(within(two).getByLabelText("Technical Interview 1")).toBeChecked();
    expect(field(three, "Round name")).toHaveValue("");
    expect(field(three, "Order")).toHaveValue(3);
  });

  it("does not reset the form when the parent refreshes the jobs list", () => {
    const rounds = [saved("r1", 1, "Technical Interview 1")];
    const { rerender, onSaved } = setup([job(rounds)]);
    fireEvent.click(screen.getByRole("button", { name: /Add round/ }));
    const [, added] = cards();
    fireEvent.change(field(added, "Round name"), { target: { value: "Half typed" } });
    fireEvent.change(field(cards()[0], "Duration"), { target: { value: "90" } });

    // Same saved content, brand-new array/object identities (what a periodic reload produces).
    rerender(<InterviewRoundEditor jobs={[job(rounds.map(item => ({ ...item })))]} onSaved={onSaved} />);
    rerender(<InterviewRoundEditor jobs={[job(rounds.map(item => ({ ...item })))]} onSaved={onSaved} />);

    expect(cards()).toHaveLength(2);
    expect(field(cards()[1], "Round name")).toHaveValue("Half typed");
    expect(field(cards()[0], "Duration")).toHaveValue(90);
  });

  it("editing one round leaves the others untouched", () => {
    setup([job([saved("r1", 1, "Technical Interview 1"), saved("r2", 2, "Technical Interview 2", { prerequisite_round_ids: ["r1"], duration_minutes: 30 })])]);
    fireEvent.change(field(cards()[0], "Round name"), { target: { value: "Screening" } });
    expect(field(cards()[1], "Round name")).toHaveValue("Technical Interview 2");
    expect(field(cards()[1], "Duration")).toHaveValue(30);
    expect(within(cards()[1]).getByLabelText("Screening")).toBeChecked();
  });

  it("keeps all input when the backend rejects the save, then persists and restores after a successful save", async () => {
    const { rerender, onSaved } = setup();
    fireEvent.click(screen.getByRole("button", { name: /Add round/ }));
    fireEvent.change(field(cards()[0], "Round name"), { target: { value: "Technical Interview 1" } });
    fireEvent.change(field(cards()[0], "Required interviewer emails"), { target: { value: "bad-email" } });

    mocks.apiRequest.mockRejectedValueOnce(new Error("value is not a valid email address"));
    fireEvent.click(screen.getByRole("button", { name: "Save round configuration" }));
    expect(await screen.findByText(/not a valid email/)).toBeInTheDocument();
    expect(field(cards()[0], "Round name")).toHaveValue("Technical Interview 1");
    expect(field(cards()[0], "Required interviewer emails")).toHaveValue("bad-email");

    fireEvent.change(field(cards()[0], "Required interviewer emails"), { target: { value: "lead@example.com" } });
    const persisted = saved("r-new", 1, "Technical Interview 1");
    mocks.apiRequest.mockImplementationOnce(async (_path, options) => ({ rounds: options.body.rounds.map(item => ({ ...persisted, round_id: item.round_id })) }));
    fireEvent.click(screen.getByRole("button", { name: "Save round configuration" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(mocks.apiRequest.mock.calls.at(-1)[1].body.rounds[0]).toMatchObject({ name: "Technical Interview 1", order: 1, duration_minutes: 60, required_interviewers: ["lead@example.com"] });
    expect(await screen.findByText(/Saved 1 interview round/)).toBeInTheDocument();

    // "Refresh the page": a fresh editor built from the saved job shows the persisted round.
    cleanup();
    sessionStorage.clear();
    const [, savedRounds] = [null, onSaved.mock.calls[0][1]];
    render(<InterviewRoundEditor jobs={[job(savedRounds)]} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Job"), { target: { value: "job-1" } });
    expect(field(cards()[0], "Round name")).toHaveValue("Technical Interview 1");
    expect(field(cards()[0], "Required interviewer emails")).toHaveValue("lead@example.com");
  });

  it("restores an unsaved draft after the editor is remounted, and Discard clears it", () => {
    const first = setup();
    fireEvent.click(screen.getByRole("button", { name: /Add round/ }));
    fireEvent.change(field(cards()[0], "Round name"), { target: { value: "Unsaved draft" } });
    first.unmount();

    render(<InterviewRoundEditor jobs={[job([])]} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Job"), { target: { value: "job-1" } });
    expect(field(cards()[0], "Round name")).toHaveValue("Unsaved draft");

    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(cards()).toHaveLength(0);
  });
});
