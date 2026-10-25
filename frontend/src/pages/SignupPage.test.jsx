import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SignupPage from "./SignupPage";

const mocks = vi.hoisted(() => ({
  authenticate: vi.fn(),
  navigate: vi.fn()
}));

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ authenticate: mocks.authenticate })
}));

vi.mock("react-router-dom", async importOriginal => {
  const actual = await importOriginal();
  return { ...actual, useNavigate: () => mocks.navigate };
});

describe("SignupPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authenticate.mockResolvedValue(undefined);
  });

  it("shows only email and password and submits recruiter signup", async () => {
    render(
      <MemoryRouter>
        <SignupPage />
      </MemoryRouter>
    );
    expect(screen.getByLabelText("Work email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.queryByLabelText(/invite/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "hr@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => expect(mocks.authenticate).toHaveBeenCalledWith("/auth/signup", {
      email: "hr@example.com",
      password: "correct-horse-battery"
    }));
  });
});
