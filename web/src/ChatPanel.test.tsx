import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";

describe("ChatPanel", () => {
  it("submits a question and renders answer with sources", async () => {
    const onSubmit = vi.fn().mockResolvedValue({
      role: "assistant",
      content: "张三是卖方。",
      sources: [
        {
          index: 1,
          image_id: 3,
          filename: "#3 地契",
          time: "道光元年",
          excerpt: "立契人张三",
        },
      ],
    });

    render(<ChatPanel onSubmit={onSubmit} />);

    const input = screen.getByPlaceholderText("询问文书记载的人物、地点或交易");
    fireEvent.change(input, { target: { value: "谁卖地？" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText("张三是卖方。")).toBeInTheDocument();
    });
    expect(onSubmit).toHaveBeenCalledWith("谁卖地？", []);
    expect(screen.getByText("来源 1 · #3 地契")).toBeInTheDocument();
    expect(screen.getByText("立契人张三")).toBeInTheDocument();
  });

  it("shows an error when the chat request fails", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("服务不可用"));

    render(<ChatPanel onSubmit={onSubmit} />);

    fireEvent.change(screen.getByPlaceholderText("询问文书记载的人物、地点或交易"), {
      target: { value: "谁买地？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText("服务不可用")).toBeInTheDocument();
    });
  });
});
