import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EvaluationPage from "./EvaluationPage";

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("../services/api", () => ({ apiRequest: mocks.apiRequest }));

const page = outcome => ({
  interviewer_email: "lead@example.com",
  candidate: { name: "Taylor Candidate", email: "t@example.com", phone: null, skills: ["python"], education: null, experience_years: 2 },
  job: { title: "Backend Engineer" },
  round: { name: "Technical Interview 2" },
  interview: { starts_at: "2026-10-21T10:00:00Z", timezone: "UTC", duration_minutes: 60, status: "scheduled" },
  resume_url: "http://api.example/evaluations/tok/resume",
  expires_at: "2026-11-21T10:00:00Z",
  evaluation: { submitted: Boolean(outcome), outcome, feedback: null, can_submit: !outcome, resolves_on_hold: false }
});

function renderPage() {
  render(<MemoryRouter initialEntries={["/evaluate/tok"]}><Routes><Route path="/evaluate/:token" element={<EvaluationPage />} /></Routes></MemoryRouter>);
}

describe("EvaluationPage", () => {
  beforeEach(() => { cleanup(); vi.resetAllMocks(); vi.spyOn(window, "confirm").mockReturnValue(true); });

  it("lets an interviewer review the candidate and submit a final outcome once", async () => {
    mocks.apiRequest
      .mockResolvedValueOnce(page(null))
      .mockResolvedValueOnce({ status: "recorded", outcome: "passed", message: "Recorded as Passed. The candidate is now eligible for the next configured round." })
      .mockResolvedValueOnce(page("passed"));
    renderPage();

    expect(await screen.findByText("Taylor Candidate")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open resume/ })).toHaveAttribute("href", "http://api.example/evaluations/tok/resume");
    fireEvent.click(screen.getByLabelText(/Passed/));
    fireEvent.change(screen.getByLabelText("Feedback"), { target: { value: "Excellent system design answers." } });
    fireEvent.click(screen.getByRole("button", { name: "Submit evaluation" }));

    await waitFor(() => expect(mocks.apiRequest).toHaveBeenNthCalledWith(2, "/evaluations/tok", {
      method: "POST", body: { outcome: "passed", feedback: "Excellent system design answers.", rating: null }
    }));
    expect(await screen.findByText(/eligible for the next configured round/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Submit evaluation" })).not.toBeInTheDocument());
  });

  it("explains expired or revoked links", async () => {
    mocks.apiRequest.mockRejectedValueOnce(new Error("This evaluation link has expired. Ask HR to resend it."));
    renderPage();
    expect(await screen.findByText(/has expired/)).toBeInTheDocument();
  });
});
