import { FormEvent, useEffect, useRef, useState } from "react";
import { Loader2, MessageCircle, Send } from "lucide-react";
import type { ChatTurn } from "./types";

type ChatPanelProps = {
  onSubmit: (question: string, history: ChatTurn[]) => Promise<ChatTurn | null>;
};

export function ChatPanel({ onSubmit }: ChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    listRef.current?.scrollTo?.({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || busy) {
      return;
    }

    setError("");
    setBusy(true);
    setMessages((current) => [...current, { role: "user", content: trimmed }]);
    try {
      const assistantMessage = await onSubmit(trimmed, messages);
      if (assistantMessage) {
        setMessages((current) => [...current, assistantMessage]);
        setQuestion("");
      } else {
        setMessages((current) => current.slice(0, -1));
        setError("回答生成失败，请稍后重试。");
      }
    } catch (caught) {
      setMessages((current) => current.slice(0, -1));
      setError(caught instanceof Error ? caught.message : "回答生成失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="chat-shell">
      <div className="section-head">
        <div>
          <h2>智能问答</h2>
          <p>基于当前账号的文书库检索，并附带引用来源。</p>
        </div>
        <MessageCircle size={22} />
      </div>

      <div className="chat-messages" ref={listRef}>
        {messages.length === 0 ? (
          <div className="chat-empty">
            <strong>向知识库提问</strong>
            <span>例如：谁卖地？交易价格是多少？张三出现在哪些契约里？</span>
          </div>
        ) : (
          messages.map((message, index) => (
            <article key={index} className={`chat-message ${message.role}`}>
              <div className="chat-bubble">
                <strong>{message.role === "user" ? "你" : "文渊智图"}</strong>
                <p>{message.content}</p>
              </div>
              {message.sources?.length ? (
                <div className="chat-sources">
                  {message.sources.map((source) => (
                    <div key={`${source.index}-${source.image_id}`} className="source-card">
                      <strong>
                        来源 {source.index ?? 1} · {source.filename ?? "未知文书"}
                      </strong>
                      <small>
                        {[source.time, source.location, source.seller, source.buyer, source.price]
                          .filter(Boolean)
                          .join(" · ")}
                      </small>
                      {source.excerpt ? <p>{source.excerpt}</p> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          ))
        )}
        {busy ? (
          <div className="chat-message assistant">
            <div className="chat-bubble">
              <strong>文渊智图</strong>
              <p className="chat-loading">
                <Loader2 className="spin" size={15} /> 正在检索文书并生成回答
              </p>
            </div>
          </div>
        ) : null}
      </div>

      {error ? <div className="chat-error">{error}</div> : null}

      <form className="chat-input-bar" onSubmit={submit}>
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="询问文书记载的人物、地点或交易"
          disabled={busy}
        />
        <button className="primary-button" type="submit" disabled={busy || !question.trim()}>
          {busy ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
          发送
        </button>
      </form>
    </section>
  );
}
