import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CandidateDetailPage from "./CandidateDetailPage";

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock("../services/api", () => ({ apiRequest: mocks.apiRequest }));

const application = {
  id: "app-1",
  stage: "screening",
  applied_at: "2026-10-19T10:00:00Z",
  candidate: {
    id: "candidate-1",
    name: "Taylor Candidate",
    email: "taylor@example.com",
    phone: null,
    skills: [],
    experience_years: null,
    education: null
  },
  job: { id: "job-1", title: "Software Engineer", department: "Engineering", interview_rounds: [] },
  ats_score: null,
  matching_status: "pending",
  resume_filename: "resume.pdf",
  resume_size: 1024,
  resume_content_type: "application/pdf"
};

describe("CandidateDetailPage email lifecycle", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    mocks.apiRequest.mockImplementation(path => {
      if (path === "/recruitment/applications/app-1") return Promise.resolve(application);
      if (path === "/interviews/applications/app-1") return Promise.resolve([]);
      if (path === "/interviews/applications/app-1/progression-overrides") return Promise.resolve([]);
      if (path === "/recruitment/applications/app-1/email-drafts") return Promise.resolve([{
        _id: "email-1",
        recipient: "taylor@example.com",
        subject: "Next steps",
        body: "Hello Taylor",
        email_type: "shortlist",
        approval_status: "approved",
        delivery_status: "failed",
        delivery_failure_type: "explicit_rejection",
        error_message: "Recipient was refused."
      }]);
      if (path === "/recruitment/email-drafts/email-1/retry") {
        return Promise.resolve({
          _id: "email-retry-1",
          retry_of: "email-1",
          recipient: "taylor@example.com",
          subject: "Next steps",
          body: "Hello Taylor",
          email_type: "shortlist",
          approval_status: "approved",
          delivery_status: "not_sent"
        });
      }
      return Promise.resolve(null);
    });
  });

  it("prepares a retry only for an explicit provider rejection and keeps it awaiting HR action", async () => {
    render(
      <MemoryRouter initialEntries={["/recruitment/app-1"]}>
        <Routes><Route path="/recruitment/:applicationId" element={<CandidateDetailPage />} /></Routes>
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole("button", { name: "Prepare safe retry" }));
    expect(await screen.findByRole("button", { name: "Confirm & send retry" })).toBeInTheDocument();
    expect(mocks.apiRequest).toHaveBeenCalledWith("/recruitment/email-drafts/email-1/retry", { method: "POST" });
    await waitFor(() => expect(screen.getByText(/Review and confirm this new attempt/)).toBeInTheDocument());
  });

  it("reopens and saves an existing unsent draft", async () => {
    const draft = {
      _id: "email-1",
      recipient: "taylor@example.com",
      subject: "Next steps",
      body: "Hello Taylor",
      email_type: "shortlist",
      approval_status: "draft",
      delivery_status: "not_sent"
    };
    mocks.apiRequest.mockImplementation(path => {
      if (path === "/recruitment/applications/app-1") return Promise.resolve(application);
      if (path === "/interviews/applications/app-1") return Promise.resolve([]);
      if (path === "/interviews/applications/app-1/progression-overrides") return Promise.resolve([]);
      if (path === "/recruitment/applications/app-1/email-drafts") return Promise.resolve([draft]);
      if (path === "/recruitment/email-drafts/email-1") return Promise.resolve({
        ...draft,
        subject: "Revised next steps",
        body: "Updated message."
      });
      return Promise.resolve(null);
    });

    render(
      <MemoryRouter initialEntries={["/recruitment/app-1"]}>
        <Routes><Route path="/recruitment/:applicationId" element={<CandidateDetailPage />} /></Routes>
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Revised next steps" } });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Updated message." } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(mocks.apiRequest).toHaveBeenCalledWith(
      "/recruitment/email-drafts/email-1",
      { method: "PATCH", body: { subject: "Revised next steps", body: "Updated message." } }
    ));
    expect(await screen.findByText("Email draft saved.")).toBeInTheDocument();
  });
});
