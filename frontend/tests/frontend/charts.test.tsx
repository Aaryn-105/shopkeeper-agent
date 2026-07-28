import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Sparkline } from "../../src/components/charts/Sparkline";
import { BarChart } from "../../src/components/charts/BarChart";
import { Gauge } from "../../src/components/charts/Gauge";

describe("Sparkline", () => {
  it("renders a placeholder when values are empty", () => {
    const { container } = render(<Sparkline values={[]} />);
    expect(container.querySelector("svg")).toBeTruthy();
    expect(container.querySelector("text")?.textContent).toMatch(/暂无数据/);
  });

  it("renders a path with one M + (N-1) L commands for N values", () => {
    const { container } = render(<Sparkline values={[1, 3, 2, 5, 4]} />);
    const paths = container.querySelectorAll("path[stroke]");
    expect(paths.length).toBeGreaterThanOrEqual(2);
    // The line path is the one whose stroke matches the prop (not "none")
    const linePath = Array.from(paths).find(
      (p) => p.getAttribute("stroke") !== "none",
    );
    expect(linePath).toBeTruthy();
    const d = linePath?.getAttribute("d") ?? "";
    expect(d.split("L").length).toBe(5); // 1 M + 4 L
  });

  it("shows dots when showDots=true", () => {
    const { container } = render(
      <Sparkline values={[1, 2, 3]} showDots />,
    );
    expect(container.querySelectorAll("circle").length).toBe(3);
  });

  it("clamps extreme values without throwing", () => {
    const { container } = render(<Sparkline values={[1e10, -1e10]} />);
    expect(container.querySelector("path")).toBeTruthy();
  });
});

describe("BarChart", () => {
  it("renders a placeholder when data is empty", () => {
    const { container } = render(<BarChart data={[]} />);
    expect(container.textContent).toMatch(/暂无数据/);
  });

  it("renders one row per data item with text label", () => {
    const { container } = render(
      <BarChart
        data={[
          { label: "alpha", value: 10 },
          { label: "beta", value: 50 },
          { label: "gamma", value: 25 },
        ]}
      />,
    );
    expect(container.querySelectorAll("rect").length).toBe(3);
    expect(container.textContent).toContain("alpha");
    expect(container.textContent).toContain("50");
  });

  it("uses the longest bar's value as 100% reference", () => {
    const { container } = render(
      <BarChart
        data={[
          { label: "small", value: 5 },
          { label: "big", value: 100 },
        ]}
      />,
    );
    const widths = Array.from(container.querySelectorAll("rect")).map(
      (r) => Number(r.getAttribute("width") ?? 0),
    );
    // The big bar should be ~2.5x to ~5x the small one (depending on area size)
    expect(widths[1]).toBeGreaterThan(widths[0]);
  });
});

describe("Gauge", () => {
  it("renders a percentage label that matches the value", () => {
    const { container } = render(<Gauge value={0.5} />);
    expect(container.textContent).toContain("50.0%");
  });

  it("clamps >1 and <0 values", () => {
    const { container } = render(<Gauge value={2.0} />);
    expect(container.textContent).toContain("100.0%");
    const { container: c2 } = render(<Gauge value={-0.5} />);
    expect(c2.textContent).toContain("0.0%");
  });

  it("uses the good tone color when value is high", () => {
    const { container } = render(<Gauge value={0.95} tone="good" />);
    // The colored stroke path should have the good color (#10b981)
    const paths = Array.from(container.querySelectorAll("path"));
    const colored = paths.find((p) => p.getAttribute("stroke") === "#10b981");
    expect(colored).toBeTruthy();
  });
});