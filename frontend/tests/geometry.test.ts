import { expect, it } from "vitest";
import { clampBox } from "../src/domain/geometry";
import { statusTone, jobLabels } from "../src/domain/status";
it("Keeps dragged and resized boxes in original image boundaries", () => {
  expect(clampBox(-5, -5, 200, 200, 100, 80)).toEqual([0, 0, 100, 80]);
  expect(clampBox(120, 100, 0, 0, 100, 80)).toEqual([99, 79, 100, 80]);
  expect(clampBox(10, 20, 30, 40, 100, 80)).toEqual([10, 20, 40, 60]);
});
it("Distinguishes completed, pending and failed tasks", () => {
  expect(statusTone("accepted")).toBe("success");
  expect(statusTone("failed")).toBe("danger");
  expect(statusTone("submitted")).toBe("pending");
  expect(jobLabels.cancel_requested).toBe("正在取消");
});
