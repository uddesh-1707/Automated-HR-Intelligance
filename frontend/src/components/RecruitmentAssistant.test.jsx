import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RecruitmentAssistant from "./RecruitmentAssistant";

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock("../services/api", () => ({ apiRequest: mocks.apiRequest }));

describe("RecruitmentAssistant", () => {
  beforeEach(() => { cleanup(); vi.resetAllMocks(); });

  it("shows a schedule proposal and books only after explicit confirmation", async () => {
    mocks.apiRequest
      .mockResolvedValueOnce({
        message: "I prepared a plan for HR review.",
        tool_calls: [{
          name: "create_interview_schedule_plan",
          status: "completed",
          result: {
            plan_id: "plan-1",
            status: "awaiting_confirmation",
            job_title: "Backend Engineer",
            round: { name: "Technical Interview" },
            timezone: "UTC",
            window_start: "2026-10-20T10:00:00Z",
            window_end: "2026-10-27T10:00:00Z",
            counts: { eligible: 1, scheduled: 0, awaiting_confirmation: 1, awaiting_evaluation: 0, unable_to_schedule: 0 },
            items: [{ application_id: "app-1", candidate_name: "Taylor", status: "awaiting_confirmation", starts_at: "2026-10-21T10:00:00Z" }]
          }
        }]
      })
      .mockResolvedValueOnce({
        plan_id: "plan-1",
        status: "confirmed",
        counts: { scheduled: 1, awaiting_confirmation: 0 }
      });

    render(<MemoryRouter><RecruitmentAssistant jobs={[]} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Ask recruitment assistant"), {
      target: { value: "Schedule a Technical Interview for Backend Engineer" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/Schedule proposal · Backend Engineer/)).toBeInTheDocument();
    expect(screen.getByText(/Candidate calendar availability is not checked/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm plan and book" }));

    await waitFor(() => expect(mocks.apiRequest).toHaveBeenNthCalledWith(
      2,
      "/interviews/scheduling-plans/plan-1/confirm",
      { method: "POST" }
    ));
    expect(await screen.findByText(/1 eligible · 0 awaiting confirmation/)).toBeInTheDocument();
  });

  it("never claims emails were delivered when they were not, and offers a safe retry", async () => {
    mocks.apiRequest
      .mockResolvedValueOnce({
        message: "Plan ready.",
        tool_calls: [{ name: "create_interview_schedule_plan", status: "completed", result: {
          plan_id: "plan-2", status: "awaiting_confirmation", job_title: "Backend Engineer",
          round: { name: "Technical Interview 2", required_interviewers: ["lead@example.com"], location: "https://meet.example/abc" },
          timezone: "UTC", window_start: "2026-10-20T10:00:00Z", window_end: "2026-10-27T10:00:00Z",
          counts: { eligible: 1, awaiting_confirmation: 1 },
          items: [{ application_id: "app-1", candidate_name: "Taylor", status: "awaiting_confirmation", starts_at: "2026-10-21T10:00:00Z" }]
        } }]
      })
      .mockResolvedValueOnce({ plan_id: "plan-2", status: "confirmed", counts: { scheduled: 1, awaiting_confirmation: 0 }, notifications: { sent: 0, partial: 0, not_configured: 1, failed: 0 } })
      .mockResolvedValueOnce({ plan_id: "plan-2", status: "confirmed", counts: { scheduled: 1 }, notifications: { sent: 1, partial: 0, not_configured: 0, failed: 0 } });

    render(<MemoryRouter><RecruitmentAssistant jobs={[]} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Ask recruitment assistant"), { target: { value: "Schedule tech 2 for Taylor" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/Interviewer: lead@example.com/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm plan and book" }));
    expect(await screen.findByText("Some emails were not delivered")).toBeInTheDocument();
    expect(screen.getByText(/Email is not configured/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry sending emails" }));
    await waitFor(() => expect(mocks.apiRequest).toHaveBeenNthCalledWith(
      3, "/interviews/scheduling-plans/plan-2/resend-notifications", { method: "POST" }
    ));
    expect(await screen.findByText("Emails delivered")).toBeInTheDocument();
  });
});
