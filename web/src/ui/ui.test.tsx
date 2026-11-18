import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";
import { Chip, StatusDot } from "./Badge";
import { CopyButton } from "./CopyButton";
import { EmptyState, Redacted, Snippet } from "./EmptyState";
import { JsonTree } from "./JsonTree";
import { MiddleTruncate } from "./MiddleTruncate";
import { Dialog, Tabs } from "./overlays";
import { SchemaTable, schemaRows, schemaType } from "./SchemaTable";
import { statusMeta } from "./tone";
import { useStickToBottom } from "./useStickToBottom";

describe("StatusDot and Chip", () => {
  test("every status has a distinct icon and a word, not just a colour", () => {
    const icons = new Set(Object.values(statusMeta).map((m) => m.icon));
    const labels = new Set(Object.values(statusMeta).map((m) => m.label));
    expect(icons.size).toBe(Object.keys(statusMeta).length);
    expect(labels.size).toBe(Object.keys(statusMeta).length);
  });

  test("the word is in the accessibility tree even when not printed", () => {
    const { rerender } = render(<StatusDot status="retry" />);
    expect(screen.getByText("Retried")).toHaveClass("sr-only");
    rerender(<StatusDot status="error" showLabel label="Failed after 2 tries" />);
    expect(screen.getByText("Failed after 2 tries")).not.toHaveClass("sr-only");
  });

  test("a chip carries its tone and can pulse", () => {
    render(
      <Chip tone="model" mono pulse>
        scripted-support
      </Chip>,
    );
    const chip = screen.getByText("scripted-support").parentElement;
    expect(chip).toHaveClass("tone-model", "tone-soft", "pulse-ring", "font-mono");
  });
});

describe("MiddleTruncate", () => {
  const NAME = "lookup_customer_records_in_the_legacy_billing_system_3";

  test("a long name keeps its end apart from the part that may be clipped", () => {
    const { container } = render(<MiddleTruncate text={NAME} />);
    const [whole, head, tail] = Array.from(container.querySelectorAll("span > span"));
    expect(head).toHaveClass("truncate");
    expect(tail).toHaveTextContent("_system_3");
    expect(tail).toHaveClass("shrink-0");
    expect(tail).not.toHaveClass("truncate");
    expect(`${head?.textContent}${tail?.textContent}`).toBe(NAME);
    // Assistive technology gets the name once, in one piece, not as two words.
    expect(whole).toHaveTextContent(NAME);
    expect(whole).toHaveClass("sr-only");
    expect(head).toHaveAttribute("aria-hidden", "true");
    expect(tail).toHaveAttribute("aria-hidden", "true");
  });

  test("the name of a button that holds a long name is that name", () => {
    render(
      <button type="button">
        <MiddleTruncate text={NAME} />
      </button>,
    );
    expect(screen.getByRole("button", { name: NAME })).toBeInTheDocument();
  });

  test("a short name is left in one piece", () => {
    const { container } = render(<MiddleTruncate text="get_order" className="font-mono" />);
    expect(container.querySelectorAll("span")).toHaveLength(1);
    expect(screen.getByText("get_order")).toHaveClass("truncate", "font-mono");
  });
});

describe("JsonTree", () => {
  const value = {
    order_id: "A-1042",
    total_eur: 42,
    delivered: true,
    notes: null,
    items: [{ sku: "LUMEN-11", html: "<img src=x onerror=alert(1)>" }],
  };

  test("renders values as text, including markup-looking strings", () => {
    const { container } = render(<JsonTree value={value} openDepth={3} />);
    expect(screen.getByText("A-1042")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("null")).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  test("levels below openDepth start collapsed and toggle", async () => {
    const user = userEvent.setup();
    render(<JsonTree value={value} openDepth={1} />);
    expect(screen.queryByText("LUMEN-11")).not.toBeInTheDocument();
    const items = screen.getByRole("button", { name: /items:\s*\[\s*1 item\s*\]/ });
    expect(items).toHaveAttribute("aria-expanded", "false");
    await user.click(items);
    await user.click(screen.getByRole("button", { name: /0:\s*\{\s*2 keys\s*\}/ }));
    expect(screen.getByText("LUMEN-11")).toBeInTheDocument();
  });

  test("long strings fold and unfold", async () => {
    const user = userEvent.setup();
    const long = "x".repeat(400);
    render(<JsonTree value={long} />);
    expect(screen.queryByText(long)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show all 400 characters" }));
    expect(screen.getByText(long)).toBeInTheDocument();
  });

  test("copies pretty JSON, and a bare string without quotes", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const { unmount } = render(<JsonTree value={{ a: [1] }} label="arguments" />);
    await user.click(screen.getByRole("button", { name: "Copy arguments" }));
    expect(writeText).toHaveBeenLastCalledWith('{\n  "a": [\n    1\n  ]\n}');
    expect(screen.getByRole("status")).toHaveTextContent("Copied");
    unmount();
    render(<JsonTree value="plain text" label="result" />);
    await user.click(screen.getByRole("button", { name: "Copy result" }));
    expect(writeText).toHaveBeenLastCalledWith("plain text");
  });

  test("empty containers and scalars render without a toggle", () => {
    render(<JsonTree value={{ list: [], map: {} }} />);
    expect(screen.getByText("[]")).toBeInTheDocument();
    expect(screen.getByText("{}")).toBeInTheDocument();
  });
});

describe("schemaRows", () => {
  const schema = {
    type: "object",
    $defs: {
      Address: {
        type: "object",
        title: "Address",
        properties: { city: { type: "string" }, zip: { type: "string", pattern: "^\\d{5}$" } },
        required: ["city"],
      },
    },
    properties: {
      order_id: { type: "string", description: "Order id, for example A-1042." },
      limit: { type: "integer", default: 3, minimum: 1, maximum: 10 },
      status: { enum: ["open", "closed"] },
      tags: { type: "array", items: { type: "string" } },
      amount: { anyOf: [{ type: "number" }, { type: "null" }], default: null },
      address: { $ref: "#/$defs/Address", description: "Where to ship." },
      lines: {
        type: "array",
        items: { type: "object", properties: { sku: { type: "string" } }, required: ["sku"] },
      },
    },
    required: ["order_id", "address"],
  };

  test("flattens fields with types, required flags, notes and nested objects", () => {
    const rows = schemaRows(schema) ?? [];
    expect(rows.map((r) => [r.path, r.type, r.required])).toEqual([
      ["order_id", "string", true],
      ["limit", "integer", false],
      ["status", '"open" | "closed"', false],
      ["tags", "string[]", false],
      ["amount", "number | null", false],
      ["address", "Address", true],
      ["address.city", "string", true],
      ["address.zip", "string", false],
      ["lines", "object[]", false],
      ["lines[].sku", "string", true],
    ]);
    expect(rows[1]?.notes).toEqual(["default 3", "min 1", "max 10"]);
    expect(rows[5]?.description).toBe("Where to ship.");
    expect(rows[7]?.notes).toEqual(["pattern ^\\d{5}$"]);
    expect(rows[6]?.depth).toBe(1);
  });

  test("a recursive schema terminates", () => {
    const tree = {
      $defs: {
        Node: {
          type: "object",
          properties: { children: { type: "array", items: { $ref: "#/$defs/Node" } } },
        },
      },
      $ref: "#/$defs/Node",
    };
    expect(schemaRows(tree)?.map((r) => r.path)).toEqual(["children"]);
  });

  test("schemas without named fields give null and a type expression", () => {
    expect(schemaRows({ type: "string" })).toBeNull();
    expect(schemaRows(null)).toBeNull();
    expect(schemaRows({ type: "array", items: { type: "object", properties: {} } })).toBeNull();
    expect(
      schemaType({ type: "array", items: { anyOf: [{ type: "string" }, { type: "null" }] } }, {}),
    ).toBe("(string | null)[]");
    expect(schemaType({ const: "x" }, {})).toBe('"x"');
    expect(schemaType({}, {})).toBe("any");
  });

  test("the table marks required and optional in words", () => {
    render(<SchemaTable schema={schema} caption="Parameters of get_order" />);
    const table = screen.getByRole("table", { name: "Parameters of get_order" });
    const row = within(table).getByRole("row", { name: /order_id/ });
    expect(within(row).getByText("required")).toBeInTheDocument();
    expect(
      within(within(table).getByRole("row", { name: /limit/ })).getByText("optional"),
    ).toBeInTheDocument();
    expect(within(table).getByText("default 3 · min 1 · max 10")).toBeInTheDocument();
  });

  test("the table falls back to a sentence for field-less schemas", () => {
    const { rerender } = render(<SchemaTable schema={{ type: "string" }} caption="Output" />);
    expect(screen.getByText("string")).toBeInTheDocument();
    rerender(<SchemaTable schema={{ type: "object", properties: {} }} caption="Parameters" />);
    expect(screen.getByText("Parameters: no fields.")).toBeInTheDocument();
  });
});

describe("CopyButton, EmptyState, Tabs, Dialog", () => {
  test("copy falls back when the clipboard API is refused, and reports failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    document.execCommand = vi.fn(() => true);
    render(<CopyButton text="pydantic-ai-viz demo" label="Copy command" />);
    await user.click(screen.getByRole("button", { name: "Copy command" }));
    expect(document.execCommand).toHaveBeenCalledWith("copy");
    expect(screen.getByRole("status")).toHaveTextContent("Copied");

    document.execCommand = vi.fn(() => false);
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("status")).toHaveTextContent("Copy failed");
  });

  test("an empty state has a heading, a reason and actions", () => {
    render(
      <EmptyState icon="live" title="Waiting for a run" actions={<button type="button">Go</button>}>
        <p>Run your agent and it shows up here.</p>
        <Snippet code="pydantic-ai-viz demo" label="Copy command" />
        <Redacted what="Arguments" />
      </EmptyState>,
    );
    expect(screen.getByRole("heading", { name: "Waiting for a run" })).toBeInTheDocument();
    expect(screen.getByText("pydantic-ai-viz demo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy command" })).toBeInTheDocument();
    expect(screen.getByText(/Content capture is off/)).toHaveTextContent(
      "Arguments not captured. Content capture is off (include_content=False).",
    );
  });

  test("tabs switch panels with the keyboard", async () => {
    const user = userEvent.setup();
    render(
      <Tabs
        label="Tool details"
        items={[
          { id: "args", label: "Arguments", content: <p>the arguments</p> },
          { id: "result", label: "Result", content: <p>the result</p> },
        ]}
      />,
    );
    expect(screen.getByRole("tablist", { name: "Tool details" })).toBeInTheDocument();
    expect(screen.getByText("the arguments")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Arguments" }));
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("the result")).toBeInTheDocument();
  });

  test("no tab sticks out of the strip: it scrolls sideways only", () => {
    // The strip scrolls when the tabs do not fit, so anything taller than it would give it a
    // vertical scrollbar. A tab that reached below the strip to cover its border did that.
    render(<Tabs label="Span details" items={[{ id: "a", label: "A", content: null }]} />);
    const strip = screen.getByRole("tablist");
    expect(strip.className).not.toMatch(/border-b/);
    for (const tab of within(strip).getAllByRole("tab")) {
      expect(tab.className).not.toMatch(/(^|\s)-m[by]?-/);
    }
  });

  test("a dialog is labelled and closes from its button", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <Dialog open onOpenChange={onOpenChange} title="Keyboard shortcuts">
        <p>body</p>
      </Dialog>,
    );
    expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  test("closing a dialog puts the focus back where it was", async () => {
    const user = userEvent.setup();
    render(<Opener />);
    const opener = screen.getByRole("button", { name: "Show shortcuts" });
    await user.click(opener);
    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    // Not on <body>: a keyboard user would be sent back to the top of the page.
    expect(opener).toHaveFocus();
  });
});

/** A dialog opened by a button that is not a Radix trigger, like the one in the top bar. */
function Opener() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Show shortcuts
      </button>
      <Dialog open={open} onOpenChange={setOpen} title="Keyboard shortcuts">
        <p>body</p>
      </Dialog>
    </>
  );
}

/** A scroll area that follows its newest line. */
function Log({ lines, active = true }: { lines: number; active?: boolean }) {
  const ref = useStickToBottom<HTMLDivElement>(active, lines);
  return <div ref={ref} data-testid="log" />;
}

describe("useStickToBottom", () => {
  /** jsdom lays nothing out, so the sizes are given: 100 px high, 20 px per line. */
  function measured(lines: number): HTMLElement {
    const log = screen.getByTestId("log");
    Object.defineProperty(log, "clientHeight", { configurable: true, value: 100 });
    Object.defineProperty(log, "scrollHeight", { configurable: true, value: lines * 20 });
    return log;
  }

  const scrollTo = (log: HTMLElement, top: number): void => {
    log.scrollTop = top;
    fireEvent.scroll(log);
  };

  test("follows new content until the user scrolls away, and again once they are back", () => {
    const { rerender } = render(<Log lines={10} />);
    let log = measured(20);
    rerender(<Log lines={20} />);
    expect(log.scrollTop).toBe(400); // at the bottom: scrollTop is clamped by a real browser

    // Reading further up: more content arrives and the position stays put.
    scrollTo(log, 120);
    log = measured(30);
    rerender(<Log lines={30} />);
    expect(log.scrollTop).toBe(120);

    // Back at the bottom (within a row of it): following resumes.
    scrollTo(log, 600 - 100 - 10);
    log = measured(40);
    rerender(<Log lines={40} />);
    expect(log.scrollTop).toBe(800);
  });

  test("turning the wheel up lets go at once, however close to the bottom that still is", () => {
    // Text that streams in many times a second would otherwise pull a slow scroll back down
    // before it had got a row away.
    const { rerender } = render(<Log lines={10} />);
    let log = measured(20);
    rerender(<Log lines={20} />);
    scrollTo(log, 300); // where a browser puts "the bottom" of 400 px in a 100 px area
    fireEvent.wheel(log, { deltaY: -5 });
    scrollTo(log, 295);
    log = measured(21);
    rerender(<Log lines={21} />);
    expect(log.scrollTop).toBe(295);
  });

  test("a few pixels lost when content settles do not count as scrolling away", () => {
    const { rerender } = render(<Log lines={10} />);
    let log = measured(20);
    rerender(<Log lines={20} />);
    scrollTo(log, 300);
    scrollTo(log, 298); // the area shrank by 2 px under us: nobody scrolled
    log = measured(30);
    rerender(<Log lines={30} />);
    expect(log.scrollTop).toBe(600);
  });

  test("does nothing while inactive", () => {
    const { rerender } = render(<Log lines={10} active={false} />);
    const log = measured(20);
    rerender(<Log lines={20} active={false} />);
    expect(log.scrollTop).toBe(0);
  });
});
