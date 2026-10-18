// @vitest-environment node

import { expect, it } from "vitest";
import { manualChunks } from "../vite.config";

it("splits large frontend vendors into chunks", () => {
  expect(manualChunks.echarts).toContain("echarts");
  expect(manualChunks.react).toContain("react");
  expect(manualChunks.react).toContain("react-dom");
});
