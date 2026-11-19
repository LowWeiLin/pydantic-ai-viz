import { describe, expect, test } from "vitest";
import { templateParts } from "./source";

const holes = (text: string): string[] =>
  templateParts(text)
    .filter((part) => part.hole)
    .map((part) => part.text);

describe("a template in the syntax of str.format", () => {
  test("placeholders are holes, kept with their braces", () => {
    expect(templateParts("Today is {date}. Hi {user.name!r}.")).toEqual([
      { hole: false, text: "Today is " },
      { hole: true, text: "{date}" },
      { hole: false, text: ". Hi " },
      { hole: true, text: "{user.name!r}" },
      { hole: false, text: "." },
    ]);
  });

  test("a doubled brace is one literal brace, never a hole", () => {
    expect(templateParts('Reply as {{"answer": {answer}}}.')).toEqual([
      { hole: false, text: 'Reply as {"answer": ' },
      { hole: true, text: "{answer}" },
      { hole: false, text: "}." },
    ]);
    expect(holes("{{literal}}")).toEqual([]);
  });

  test("a placeholder may hold braces of its own", () => {
    expect(holes("Use {total:{...}.2f} and { {'a': x}['a']} here")).toEqual([
      "{total:{...}.2f}",
      "{ {'a': x}['a']}",
    ]);
  });

  test("a brace that is never closed is text", () => {
    expect(templateParts("broken {here")).toEqual([{ hole: false, text: "broken {here" }]);
    expect(templateParts("")).toEqual([]);
  });
});
