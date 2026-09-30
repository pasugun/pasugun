import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Disclaimer } from "./disclaimer";

describe("Disclaimer", () => {
  it("투자 책임 고지를 보여준다", () => {
    render(<Disclaimer />);

    expect(screen.getByText(/투자 판단의 책임은 본인에게 있습니다/)).toBeInTheDocument();
  });
});
