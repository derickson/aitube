import { useEffect, useRef, useState } from "react";
import type { Agent, ContentItem } from "../api/client";
import { chatStreamUrl, listAgents } from "../api/client";

interface ChatPanelProps {
  item: ContentItem;
  onSeek?: (time: number) => void;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

function parseTimestamp(ts: string): number {
  const parts = ts.split(":").map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return parts[0] * 60 + parts[1];
}

function renderInlineHtml(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong class="chat-bold">$1</strong>')
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")
    .replace(/_(.+?)_/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function renderMessageContent(
  content: string,
  onSeek?: (time: number) => void,
) {
  // Split on timestamps and bullet markers
  const parts = content.split(/(\[\d{1,2}:\d{2}(?::\d{2})?\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[(\d{1,2}:\d{2}(?::\d{2})?)\]$/);
    if (match && onSeek) {
      const seconds = parseTimestamp(match[1]);
      return (
        <span
          key={i}
          className="chat-timestamp-link"
          onClick={() => onSeek(seconds)}
        >
          [{match[1]}]
        </span>
      );
    }
    return (
      <span
        key={i}
        dangerouslySetInnerHTML={{ __html: renderInlineHtml(part) }}
      />
    );
  });
}

export function ChatPanel({ item, onSeek }: ChatPanelProps) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("default");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    listAgents().then(setAgents).catch(console.error);
  }, []);

  // Reset chat when content item changes
  useEffect(() => {
    setMessages([]);
    setInput("");
    setStreaming(false);
    abortRef.current?.abort();
  }, [item.id]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: ChatMessage = { role: "user", content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(chatStreamUrl(item.id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: newMessages.map((m) => ({ role: m.role, content: m.content })),
          agent_id: selectedAgent,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        throw new Error(`Chat error: ${res.status}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let assistantContent = "";
      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.done) break;
            if (data.error) {
              assistantContent += `\n\nError: ${data.error}`;
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: "assistant", content: assistantContent };
                return updated;
              });
              break;
            }
            if (data.token) {
              assistantContent += data.token;
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: "assistant", content: assistantContent };
                return updated;
              });
            }
          } catch {
            // skip malformed lines
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setMessages((prev) => [
          ...prev,
          ...(prev[prev.length - 1]?.role === "assistant" ? [] : [{ role: "assistant" as const, content: "" }]),
        ]);
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            role: "assistant",
            content: `Error: ${(e as Error).message}`,
          };
          return updated;
        });
      }
    } finally {
      setStreaming(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-agent-bar">
        <label htmlFor="agent-select">Agent:</label>
        <select
          id="agent-select"
          className="chat-agent-select"
          value={selectedAgent}
          onChange={(e) => setSelectedAgent(e.target.value)}
          disabled={streaming}
        >
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        {messages.length > 0 && (
          <button
            className="btn chat-clear-btn"
            onClick={() => { setMessages([]); abortRef.current?.abort(); setStreaming(false); }}
            disabled={streaming}
          >
            Clear Chat
          </button>
        )}
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            Ask a question about this {item.type === "video" ? "video" : item.type === "podcast_episode" ? "podcast" : "article"}.
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`chat-message chat-message-${msg.role}`}>
            <div className="chat-message-bubble">
              {msg.role === "assistant"
                ? renderMessageContent(msg.content, onSeek)
                : msg.content}
              {streaming && i === messages.length - 1 && msg.role === "assistant" && (
                <span className="chat-cursor" />
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-bar">
        <textarea
          className="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about this content..."
          rows={1}
          disabled={streaming}
        />
        <button
          className="btn btn-primary chat-send-btn"
          onClick={sendMessage}
          disabled={streaming || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
