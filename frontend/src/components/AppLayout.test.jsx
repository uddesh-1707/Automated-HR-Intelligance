import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import AppLayout from "./AppLayout";

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock("../services/api", () => ({ apiRequest: mocks.apiRequest }));
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ user: { email: "hr@example.com", role: "admin" }, logout: vi.fn() })
}));

describe("AppLayout recruitment assistant launcher", () => {
  it("provides job context to the assistant and closes the chat", async () => {
    mocks.apiRequest
      .mockResolvedValueOnce([{ id: "job-1", title: "Frontend Developer", status: "published" }])
      .mockResolvedValueOnce({ message: "I found the applications.", tool_calls: [] });
    render(<MemoryRouter><AppLayout /></MemoryRouter>);

    expect(screen.getByRole("button", { name: "Open recruitment assistant" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "Recruitment assistant chat" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open recruitment assistant" }));
    const assistant = screen.getByRole("region", { name: "Recruitment assistant chat" });
    expect(assistant).toBeInTheDocument();
    expect(assistant).toHaveClass("assistant-floating-panel");
    fireEvent.change(await screen.findByLabelText("Assistant job filter"), { target: { value: "job-1" } });
    fireEvent.change(screen.getByLabelText("Ask recruitment assistant"), { target: { value: "Rank applications by CGPA" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(mocks.apiRequest).toHaveBeenNthCalledWith(2, "/recruitment/assistant", {
      method: "POST",
      body: { message: "Rank applications by CGPA", history: [], job_id: "job-1" }
    }));

    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    expect(screen.queryByRole("region", { name: "Recruitment assistant chat" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open recruitment assistant" })).toHaveAttribute("aria-expanded", "false");
  });
});
